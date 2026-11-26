---
proposed_by: agent/viz
date: 2026-06-24
status: proposed   # proposed | accepted | rejected
---

## Operation
insert_after   (one edit)

## Target
`## Common failure modes (watch for these)` — insert as a new bullet directly after the
"A UI / demo layer re-running, re-aggregating, re-formatting or charting figures …" bullet proposed
by `agent/frontend` (or, if that one is not yet applied, after the existing outputs/rendering bullet).

## Proposed text
- A chart that needs an **interactive renderer or a series the frozen contract doesn't carry** (a 3D
  surface, a half-hourly dispatch) → **(a) vendor the renderer, never a CDN.** Bundle the library
  (e.g. a pinned `plotly.min.js`) under `/static` so the view renders **offline and under a strict
  CSP** and stays byte-deterministic; keep `/static` un-gated; feed it the same **JSON island of raw
  record values (pixel geometry) + pre-formatted label strings (every visible figure)** — the
  renderer positions and draws but **formats no figure** — and always keep the accessible
  server-rendered fallback table. The template arithmetic-free AST guard still holds (vendored JS is
  not scanned; numbers still become strings only in `outputs.formatting`). **(b) To make an internal
  engine artifact legible from a *saved* run, persist a serializable PROJECTION additively — never
  promote it onto the frozen contract.** Mirror the duck-typed accessor pattern
  (`selected_dispatch_energy` / `last_sizing_run` → `last_dispatch_profile`): the producer retains
  its last result, the engine exposes a `last_*()` hook, the quote pipeline reads it via
  `getattr`/`callable` and persists it onto an **additive, Optional, hash-excluded `QuoteRecord`
  field** (excluded from `_canonical_run_input` so `input_hash`/`quote_id` are unchanged and
  `SCHEMA_VERSION` is **not** bumped; engines without the hook leave it `None`). **(c) Project a real
  slice, never a synthesized shape:** persist *one real solved day* (chosen deterministically, e.g.
  highest-discharge, earliest on ties), not an element-wise average — an average authors a shape no
  half-hour actually had. **(d) When the data genuinely isn't on the record, show a labelled seam**
  (what's missing + how to get it), never an invented series or a zero-filled placeholder. This is
  the same "extract → format → render" discipline extended across the *client-render* boundary and
  the *persistence* boundary — the two last places fabrication or a frozen-contract violation can
  sneak back in.

## Evidence
W5-2 (D25) built the Wave-5 visual analytics on the W5-1 surface: `static/charts.js` became a thin
**vendored-Plotly** (pinned v2.35.2, no CDN) config builder for the 3D sizing surface (feasibility
colour band + chosen-optimum marker), the half-hourly dispatch, P50/P90 cashflows, DSCR-vs-covenant
and the firm-vs-gated value stack — all reading `app/view.py` islands (raw values + pre-formatted
strings). The dispatch chart reads a **persisted** representative day: new `gridq.schemas.dispatch_profile`
(`DispatchProfile`/`DispatchProfilePoint`) projected by `HalfHourlyDispatchSchedule.representative_day_profile()`,
exposed via `MilpEngine.last_dispatch_profile()` (mirrors `last_sizing_run`, D23) and persisted onto
`QuoteRecord.dispatch_profile` — additive, hash-excluded, `SCHEMA_VERSION` unchanged, frozen
`OptimisationResult` untouched (D13). A real-mode run with no metered HH series shows the honest
"dispatch not persisted" seam, never a fabricated series. Tests: `test_dispatch_profile.py`,
`test_app_view_islands.py`, `test_app_charts_wiring.py` (vendored-Plotly/no-CDN, islands populated,
deterministic page, honest seam) — whole tree green.

## Notes
Reusable method for any chart that outgrows a static table: vendor-don't-CDN, island-don't-compute,
project-don't-promote, real-slice-don't-average, seam-don't-fabricate. The *facts* (what was built)
live in `docs/changelog.md` + `docs/decisions.md` (D25); this is the method, so it belongs in
SKILLS.md. One bounded edit. Extends the `agent/frontend` UI bullet with the renderer-vendoring and
additive-projection rules W5-2 added.
