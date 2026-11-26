---
proposed_by: agent/frontend
date: 2026-06-24
status: proposed   # proposed | accepted | rejected
---

## Operation
insert_after   (one edit)

## Target
`## Common failure modes (watch for these)` — insert as a new bullet directly after the
"Output/rendering layers authoring numbers …" bullet proposed by `agent/outputs` (or, if that one
is not yet applied, after the existing "LLM-authored or invented numbers in outputs …" line).

## Proposed text
- A UI / demo layer re-running, re-aggregating, re-formatting or **charting** figures instead of **serving values from the run record** → build the frontend as a **read-only surface over the deterministic core**: routes call the pipeline (`gridq.quotes.generate_quote`) + the read-model (`gridq.outputs.build_view_model`); templates interpolate only strings produced by `gridq.outputs.formatting` (expose them as Jinja `fmt_*` globals) — **never** a number computed, aggregated or stringified in a route/template (that belongs to `finance`/`quotes` and `outputs.formatting`). For charts, pass a **JSON island of raw record values (for pixel geometry) + pre-formatted label strings (for any visible text)** to a self-contained client renderer (vanilla canvas/WebGL, no CDN/build) — it positions and draws but formats no figure; always ship an accessible server-rendered fallback table. Make these *properties, not hopes*: an **AST test over the templates** (forbid arithmetic operators + `sum`/`round`/`abs`… filters/calls on figures — mirror `test_outputs_no_arithmetic_in_html.py`), plus "served bytes equal a fresh render of the record" and "two runs identical". Keep the web framework an **optional dependency group** (engine/CLI/tests import without it). And: **never default to or substitute synthetic data** — offer real-vs-demo as an explicit choice, surface the ranked **data-asks** when inputs are missing (degrade honestly, never invent), and when a demo must show a populated story, run the **real engine on clearly-labelled synthetic inputs** (the `rich_quote`/`demo_site` pattern: a labelled synthetic input is not fabrication — the figures are still engine-derived and the run stays honestly not-bankable).

## Evidence
W5-1 (D24) built the full Wave-5 surface on top of the W3-7b shell: `gridq.app` (FastAPI+Jinja2,
self-contained, no JS toolchain) with two explicit run modes, the navigable E2E flow, and a 3D sizing
surface. Figures flow only through `build_view_model` + `formatting` (Jinja `fmt_*` globals);
`view.py` builds JSON chart islands (raw values + pre-formatted strings) consumed by a self-contained
WebGL/canvas `static/charts.js`; `store.py` persists each run by deterministic `quote_id` under a
gitignored store. `tests/test_app_templates_no_arithmetic.py` AST-parses every template and forbids
arithmetic/aggregation on figures; `test_app_demo.py` asserts the served artifact equals a fresh
render of the record; `test_app_real_mode_gating.py` asserts missing inputs surface data-asks and
degrade to `insufficient_data` without invented numbers. The demo runs the real `MilpEngine` dispatch
+ sizer on `examples/demo_site/` (synthetic load) with illustrative labelled debt — populated surface,
still `not_bankable`. Whole tree green: **537 tests; ruff/format/mypy(100)/review_gates clean.**

## Notes
Procedural "how to act" for any interactive surface over the deterministic core. Extends the outputs
`extract → format → render` rule across the request boundary **and** the client-render boundary (the
view layer and a chart's pixel-math are the last places arithmetic/fabrication can sneak back in), and
adds the honest-mode rule (no default-to-synthetic; data-asks over guesses; labelled-real-engine demo).
The *facts* (what was built) live in `docs/changelog.md`; this is the reusable method, so it belongs
in SKILLS.md. One bounded edit.
