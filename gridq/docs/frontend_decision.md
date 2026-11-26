# Frontend Decision — v1 demo surface (W3-7a)

**Status:** Proposed (decision-only; no application code) · **Date:** 2026-06-22 · **Author:** Olly
**Branch:** `agent/frontend` · **Implements the choice deferred in:** [issue 013 — Web UI shell & demo flow](issues/013-ui-shell.md) ("framework is a v1 choice: FastAPI/Flask/Streamlit").
**Relates to:** boundary scope item 3.9 (2026-quality outputs & visual proposal layer), milestone M8 outputs, the `agent/outputs` and `agent/quotes-telemetry` lanes.

---

## TL;DR — the verdict

**Build the v1 demo as a thin [FastAPI](https://fastapi.tiangolo.com/) + server-rendered [Jinja2](https://jinja.palletsprojects.com/) app** that wraps the existing pipeline and renders every number straight off the stamped `QuoteRecord`. It is the only option that scores well for **both** audiences at once (a non-technical exec *and* the two Python engineers who maintain it), fits the pure-Python/Pydantic stack with **no JavaScript toolchain**, satisfies issue 013's live-flow + smoke-test bar, keeps **pricing arithmetic out of the UI by construction**, and **grows toward a real product surface by addition, not a rewrite**.

| Framing question | Answer |
|---|---|
| **Is static HTML enough for the next demo?** | **No.** A static page bakes numbers from a one-off JSON: there is no live `load → run`, nothing for the required smoke test to exercise, and the figures shown are a frozen copy that can silently drift from the run record (the exact "UI authors numbers" failure to avoid). For the exec, credibility comes precisely from *watching a real site go in and a quote come out* — a frozen page cannot show that. Keep a single pre-rendered page only as an **optional break-glass fallback**, never as the demo itself. |
| **Do we need interactivity now?** | **Yes — but only request/response interactivity, not a reactive SPA.** A person must load/select a `SiteModel`, trigger a deterministic run, and navigate the named screens. Plain server-rendered HTML forms (`POST` load, `POST` run, `GET` inspect/memo/telemetry) already satisfy this. No client-side state, no live recomputation, no JS framework. (HTMX is *optional sugar* for later — deferred to zero for the first demo; see guardrails.) |

> **This is a framework decision, not a green light to build.** The framework choice (FastAPI+Jinja2) holds regardless of audience. But the demo's *value* is gated by two preconditions the analysis surfaced — the output generators must exist and the run record must expose pre-derived display numbers — without which this is an engineer demo, not an exec demo. See **§5 Hard build preconditions**.

---

## 1. Context — why this decision, now

The pipeline is credible only if a person — not a test runner — can drive it end-to-end. Issue 013 (Milestone 8) calls for a thin web app wrapping the existing modules into one demo flow (`load → run → inspect → memo/proposal → telemetry`) and explicitly left the framework as a v1 choice. Wave 3 needs that choice made and the smallest useful scope defined before any UI code is written.

**Who the next demo is for (the two audiences that drive the trade-off):**

- **Karim** — Operations / Partnerships / Legal. A **non-technical** business stakeholder. He needs to *see* the product work end-to-end and find it credible. For him, the payoff is a believable **customer proposal** (bill before/after, savings, term, structure), not a wall of hashes.
- **Olly** — the two engineers who build and extend the system. They need the frontend to be **comprehensible and maintainable in their Python toolchain** — not a second language, a build step, or a split repo.

**The non-negotiable constraint** (CLAUDE.md / SKILLS.md "Deterministic core"): *every output number traces to input + assumption version + code version; the UI never computes or authors a figure.* This is the literal meaning of **"keep pricing arithmetic out of the UI"** and it is the lens through which every option below is judged.

**Current repo reality (verified 2026-06-22):**
- `src/gridq/quotes/` exists — `generate_quote(...) → QuoteRunResult` is the pipeline entry point the `gridq` CLI already uses; `assemble_quote_record(...)` stamps a `QuoteRecord`.
- `src/gridq/outputs/` is an **empty package** — the memo (011) and customer-proposal (012) generators are **not built yet**.
- `src/gridq/app/` does **not** exist — the frontend is greenfield.
- `FinancialCase` exposes raw annual series (`p50_cashflows`/`p90_cashflows: list[float]`), `dscr_summary`, `irr`, `payback_years`, `PriceRecommendation` — but **no stamped headline aggregate** (no lifetime/NPV total) and **no `bill_before`/`bill_after`/`savings` fields anywhere**.

---

## 2. The two framing questions, in full

### Is static HTML enough for the next demo? — No
Static-HTML-only cannot deliver issue 013's core acceptance criteria: a live `load → run` against the mock `OptimisationResult`, and `tests/test_app_smoke.py` proving the app boots and routes respond. A static page has no server and no run, so there is nothing to smoke-test, and its numbers are a hand-regenerated copy that drifts from the run record. It is a *demo screenshot, not a product surface*, and has no growth path without becoming the FastAPI option anyway.

### Do we need interactivity now? — Yes, but request/response only
The demo must be navigable and must run the pipeline live. That requires a server, **not** a single-page app. Server-rendered forms cover `load → run → inspect` today; rich client-side interactivity buys a deterministic, document-style product nothing in v1.

---

## 3. Options compared

Scores are 1–5 (5 best), consolidated from a four-lens judge panel (speed · audience-fit · polish-ceiling · production/stack-fit) and adversarially stress-tested. "Overbuild" and "Stack fit" are scored so that **5 = best for this v1 local demo**.

| Criterion | Next.js | Streamlit | **FastAPI + Jinja2** | Static-HTML-only |
|---|:--:|:--:|:--:|:--:|
| Speed to a *qualifying* demo | 1 | 5 | **4** | 2¹ |
| Showing Karim (exec credibility) | 3² | 3 | **5** | 2 |
| Olly comprehensibility / maintainability | 1 | 4 | **5** | 4³ |
| Polish ceiling (proposal) | 5 | 2 | **4**⁴ | 4³ |
| Right-sized (low overbuild risk) | 1 | 4 | **4** | 3¹ |
| Arithmetic kept out of the UI | 1⁵ | 2⁶ | **4**⁷ | 5⁸ |
| Production compatibility (grows w/o rewrite) | 3 | 2⁹ | **5** | 1 |
| Stack fit (pure-Python / Pydantic) | 1 | 5 | **5** | 3 |

¹ Static is cheap to *make* but **does not deliver the brief** (no live run, fails the smoke test) — it under-builds, so it is not actually "fast to a qualifying demo."
² Highest raw polish, but for *this* pitch credibility lives in the deterministic numbers, not front-end flash; a slick SPA misattributes trust to the wrong layer.
³ Trivial to maintain *until* you must hand-regenerate baked JSON, which invites someone hand-editing a figure in — a drift / "UI authors numbers" trap.
⁴ Jinja2 emits the same arbitrary HTML/CSS as React — typography, layout, print stylesheet, CSS/SVG animation. Reaches ~90% of the polish ceiling with no SPA; a polished, **shareable** proposal is reachable later by rendering the same template to a self-contained HTML file.
⁵ A separate API + React client is the strongest invitation to leak or duplicate pricing logic into the client.
⁶ The script-rerun model makes "never recompute in the UI" a *discipline to police*, not a property you get for free.
⁷ By construction **iff** the run record exposes pre-derived display numbers (see §5 P2). The template only interpolates finished fields; all maths stays in `gridq.finance`/`gridq.quotes`.
⁸ Nothing executes, so nothing recomputes — but the numbers are a frozen copy that drifts from the record, which is its own violation.
⁹ Streamlit is a data-app runtime, not a request/response server; adding auth/routing/multi-user/hosting later is effectively a re-platform.

### Per-option verdict
- **Next.js — rejected (overbuild, worst stack-fit).** Drags a JS/Node toolchain, a build step, a second language, and a split repo into a pure-Python team for a single-user, no-auth, **local** demo. Its only real edge — highest polish ceiling for the eventual animated proposal layer (boundary 3.9) — is explicitly **out of scope for v1**, and the proposal is a baked-number *document* whose polish lives in HTML/CSS, so an SPA runtime buys it nothing. Textbook heavy-SPA smell.
- **Streamlit — rejected (dead-end seed, weak proposal surface).** Fastest to a first demo and genuinely pure-Python, but (a) the production path is a rewrite, and (b) its default chrome reads as an internal data tool with no clean route to a branded, shareable proposal — and FastAPI is only slightly slower to first demo while winning everywhere else.
- **Static-HTML-only — rejected as the demo (under-build).** Cannot show the live end-to-end run, fails the smoke-test criterion, and risks number-drift. **Retained only** as an optional zero-dependency frozen-demo fallback.
- **FastAPI + Jinja2 — recommended.** The convergence point: clickable, named, real-URL screens read as "a real product" to Karim; idiomatic Python in one repo for Olly; numbers rendered server-side from the `QuoteRecord`; satisfies issue 013's live-flow + smoke test; and FastAPI is a production-grade ASGI server, so auth/multi-user/hosting are later *additions*, and the 2026-quality proposal layer (3.9) is a *CSS/markup upgrade on the same template*, not a migration.

---

## 4. The smallest useful frontend

A **single thin FastAPI app** confined to `src/gridq/app/`: routes that call the existing `gridq.quotes.generate_quote(...)` and `gridq.ingestion` directly (no new abstraction layer), five server-rendered Jinja2 screens mapping 1:1 to issue 013, one CSS file, and one `TestClient` smoke test. Every screen reads fields off the already-stamped run record. No new domain logic, no auth, no DB, no JS build step.

### Screens (1:1 with issue 013)

| # | Screen | Purpose | Data source (UI displays only) |
|---|---|---|---|
| 1 | **Load SiteModel** | Put a real site in (the credibility moment); surface validation & tiering honestly. | `gridq.ingestion` — validation result, data tier (T0/T1/T2), gate failures with the specific actionable errors. Invalid input → those errors, not a stack trace. |
| 2 | **Run** | Trigger the deterministic pipeline; watch a site become a quote, no manual code steps. | `gridq.quotes.generate_quote(...)` against the mock `OptimisationResult`. UI only invokes and reports; links to the stamped quote. |
| 3 | **Inspect quote** | Show the immutable, traceable run record — the proof the numbers are deterministic & bankable. | The `QuoteRecord`: provenance (inputs hash, data tier, assumption version, code version, seed), optimisation summary, finance outputs (P50/P90, DSCR, price-to-target), gate/credit status, lifecycle state. Field interpolation only. |
| 4 | **Memo + Proposal** | Render the two bankable artifacts in-browser; the proposal is the installer-facing payoff. | The M8 memo (011) + customer proposal (012) rendered from the record: bill before/after, savings, term, structure. **Numbers match the record exactly.** *(Gated on §5 P1 + P2.)* |
| 5 | **Telemetry** | Show the per-run telemetry that proves the system measures itself from day one. | The run's `TelemetryEvent` (X.3): tier, human-minutes, iterations, outcome, assumption/code versions. |

**Run-of-show for an exec demo:** lead **Load → Run → Proposal** (the payoff), with **Inspect → Telemetry** as the "…and here's why it's bankable" backstop — *not* engineer-first.

### Reusable components
- `base.html` — shared layout + nav across the five screens, one linked stylesheet.
- A small set of partials, **extracted only on second reuse** (do not pre-factor): a provenance block (hash / assumption version / code version / seed), a finance-figures block (P50/P90 / DSCR / price-to-target — *interpolation only*), a gate/tier/lifecycle status badge, and a validation-errors + ranked-data-asks block for the Load screen.

### Files to create (v1 — deliberately ~8, see guardrails)

| Path | Purpose |
|---|---|
| `src/gridq/app/app.py` | Thin FastAPI app: routes for the five screens that call `gridq.quotes` / `gridq.ingestion` directly and render templates from the stamped record. Orchestration + presentation only. |
| `src/gridq/app/templates/base.html` | Shared layout + nav; single CSS link. |
| `src/gridq/app/templates/load.html` | Load screen: upload/paste/select form + validation/tier/gate-error rendering. |
| `src/gridq/app/templates/run.html` | Run screen: trigger the pipeline, link to the produced quote. |
| `src/gridq/app/templates/quote.html` | Inspect screen: provenance, finance outputs, gate/credit status, lifecycle — interpolation only. |
| `src/gridq/app/templates/memo_proposal.html` | Render the memo + customer proposal in-browser from the record. |
| `src/gridq/app/templates/telemetry.html` | Telemetry screen from the `TelemetryEvent`. |
| `src/gridq/app/static/app.css` | One minimal stylesheet (no JS build, no external assets). |
| `tests/test_app_smoke.py` | `TestClient` smoke test: app boots and primary routes respond — satisfies issue 013's acceptance criterion. |

**Explicitly NOT in v1** (cut after the analysis flagged them as overbuild smuggled back in):
- ~~`src/gridq/app/service.py`~~ — redundant. `gridq.quotes.generate_quote(...)` is already the shared pipeline entry point the CLI uses; routes call it directly. Add a thin adapter later only if a real need appears.
- ~~`export.py` + `proposal_export.html`~~ — a self-contained, shareable, branded proposal file **is** the deferred boundary-3.9 / M8-Later visual layer. Defer it. The in-browser Memo+Proposal screen satisfies the v1 acceptance criterion. (When 3.9 is in scope, it is added as a CSS/template upgrade on the *same* proposal template — no framework change.)
- **HTMX** — deferred to zero for the first demo. Plain server-rendered forms with full page reloads already satisfy `load → run → inspect` and the smoke test. Adopt HTMX later only if a no-reload transition is wanted, and only as inline attributes (never a JS build).

---

## 5. Hard build preconditions (the honest part)

The framework is right, but the demo's exec-credibility depends on two things that **do not exist in the repo today**. These are preconditions for the *build* (W3-7b), surfaced by adversarial review and verified against the code:

- **P1 — Outputs must exist first.** The customer-proposal (012) and memo (011) generators live in an **empty `outputs/` package**. Until they are built (against the mock `OptimisationResult`, numbers injected from the record, prose LLM-drafted on the perimeter), screen 4 is empty/stubbed and the demo is *engineer-credible only*. The proposal is the one screen Karim cares about most.
- **P2 — A presentation contract: the record must expose pre-derived display numbers.** "Templates only interpolate, never compute" is **not automatic** with the current schema. `p50_cashflows`/`p90_cashflows` are raw `list[float]` with **no stamped headline/aggregate**, and there are **no `bill_before`/`bill_after`/`savings_absolute`/`savings_pct` fields anywhere**. An engineer asked to show a "£X saved over the term" headline or a P50-vs-P90 chart, or a "savings %", would be *forced* to `sum()`/subtract in the route or template — arithmetic in the UI, which a 200-OK smoke test cannot detect. So the build must:
  1. Have the M8 generators / quote layer **stamp every display scalar as a named, pre-derived field** on the record (or a dedicated read-model): `bill_before`, `bill_after`, `savings_absolute`, `savings_pct`, a headline lifetime/NPV value, `min_p90_dscr` (already present), and any chart series **pre-shaped**. Templates then *format only* (currency/percent string formatting), never operate.
  2. **Not build screen 4** (or any headline/chart) until those fields exist.
  3. Enforce it **structurally, not by code review**: a test that statically asserts no arithmetic operators (`+ - * /`, `sum`, `|sum`) act on finance fields inside `gridq/app/templates/`, plus issue-012's "every proposal number equals the corresponding record field" test wired into the app's smoke suite. (A review rule alone is the same discipline-based guard we *rejected Streamlit for relying on*.)

- **Demo data.** Designate **one canonical `SiteModel` fixture** whose mock `OptimisationResult` is a **deliberately sized, bankable-looking** case, clearly labelled *"illustrative mock dispatch — not real-engine output."* The integration `BaselineEngine` is always `not_bankable` with no invented revenue, so the proposal would otherwise show nothing to sell. A sized **mock** (issue 005) lets the proposal render a believable savings story *straight off the stamped record* — numbers come from the record, the mock is honestly disclosed, no fabrication. This respects the no-invented-data and no-synthetic-as-real rules: the fixture is a labelled demo input, never treated as a real site.

---

## 6. Keeping pricing arithmetic out of the UI — the mechanism

1. Routes never compute. Each route asks `gridq.quotes` for an already-finished, stamped Pydantic record (`QuoteRecord` / `TelemetryEvent` / ingestion result) and hands that object to a Jinja2 template.
2. Templates only interpolate and format fields — no operators, no aggregation, no derived values. All pricing maths stays in `gridq.finance` / `gridq.quotes`; figures (P50/P90, DSCR, price-to-target, bill/savings) are read **verbatim**, each already traceable to input + assumption version + code version + seed.
3. This becomes a *property*, not a hope, once §5 P2 lands (the record exposes every displayed scalar pre-derived) and the static-no-arithmetic test guards `gridq/app/templates/`.

---

## 7. Build sequence — "Both, sequenced" (chosen)

The next demo serves **both** audiences in one sitting, so sequence to minimise rework:

1. **Scaffold the thin shell** against the existing stamped `QuoteRecord`: screens 1 (Load), 2 (Run), 3 (Inspect), 5 (Telemetry) + the smoke test. This is buildable now — `quotes`/`ingestion` exist. Screen 4 renders provenance/finance only at this stage.
2. **Build M8 outputs (P1):** memo (011) + customer proposal (012) on the mock, with the presentation-contract fields (P2) stamped on the record; add the no-arithmetic-in-templates test.
3. **Designate + wire the sized demo fixture**, then light up screen 4 (Memo + Proposal).
4. **Present together** once the proposal renders a believable story — Load → Run → Proposal as the payoff, Inspect/Telemetry as the bankability backstop.

---

## 8. Overbuild guardrails

- All app code confined to `src/gridq/app/`; orchestration + presentation **only**; zero new domain logic (no recompute, no figure authoring).
- Ship exactly the five issue-013 screens. No extra dashboards, no CRM, no approval/sign workflow, no AI parsing of arbitrary formats (structured `SiteModel` JSON only).
- No auth, no multi-user, no DB, no hosting in v1 — local dev/demo tool.
- No JS/Node toolchain and no build step. HTMX deferred to zero; if it ever starts to resemble a JS framework, drop back to plain forms.
- Defer all boundary-3.9 polish (animations, branded layout, shareable proposal export) to M8-Later; the v1 proposal is clean and legible, not designed — *but it must still read as a customer-facing document, not a data dump.*
- Keep it ~8 files (one `app.py` + five templates + one CSS + one smoke test). Extract partials only on second reuse. Resist template/CSS sprawl.
- "Numbers shown match the run record exactly" is an explicit acceptance check.

---

## 9. Risks

- **Dependency timing.** Screen 4 needs the M8 generators (Wave 2/3, not built). Mitigation: the shell ships first against the `QuoteRecord` (step 1) and screen 4 lights up when P1+P2 land.
- **Presentation-contract debt.** Until the record exposes pre-derived display scalars, FastAPI does **not** keep arithmetic out of the UI "for free." Mitigation: P2 is a hard precondition with a structural test, not a guideline.
- **Static-fallback drift.** If a frozen page is kept as break-glass, a hand-edited figure reintroduces the "UI authors numbers" violation. Mitigation: always regenerate it from the record; treat it as build output, never hand-edit.
- **Exec expectation gap.** The demo is clickable and credible but deliberately *not* 2026-polished. Manage expectations that visual polish is M8-Later by design.
- **HTMX / template sprawl.** Both can erode the "pure Python, thin" property. Mitigation: the guardrails in §8.
- **Slightly slower to the very first pixel than Streamlit** (hand-authored templates). Accepted: the dual-audience win and the no-rewrite production path are worth it.

---

## 10. Open decisions (for whoever picks up the build)

1. **Static break-glass fallback** — keep one pre-rendered page as zero-dependency insurance for the live demo, or rely on the live app only? *(Recommend: optional, regenerated from the record; never the deliverable.)*
2. **Proposal styling for THIS demo** — clean-and-legible only *(recommended; defers 3.9)*, or a first pass at branded look-and-feel now *(risks pulling M8-Later forward)*?
3. **Fixtures in the Load dropdown** — which of the example `SiteModel` JSONs to pre-list, and which one is the canonical, deliberately-sized demo site.
4. **HTMX** — confirm "plain forms, full reloads" for v1, or accept a single inlined HTMX include (still no Node/build) for `load → run → inspect`.
5. **A `decisions.md` entry** — once accepted, record this as the next `D` (e.g. D14) with a one-line pointer here.

---

## Appendix — how this was decided

Decided via a judge-panel workflow: four independent evaluators scored all four options through distinct lenses (speed-to-demo · audience-fit · polish-ceiling · production/stack-fit), a synthesis stage produced the recommendation and minimal scope, and three adversarial reviewers attacked it (overbuild, the Karim demo, arithmetic-in-UI leak). All four lenses converged on FastAPI+Jinja2. The adversarial pass did **not** overturn the framework — it tightened the scope (cut `service.py`, the static export, and HTMX from v1) and surfaced the two hard preconditions in §5, each of which was then **verified against the actual repo state** (empty `outputs/`; `FinancialCase` has no headline aggregate and no bill/savings fields; `generate_quote` already exists).
