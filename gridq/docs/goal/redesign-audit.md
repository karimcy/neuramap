# Cinematic Frontend Redesign - Functional-Parity Audit

**Goal:** redesign everything served under `src/gridq/app/` into a cinematic, premium,
verse.inc-class aesthetic. **Presentation layer ONLY** - no engine, QuoteRecord, route or
numeric-logic change. The core invariant (*the interface formats, it never computes or invents
a number*) survives untouched.

**How to use this document:** every route, template, binding, interaction and a11y feature
below must work identically after the redesign - this is the parity checklist the QA gate
verifies against. Compiled 2026-07-03 (branch `goal/cinematic-redesign`).

---

Server-rendered FastAPI + Jinja2, self-contained (no external CDN/web fonts). Deterministic-core invariant throughout: **the interface computes nothing** - every figure is read off the stamped `QuoteRecord` via `gridq.outputs.build_view_model` + `gridq.outputs.formatting`, exposed to templates as `fmt_*` globals or embedded as raw values inside JSON data-islands that the client renderer only positions. App factory: `create_app()` in `app.py`; module entry `__main__.py` -> uvicorn.

## 1. Routes

All routes are defined inside `create_app()` in `src/gridq/app/app.py`. A `render()` helper wraps `templates.TemplateResponse`.

| Method | Path | Handler | Response / template | Notes |
|---|---|---|---|---|
| GET | `/healthz` | `healthz` | `JSONResponse {"status":"ok"}` (200) | No template, no pandas; auth-exempt LB probe |
| GET | `/` | `landing` | `landing.html` (200) | Front page; builds `hero` from newest solved run via `_hero_stats`, `recent[:5]`, `stats` |
| GET | `/projects` | `projects` | `projects.html` (200) | Only `_is_complete` runs |
| GET | `/equipment` | `equipment_page` | `equipment.html` (200) | `eq = equipment.catalogue()` |
| GET | `/review` | `review_page` | `review.html` (200) | Query param `?run=<quote_id>` selects run; defaults to first completed; `redteam.red_team()` + `redteam.SEVERITY_LABEL` |
| GET | `/new-quote` | `new_quote` | `new_quote.html` (200) | The 5-stage wizard; `?demo=1` handled client-side by `wizard.js` |
| POST | `/api/run` | `api_run_start` | `JSONResponse {"job_id":...}` (200) or `{"error":"invalid","message":...}` (**400**) | Starts a **background thread** run; `demo=1` runs curated `DemoEngine`; else builds `SiteModel`, synthesises HH profile if only annual kWh, applies reference tariff rates. Field map: `grid_connection_kva`->`mic_kw` |
| GET | `/api/run/{job_id}` | `api_run_status` | `JSONResponse` job dict (200) or `{"status":"unknown"}` (**404**) | Poll contract: `status` = `running` / `done` (+`quote_id`) / `error` (+`message`) |
| GET | `/real` | `real_form` | **RedirectResponse -> `/new-quote` (307)** | Unified into wizard |
| GET | `/real/haywood-oaks` | `real_form_example` | `real_form.html` (200) | Pre-fills from private gitignored `load_haywood_oaks_prefill()`; adds `example_note`; falls back to blank form (never 404/500) |
| POST | `/run/real` | `run_real` | **RedirectResponse -> `/run/{quote_id}` (303)**; on bad input `real_form.html` (**400**) | Synchronous real MILP run |
| GET | `/demo` | `demo_page` | **RedirectResponse -> `/new-quote?demo=1` (307)** | |
| POST | `/run/demo` | `run_demo` | **RedirectResponse -> `/run/{quote_id}` (303)** | Curated `DemoEngine` + `DEMO_DEBT_TERMS` + `DEMO_LABEL` |
| GET | `/run/{quote_id}` | `inspect` | `inspect.html` (200) or `not_found.html` (**404**) | Builds 5 chart islands + optional `upside`/`upside_streams` + `asks`; the main results screen |
| GET | `/run/{quote_id}/spec` | `spec` | `spec.html` (200) / `not_found.html` (404) | |
| GET | `/run/{quote_id}/artifacts` | `artifacts` | `artifacts.html` (200) / `not_found.html` (404) | `generate_artifacts()` -> `report_html` into iframe `srcdoc` |
| GET | `/run/{quote_id}/telemetry` | `telemetry` | `telemetry.html` (200) / `not_found.html` (404) | |
| - | `/static/*` | `StaticFiles` mount | static assets | Auth-exempt |

**Optional gate:** HTTP Basic middleware (`_gate`) registered only when `GRIDQ_BASIC_AUTH="user:pass"` is set; 401 + `WWW-Authenticate: Basic realm="GridQ"`; exempts `/healthz` and `/static`. Constant-time compare.

**Orphans / dangling links (cleanup candidates, decide during redesign):**
- `templates/demo.html` is **not rendered by any route**. Dead template.
- `equipment.html` links to `/equipment/upload` - **no such route** (dead link).
- `static/changes-2026-07.html` is a standalone page not referenced anywhere.

`ASSUMPTION_VERSION = "gb_v1_3"` is pinned in `app.py`.

## 2. Templates

Two base layouts; page templates extend one; `_`-prefixed partials are `{% include %}`d.

### Base layouts
| Template | Body class | Used by | Key chrome |
|---|---|---|---|
| `base.html` | `body.app` | all app-shell pages | Sidebar nav (Dashboard/Projects/Equipment/Review + "New Quote"), topbar breadcrumbs (`{% block crumbs %}`), run tabs (when `nav_quote_id`: Results/System spec/Proposal/Telemetry), `{% block statusstrip %}`, footer. Loads `app.css?v={{static_v}}`. Skip-link, `#main tabindex=-1`. |
| `base_immersive.html` | `body.immersive` | `landing.html` only | Minimal: no sidebar, just `#main`. Skip-link. |

Both inject `document.documentElement.classList.add('js')` before paint (drives `html:not(.js)` no-JS reveal safety) and a data-URI SVG favicon.

### Page templates -> route -> context consumed
| Template | Route(s) | Extends | Context / data bindings |
|---|---|---|---|
| `landing.html` | `/` | immersive | `hero.*` (power_kw, energy_kwh, irr_pct, irr_label, payback_years/label, savings_gbp/label, label, quote_id), `has_example`, `stats`, `recent`. Includes `_viz_surface`, `_viz_stack`, `_viz_cashflow`, `_viz_proposal`. Loads `dashboard.js`. |
| `new_quote.html` | `/new-quote` | base | `eq.batteries`, `eq.inverters` (dropdowns). Loads leaflet.css + plotly + leaflet.js + `run_progress.js` + `wizard.js`. |
| `real_form.html` | `/real/haywood-oaks`, POST `/run/real` (error) | base | `form.get(...)` values, `error`, `example_note`. Static form (no JS). |
| `inspect.html` | `/run/{id}` | base | `vm.*`, `quote.*`, `site.*`, `is_demo`, `nav_quote_id`; islands `surface`/`finance`/`dispatch`/`utilisation`/`value_stack`; `upside`/`upside_streams`; `asks`. Includes 12 partials + 5 island script tags. |
| `spec.html` | `/run/{id}/spec` | base | `vm.spec.*`, `quote.optimisation_result.*`. Includes `_warnings`, `_provenance`. |
| `artifacts.html` | `/run/{id}/artifacts` | base | `report_html` in `<iframe srcdoc>`, `is_demo`. |
| `telemetry.html` | `/run/{id}/telemetry` | base | `telemetry.*` (event_type, quote_id, data_tier, missing_data_count, human_minutes, installer_iterations, contributor, notes). Includes `_provenance`. |
| `review.html` | `/review` | base | `runs`, `selected_id`, `review.{items,counts,total}`, `severity_label`. Run picker `<select onchange>`. |
| `projects.html` | `/projects` | base | `runs[]` (label, site_id, status, is_demo, quote_id). |
| `equipment.html` | `/equipment` | base | `eq.solar/batteries/inverters/counts`. Inline script: tab toggle. |
| `not_found.html` | 404 fallback | base | `quote_id`. `.state` empty-state component. |
| `demo.html` | **(orphaned)** | base | dead. |

### Partial include graph
All included by `inspect.html` unless noted:
- `_statusbar.html` -> `inspect`, `spec`, `artifacts`, `telemetry` (via `{% block statusstrip %}`)
- `_system_diagram.html`, `_surface.html`, `_dispatch.html`, `_utilisation.html`, `_value_streams.html`, `_finance.html`, `_data_asks.html`, `_improve.html`, `_warnings.html`, `_provenance.html` -> `inspect.html`
- `_warnings.html` also -> `spec.html`; `_provenance.html` also -> `spec.html`, `telemetry.html`
- `_viz_surface.html` -> `landing.html` (decorative hero SVG); `_viz_stack.html`, `_viz_cashflow.html`, `_viz_proposal.html` -> `landing.html` (decorative feature SVGs; NOTE: these carry hardcoded ILLUSTRATIVE figures - "16.0% IRR", "6.8 yr", "1.45x DSCR" - a rendering of the F-011 class of problem inside decorative mockups; the redesign must replace them with clearly-decorative or record-driven visuals)

### Partial context bindings
| Partial | Consumes |
|---|---|
| `_statusbar.html` | `is_demo`, `vm.quote_mode`, `vm.bankable_status`, `vm.split.floor_total_gbp`/`upside_total_gbp` (`fmt_gbp_compact`), `vm.data_tier` |
| `_system_diagram.html` | `vm.spec.selected_power_kw/energy_kwh/duration_hours`, `site.connection.mic_kw`, `site.load_data.max_import_kw`. Inline SVG single-line diagram. |
| `_surface.html` | `quote.sizing_grid.points/selected`, `surface.{z_label,selected,provenance,covenant_label,holes_filled,has_surface}`; fallback table iterates `quote.sizing_grid.points` |
| `_dispatch.html` | `dispatch.{available,basis_note,power_label,energy_label,duration_label,rows}` |
| `_utilisation.html` | `utilisation.{available,basis,segments[].pct/label/note}` |
| `_value_streams.html` | `vm.split.{floor,upside,excluded,floor_total_gbp,upside_total_gbp}`, `upside.*`, `upside_streams`; inline `stream_desc` dict (9 streams) |
| `_finance.html` | `vm.returns/dscr/price/debt/bill_savings.*`, `quote.financial_case.equity_irr`, `finance.cashflows.rows`, `finance.dscr.rows` |
| `_data_asks.html` | `asks[].{title,why,blocks,detail}` |
| `_improve.html` | `site.*` hidden fields; upload form (`improve-form`, `improve-hh`, `improve-run`, `improve-file`) |
| `_warnings.html` | `vm.warnings[]` |
| `_provenance.html` | `vm.provenance.*` (quote_id, site_id, data_tier, quote_mode, assumption_version, code_version, schema_version, input_hash, created_at, expiry_date), `quote.optimisation_result.solver_metadata.{seed,model_version,reproduction_env}` |

### Data-island script tags (`inspect.html`)
| Element id | Source (view.py) |
|---|---|
| `surface-data` | `view.surface_island(quote.sizing_grid)` |
| `finance-data` | `view.finance_island(vm)` |
| `dispatch-data` | `view.dispatch_island(quote.dispatch_profile)` |
| `utilisation-data` | `view.utilisation_island(quote.dispatch_profile)` |
| `valuestack-data` | `view.value_stack_island(vm)` |

Parsed into `GridQCharts.init({...})` in an inline script that also wires the improve-quote form. Other inline scripts: base (JS-class marker), equipment (tab toggle), review (run-picker onchange).

## 3. Static assets

Cache-busted with `?v={{ static_v }}` (max mtime over static/**, computed in `create_app()`).

| File | Size | Ours/vendor | Loaded by |
|---|---|---|---|
| `app.css` | 55 KB | ours | every page |
| `charts.js` | 16 KB | ours | `inspect.html` |
| `wizard.js` | 18 KB | ours | `new_quote.html` |
| `run_progress.js` | 6 KB | ours | `new_quote.html`, `inspect.html` |
| `dashboard.js` | 4.7 KB | ours | `landing.html`, `inspect.html` |
| `plotly.min.js` | 4.56 MB | vendored (v2.35.2) | `new_quote.html`, `inspect.html` |
| `vendor/leaflet/leaflet.js` | 147 KB | vendored | `new_quote.html` |
| `vendor/leaflet/leaflet.css` | 14.8 KB | vendored | `new_quote.html` |
| `changes-2026-07.html` | 13 KB | ours (orphan) | nothing |

## 4. Interactive elements

**Real-mode form (`real_form.html`)** - POST `/run/real`, multipart. Fields: `site_name`, `postcode`(req), `annual_consumption_kwh`, `max_import_kw`, `hh_file`(.csv), `mic_kw`(req), `mec_kw`, `asc_kw`, `voltage_level`(LV/HV/EHV), `dno_region`, `supplier`, `standing_charge`, `import_rates`(band:rate free text), `duos_red/amber/green_rate`, `g99`/`g100`/`supplier_consent`, `legal_name`, `company_number`, `sector`, `credit_status`. No JS.

**5-stage wizard (`new_quote.html` + `wizard.js`)** - `#wiz-form`, JS-submit -> `GridQRun.run` -> POST `/api/run`. Stages:
1. **Site** - name, business type, address type-ahead (`#addr-suggest`, 15 canned `PLACES`), Leaflet map `#wiz-map` (click-to-drop-pin -> Nominatim reverse geocode -> address/postcode; nearest-`PLACES` snaps DNO), postcode/lat/lon, DNO region (14), tariff type (6 -> `#tariff-card` from `TARIFFS`), `grid_connection_kva`.
2. **Consumption** - method cards (manual / csv / bill / api-disabled); manual = annual kWh (`data-req-manual`) + peak kW; csv/bill dropzones.
3. **Load profile** - archetype select + live Plotly curve `#load-curve` (8 `SHAPES` archetypes x annual), `#profile-stats`.
4. **Battery system** - battery/inverter dropdowns (auto + catalogue), existing solar kWp.
5. **Review** - `#rev-site`, `#rev-consume` summaries, `#review-curve`, gating panel `#gate-missing`/`#gate-list`.

Stage gating: `#wiz-next` disabled until `stageValid(step)`; `#wiz-submit` disabled until `missingItems()` empty; missing list rendered on stage 5. Back-nav by clicking done steps. Swipe animation between panes. **Demo prefill:** `#load-demo` or `?demo=1` -> fills the curated demo + hidden `#f-demo=1`; any user input clears the demo flag.

**Streaming run overlay (`run_progress.js`):** `#gq-run-overlay` (`role=dialog`, `role=status aria-live=polite`), POST `/api/run` -> `{job_id}`, polls `/api/run/{job_id}` every 800 ms (timeout 900 polls). Client-side stage ramp (5 stages, 1300 ms) but navigation ONLY on real `status:done`+`quote_id` (-> `/run/{quote_id}`). `status:error` -> `fail(message)` + Close.

**Improve-quote (`_improve.html`):** multipart form carrying the site forward as hidden fields; file pick enables submit; same overlay; produces a NEW quote id.

**Tab toggles:** equipment page; wizard method cards. **Run picker** on `/review` (select onchange).

**Chart interactions (`charts.js`):** 3D surface (orbit/zoom, Plotly surface + scatter3d), 2D heat-map, dispatch bars + SoE line + red-band shapes, P50/P90 bars, DSCR line + covenant dashed, value-stack stacked bars, utilisation donut (`Plotly.animate`, skipped under reduced motion). ALL have server-rendered `<details><table>` fallbacks.

## 5. Design-system state (pre-redesign)

**Tokens (`:root` in app.css):** light palette (`--paper:#f8fafc`, `--ink:#1a1a2e`, accent `--accent:#ff6b35` orange, `--firm:#1f7a4d`), dark-cinematic subset (`--ink-900:#14142a`, `--on-dark*`, `--glow-accent/cool`), `--sans`/`--serif` both Inter/system stacks, `--mono` ui-monospace, `--radius:12px`, spacing `--sp-1..8`, motion `--dur-fast/base/slow` + `--ease-standard/spring/out`.

**Components:** `.card` (white, hairline, 12px), `.panel.asks/warnings/error`, eyebrows (`.lp-eyebrow`, numbered "01 - SIZING"-style `.lp-feature__num`), `.metric`, `.stat`, `.badge-*` family, `.state` empty-state.

**Motion:** keyframes (gq-fade-up/fade/pop/swipe/pulse/flow/float/bars/rise/cue/dash/grow/growx), scrollytelling pin (`#how-pin`, 320vh), `.reveal`/`.in` observer reveals, `[data-count]` count-ups (with `data-count-exact` landing on the server string verbatim), `.gq-ambient` orbs, global `prefers-reduced-motion` reset + JS guards.

## 6. Data-binding invariants (MUST SURVIVE)

Numbers reach templates ONLY via: (a) view-model fields, (b) `fmt_*` Jinja globals (finished strings), (c) data islands (raw values for pixel geometry + pre-formatted labels). Templates never compute.

**`fmt_*` globals:** fmt_gbp, fmt_gbp_compact, fmt_gbp_per_kwh, fmt_pct, fmt_ratio, fmt_kw, fmt_kwh, fmt_hours, fmt_years, fmt_date, fmt_yes_no, fmt_pass_fail, fmt_stream, fmt_structure, fmt_titleise, fmt_unavailable, fmt_placeholder, fmt_opt.

**Guard tests pinning behaviour (keep green or update deliberately):**
- `test_app_templates_no_arithmetic.py` - Jinja AST scan forbids arithmetic/sum/round/min/max/abs/pow/divmod in every template.
- `test_app_design_system.py` - em-dash guard (templates + non-vendored JS + DEMO_LABEL + _EXAMPLE_NOTE); token presence pins (`--sp-1/4/8`, motion tokens, `--ink-900`, `--on-dark*`, `--glow-*`); `.gq-ambient` required; `vhero__glow` forbidden; spring cubic-bezier literal appears EXACTLY once; landing wires skip-link/#main/favicon.
- `test_app_view_islands.py` - island shapes, stage-awareness, `_hhmm`, feasibility in {0,1}.
- `test_app_charts_wiring.py` - plotly vendored, NO external CDN (`src="https?://"` forbidden), chart div ids (`surface`, `chart-dispatch`, `chart-cashflows`, `chart-dscr`, `chart-valuestack`) + island ids present, `GridQCharts.init(` called, honest dispatch degrade, byte-deterministic render.
- `test_app_smoke.py` - copy pins: "Sharpen this quote", "NOT BANKABLE", "unavailable", friendly 400/404 copy, `/real` + `/demo` on landing.
- `test_app_demo.py` - "DEMO"/"ILLUSTRATIVE"/"NOT BANKABLE"/"Sizing surface"/"surface-data" on inspect; artifacts iframe srcdoc byte-match.
- `test_app_real_mode_gating.py` - "Sharpen this quote", "half-hourly", "required".
- `test_app_examples.py` - haywood-oaks intake form (`action="/run/real"`, `name="hh_file"`).
- `test_app_deploy.py` - healthz, basic-auth gate, static reachable.

## 7. Accessibility features (MUST SURVIVE)

Skip link (`.skip-link` -> `#main`) in both bases; `<main id="main" tabindex="-1">`; nav aria-labels (Primary/Breadcrumb/Run navigation); `aria-current="page"` on run tabs; chart divs `role="img"` + aria-labels; wizard map `role="application"`; run overlay `role="dialog"` + `aria-live="polite"`; equipment tablist roles; `:focus-visible` accent outline; global `[hidden]{display:none!important}`; `html:not(.js)` reveal fallback; chart `<details>` table fallbacks; count-ups fall back to server strings; global reduced-motion reset + JS guards.

## 8. Redesign risk notes

- **charts.js mirrors palette BY HAND and has ALREADY DRIFTED** (`FIRM="#1f4d3a"`, `ACCENT="#b4540f"` vs css `--firm:#1f7a4d`, `--accent:#ff6b35`). The redesign must make charts read the palette from one place (CSS custom properties via getComputedStyle, or a generated JS token module) - not fix the drift by hand a third time.
- **wizard.js hardcodes accent/greys/font** for the live curve - same fix.
- **Inline hex fills** in `_surface.html`, `_system_diagram.html`, `_viz_*.html`; the `_viz_*` decoratives also carry hardcoded illustrative figures ("16.0%", "6.8 yr", "1.45x") - replace with abstract/decorative visuals or record-driven ones.
- **test_app_design_system.py pins token NAMES and the spring-bezier-once rule** - the new token file must either keep those names or the tests update in the same commit.
- **Copy strings pinned by tests** (list in §6) - reword only with test updates in the same commit.
- **Chart/overlay/wizard DOM contracts:** ids `surface`, `surface-heatmap`, `chart-*`, `*-data` islands, `gq-run-overlay` internals, `#f-*` wizard fields, `.wiz-pane[data-pane]`, `.reveal`/`[data-count]`/`#how-pin` - restructuring the DOM silently breaks JS; preserve ids/attributes or update JS + tests together.
- **No-CDN/offline invariant is load-bearing** (tests + CSP posture): fonts must be self-hosted files; Three.js must be vendored; no external requests.
- **Two base layouts / two design languages** (`.app` shell vs `.lp-*` landing) - the redesign should unify on one token system with two layout modes, not three languages.
- **Dead surfaces:** `demo.html` (orphan), `/equipment/upload` (dead link), `changes-2026-07.html` (orphan) - remove or wire up during the shell pass.
