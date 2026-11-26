# Wave 5 Scope — Finish + Public Demo

**Date:** 2026-06-24 · **Author:** Olly (`agent/integration`) · **Status:** scope captured (capture-only; nothing built here) · **Gate:** [wave4_harmony_report.md](wave4_harmony_report.md) — **GO**.

Wave 5 turns the green, deterministic, honest engine into a **publicly accessible end-to-end product view**, with the AI edge (real-site ingestion → engine → outputs) front and centre. The engine, contracts, finance, scenarios, credit mapping and outputs are done and tested; Wave 5 is the **deployment + visual layer + the one engine-seam exposure** the surface needs. Everything below is a requirement, not an implementation.

Guiding constraints carried from the build (do not regress):
- **Deterministic core, agentic perimeter.** No LLM and no stochastic AI on the pricing path; LLM prose never creates or changes a number ([CLAUDE.md §4](../CLAUDE.md)).
- **Never invent or fabricate data to force a run.** Missing inputs become ranked data-asks, never guessed values.
- **Honest labelling.** Firm vs gated upside always separated; `needs_review`/placeholder-driven and otherwise non-bankable figures always labelled; provenance + warnings always visible.

---

## 1. Public deployment

**Decided target:** **FastAPI + Jinja2** (server-rendered) — see [frontend_decision.md](frontend_decision.md). Dependencies are already in the `app` extra (`fastapi`, `jinja2`, `python-multipart`, `uvicorn`); a smoke-tested thin shell exists at [src/gridq/app/app.py](../src/gridq/app/app.py) (`/`, `/run`). No JS/Node toolchain.

**Smallest path to a publicly accessible end-to-end view:**
- **Primary:** host the existing FastAPI app on a managed Python host (e.g. Render / Fly.io / Railway / Cloud Run) behind HTTPS. **One target serves both run modes** (real interactive + demo) — the real-site mode *requires* a live backend, so a backend host is the minimal-divergence choice. Add a `Procfile`/container entry (`python -m gridq.app` / `uvicorn`), pin runtime deps incl. the solver stack (`linopy<0.6`, `highspy`, `xarray<2025` — part of the reproduction contract, D22), and a healthcheck.
- **Zero-backend fallback (demo only):** because outputs are deterministic self-contained HTML, a single demo run can be exported and hosted statically (GitHub Pages / Netlify / an object store) for a backend-free public preview. This covers the demo mode but **cannot** offer the real-site interactive path — keep it as a fallback, not the product.

**Acceptance:** a public URL where a visitor reaches the full E2E view (below) for at least the demo mode, and can submit their own real site in the real mode.

---

## 2. Run modes (explicit user choice up front — never auto-select or substitute)

The UI must offer **two modes selected before running**. Do **not** default to, silently substitute, or fabricate synthetic data.

1. **Real site data (primary — the product's AI edge).** The user enters their own real site: location + available resources / connection (MIC/MEC/ASC, voltage) / tariff stack / consents / counterparty, plus an optional half-hourly meter file and forecast reference. This runs the live path: ingestion → grading (T0/T1/T2) → `MilpEngine` → finance → the three artifacts.
   - **Honest gating:** when required inputs are missing, the system returns the **ranked data-asks** and the honest partial result (degrades to `insufficient_data` / not-bankable), **never** an invented number. The machinery already exists — [ingestion/improvement_asks.py](../src/gridq/ingestion/improvement_asks.py), [ingestion/missing_data.py](../src/gridq/ingestion/missing_data.py) → `SiteModel.missing_data`, and the engine's `INSUFFICIENT_DATA` degrade. Wave 5 must **surface** the ranked asks in the UI (a "what to add to sharpen this" panel), not re-implement them.
2. **Demo data (opt-in).** Runs a **clearly-labelled existing example dataset** (see §5), never newly-made-up numbers, so a visitor can see the full flow without their own data. Must be visibly marked *illustrative example — not real-engine-confirmed / not bankable*.

**Acceptance:** the landing screen presents the two modes as an explicit choice; neither is auto-selected; the real mode shows ranked data-asks when inputs are insufficient; the demo mode is unmistakably labelled.

---

## 3. Visual layer — the full E2E run

Today only a single combined `report.html` view exists. Wave 5 must present the full run as a navigable flow so a non-technical stakeholder can follow it end to end:

**Inputs → Dispatch → Sizing → Finance → 3 artifacts**, realising the designed screens ([frontend_decision.md §4](frontend_decision.md)):
1. **Load / enter site** (mode chooser + site form or demo picker + validation/tier display).
2. **Run** (trigger pipeline; show solver status; honest progress).
3. **Inspect** — provenance (run id, input hash, assumption/code/schema versions, seed), the optimisation result (selected power/energy/duration, solver status), dispatch summary, and the **3D sizing surface** (§4).
4. **Memo + Proposal** — the underwriting pack and the customer proposal from the record.
5. **Telemetry** — the per-run telemetry event (data tier, missing-data count, outcome).

**Cross-cutting (must be visible on every relevant screen):**
- **Provenance** stamp and **warnings / ranked data-asks**.
- **Firm vs gated** as a first-class, prominent indicator (priced floor `{FIRM, CONTRACTED}` vs conditional/gated upside with unlock conditions) — not buried inside the report iframe.
- **Non-bankable / unavailable labelling** preserved (DSCR / customer bill / counterparty remain labelled-unavailable until their inputs exist — do not fill with zeros).

Keep all arithmetic out of the UI: numbers come pre-derived from the `QuoteRecord` via `outputs/viewmodel.py` + `formatting.py`; templates interpolate strings only (the arithmetic-free HTML invariant is AST-tested — keep it).

---

## 4. 3D battery-sizing surface (explicit requirement)

Render the sizing solve as a **3D surface: power (kW) × duration (h) × levered return**, with the **P90-DSCR-feasible region highlighted** and the **chosen optimum marked**.

**Pre-req status (verified now):** the optimiser **already retains and exposes the full evaluated grid** internally — `SizingRun.candidates_evaluated: list[CandidateEvaluation]`, each candidate carrying `power_kw`, `duration_hours`, `levered_return_pct` (equity IRR), `project_irr_pct`, `min_p90_dscr`, `passes_covenant`; `SizingRun.fingerprint()` serialises them ([sizing/sizer.py:75-128](../src/gridq/sizing/sizer.py#L75-L128)). The default grid is `POWER_FRACTIONS (0.25, 0.5, 1.0) × DEFAULT_DURATION_HOURS (1, 2, 4)` scaled to the site's binding capacity (9 points; the grid is configurable/extensible).

**The Wave 5 fix to do (small, additive — flagged in [03_data_contracts.md](03_data_contracts.md)):** the grid is **dropped at the engine seam** — `MilpEngine.solve()` returns only the chosen candidate's frozen `OptimisationResult` ([engine.py:245-255](../src/gridq/optimisation/engine.py#L245-L255)), so the grid never reaches the `QuoteRecord`/outputs and the surface is not plottable from persisted data. Expose it by **either** (recommended, lowest blast radius) a duck-typed accessor mirroring the existing `selected_dispatch_energy()` hook — `MilpEngine.last_sizing_run()` / `evaluated_grid()` — that the quote pipeline reads and persists into the run record/telemetry; **or** an additive optional `sizing_grid` field on the result/quote record. Either is a **recorded deliberate decision with a `SCHEMA_VERSION` note** (the frozen `OptimisationResult` promotion is the deferred event D13 names). Determinism means the grid can alternatively be regenerated by re-running the (deterministic) sizer, but persisting it is cleaner for the demo and for reproducibility from a saved quote.

**Charting:** no charting/3D library exists yet — add one self-contained, offline-capable renderer (e.g. a vendored Plotly bundle or a small WebGL/`<canvas>` surface) consistent with the no-external-CDN / deterministic-output posture. Highlight feasibility from `passes_covenant`/`min_p90_dscr ≥ covenant`; mark the optimum from `SizingRun.selected`. *(Done: W5-1/D24 first shipped a self-contained WebGL surface; **W5-2/D25** re-rendered the whole chart suite on a **vendored** `static/plotly.min.js` (pinned v2.35.2, no CDN) — the surface uses a `surface` trace with a feasibility `surfacecolor` band + a `scatter3d` chosen-optimum marker.)*

**Acceptance:** the Inspect screen shows the surface over the evaluated grid, the P90-DSCR-feasible region highlighted, the chosen size marked, sourced from persisted run data (not recomputed ad-hoc in the browser).

---

## 5. Data — demo dataset & privacy

**Demo dataset (build it):** package a clearly-labelled example under `examples/` (e.g. `examples/demo_site/`) — a representative `SiteModel` (+ a synthetic-but-labelled or properly-consented HH series and a sample forecast reference) that runs the full flow. It must be visibly *illustrative, not real-engine-confirmed, not bankable*. Refresh the stale `examples/README.md` (it still references a never-built `demo_site/`). Never present demo numbers as a real result.

**Privacy (hard rules):**
- A user's **own** entered real data is theirs and fine to process for their run.
- **Do not preload or publicly expose any real third-party offtaker meter data or counterparty credit as a default.** Real inputs stay under the gitignored `data/` path; the public demo ships only the labelled demo dataset, never real third-party data.
- **If real-data submissions are stored** (e.g. to let a user revisit a run), note **where** (storage location/retention) and that they require protection (access control, no public indexing, deletion path). Counterparty credit data is especially sensitive — keep it per-user and never a default fixture.

---

## 6. Wave 5 checklist (summary)

- [x] Choose + stand up a public host for the FastAPI app (HTTPS, solver deps pinned, healthcheck). *(W5-3 / D26 — reproducible host-agnostic `Dockerfile` + pinned `requirements.txt` (verified in a clean venv), `/healthz`, Fly.io as the worked primary (`fly.toml`) + Procfile/Render/Railway/Cloud Run; runbook in [`deployment.md`](deployment.md). **The live deploy itself is `TODO[Olly]`** per the strict W5-3 rule — accounts/terms/domain/secrets/push stay with Olly.)*
- [x] Two explicit run modes up front (real primary / demo opt-in); no auto-select/substitute. *(W5-1 / D24)*
- [x] Real mode surfaces ranked data-asks on missing inputs (reuse existing ingestion machinery); never fabricates. *(W5-1 / D24)*
- [x] Build the 4–5 navigable screens (Load → Run → Inspect → Memo+Proposal → Telemetry). *(W5-1 / D24 — landing · real form · demo · inspect · spec · memo+proposal · telemetry)*
- [x] Firm-vs-gated, provenance, warnings/data-asks, unavailable-labels visible throughout; arithmetic stays out of the UI. *(W5-1 / D24 — enforced by `tests/test_app_templates_no_arithmetic.py`)*
- [x] Expose `SizingRun.candidates_evaluated` through the engine seam (additive; recorded decision + `SCHEMA_VERSION` note) so the grid persists. *(W5-0 / D23)*
- [x] 3D sizing surface (power × duration × levered return), P90-DSCR-feasible region highlighted, optimum marked; self-contained offline renderer. *(W5-1 / D24 — first as self-contained WebGL; re-rendered on **vendored offline Plotly** in W5-2 / D25, no CDN)*
- [x] Supporting visual analytics: half-hourly **dispatch** (from the persisted `QuoteRecord.dispatch_profile` — a real representative day), P50/P90 cashflow + DSCR-vs-covenant, firm-vs-gated value stack; every chart reads run-record values only, with honest seams where the data isn't persisted. *(W5-2 / D25)*
- [x] Package + label a demo dataset under `examples/`; refresh `examples/README.md`. *(W5-1 / D24 — `examples/demo_site/`)*
- [x] Privacy: no real third-party data preloaded/public by default; document storage + protection if submissions are stored. *(W5-1 / D24 — gitignored `$GRIDQ_RUN_STORE`, deletion path; only synthetic demo ships)*
- [ ] *(Optional, recorded D22(5))* decide whether `generate_quote`'s no-arg engine default flips to `MilpEngine`. *(left as `BaselineEngine`; the app calls `MilpEngine` explicitly — out of W5-1 scope.)*
