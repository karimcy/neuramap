# 013 · Web UI shell & demo flow

**Owner:** Oli
**Module(s):** Cross (thin web app over the pipeline)
**Milestone:** Milestone 8 — UI polish & demo flow
**Status:** v1 built (W3-7b) — `gridq.app` thin FastAPI+Jinja2 shell; site → run → the three artifacts in one view, figures from the record, smoke-tested. Polished/branded layer still M8-Later.

## Why this matters
The pipeline is only credible if it can be driven end-to-end by a person, not just a test runner. Spec §4 and Milestone 8 call for a thin web UI that wraps the existing modules into one demo flow — load SiteModel → run → inspect quote → view memo/proposal → see telemetry — so Olly can show the full system working on a real site. Without it the deterministic core, agentic outputs and telemetry exist but cannot be exercised or demonstrated as a single product surface.

## Requirements
- Wrap the existing pipeline modules (no new domain logic): SiteModel ingestion/validation (008), financial model (006), quote lifecycle (009), telemetry (010), memo (011), proposal (012).
- Provide a single demo flow with these screens/steps:
  - **Load SiteModel** — accept a structured SiteModel JSON (upload or paste/select a fixture); show validation result, data tier (T0/T1/T2) and any gate failures with specific errors, surfaced from ingestion (008).
  - **Run** — trigger the pipeline against the mock OptimisationResult (005); deterministic, reproducible run.
  - **Inspect quote** — display the immutable quote record (009): inputs hash, data tier, assumption version, code version, seed, optimisation result, finance outputs (P50/P90, DSCR, price-to-target), gate/credit status, lifecycle state.
  - **View memo/proposal** — render the generated underwriting memo (011) and customer proposal (012) in-browser.
  - **See telemetry** — show the telemetry record for the run (010): data tier at intake, human-minutes, iterations, outcome, assumption/code versions.
- Implementation framework is a v1 choice (FastAPI/Flask/Streamlit); keep it thin — orchestration and presentation only.
- All numbers shown come from the run record; the UI never computes or authors figures.
- A smoke test proves the app boots and the core route(s) respond.

## Acceptance criteria
- [ ] A valid SiteModel JSON can be loaded through the UI and its data tier and gate status are displayed correctly.
- [ ] An invalid SiteModel is rejected in the UI with the specific, actionable errors surfaced from ingestion (008).
- [ ] Running the pipeline from the UI uses the mock OptimisationResult (005) and produces a quote record without manual code steps.
- [ ] The quote-inspection view shows the run record's provenance fields (inputs hash, assumption version, code version, seed) and finance outputs (P50/P90, DSCR, price-to-target).
- [ ] The memo and proposal render in the UI and the numbers shown match the run record exactly.
- [ ] The telemetry view shows the run's telemetry record (tier, human-minutes, iterations, outcome, versions).
- [ ] The full demo flow (load → run → inspect → memo/proposal → telemetry) completes end-to-end in the web UI.
- [ ] `tests/test_app_smoke.py` confirms the app starts and the primary route(s) return successfully.

## Dependencies
- 006-financial-model-wrapper.md — finance outputs (P50/P90, DSCR, price-to-target) shown in the quote view.
- 008-ingestion-workflow.md — SiteModel load/validate/grade and the errors/tier the UI surfaces.
- 009-quote-lifecycle.md — the immutable quote record the UI inspects and renders from.
- 010-telemetry.md — the per-run telemetry record the UI displays.
- 011-output-memo-generator.md — the underwriting memo rendered in the UI.
- 012-output-proposal-generator.md — the customer proposal rendered in the UI.
- 005-mock-optimisation-contract.md — the mock OptimisationResult the run uses; this issue is buildable entirely on the mock without waiting on the real engine (014).
- Shared contract: SiteModel schema (002) and OptimisationResult schema (005) define the objects flowing through the UI.

## Out of scope (v1)
- Polished, 2026-quality proposal layout, animations or branded customer proposal layer (M8 Later).
- Tighter human-sign / approval workflow beyond viewing the drafts (M8 Later).
- Any AI parsing of arbitrary installer formats in the upload step (M0 Later) — structured SiteModel JSON only.
- Real engine integration (014) or any non-mock optimisation in the run step.
- CRM, fundraise materials, or any external-team surface (§5 exclusions).
- Authentication, multi-user accounts, hosted deployment — v1 is a local developer tool / demo app.

## Suggested files to create or modify
- `gridq/app/app.py` — thin web app wiring the demo flow over the existing pipeline modules (FastAPI/Flask/Streamlit — implementation choice).
- `gridq/app/templates/` — page templates for load / run / quote-inspect / memo+proposal / telemetry views (if a server-rendered framework is chosen).
- `gridq/app/static/` — minimal static assets (styling/scripts) for the demo shell.
- `tests/test_app_smoke.py` — smoke test that the app boots and primary route(s) respond.

> Suggested only — no application code is created by this issue document.
