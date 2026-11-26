# 018 — Meeting-readiness drive: export-aware dispatch, real price inputs, local-flex net pricing, Robert Thomas end-to-end

**Status:** PROPOSED — plan only (2026-07-17). Drafted by Olly (Fable 5 advisor session per the model policy); execution is for a `feature-builder` agent (Opus 4.8, max effort), one slice at a time, quality bar green per slice.
**Owner:** joint — schemas/app/ingestion/finance/docs (Olly side); any solver-variable or objective change (Olly side, M3/M4 — approve without gate required, see §2).
**Origin:** team meeting takeaways, 2026-07-16 (Olly). Target: fully operational Robert Thomas run + platform demo, ready for a meeting.

---

## 0. What the meeting asked vs what the repo already has

The meeting takeaways were written against an out-of-date picture. Corrections that reshape the plan:

1. **"Go from LP to MILP"** — the MILP already exists and is the default (`MilpEngine`, linopy/HiGHS, D22; `gridq run` hard-codes it at `src/gridq/cli.py:201`). It already models **discharge**, but only behind-the-meter: `discharge_ub = min(power, load)` (`src/gridq/optimisation/milp_solver.py:451`) — the deliberate no-export floor. On a site whose load is near-zero in the expensive windows (both the synthetic demo profile and Robert Thomas are overnight-heavy), discharge headroom is ~0 exactly when discharging would pay, so runs *look* charge-only. The real ask is therefore **an export decision variable**, gated on consents — not an engine rewrite. That is a frozen-seam/objective change (§2, WS-A).
2. **"G98/99/100 and MPAN … should be added"** — they already exist on the schema (`ConsentState.g98/g99/g100`, `ConsentState.export_mpan`, `SupplyBoundary.import_mpan`, `ConnectionDetails.mic_kw/mec_kw/asc_kw`) and on `real_form.html`. What's missing is (a) the guided wizard (`new_quote.html`) doesn't expose them, (b) there is no **export/route-to-market arrangement** object (counterparty, terms, aggregator cut), (c) demo preselection.
3. **"3 discrete prices for 3 times of day"** — that's the red/amber/green `import_rates` band map placed on timestamps by `tariff_calendar.py`. It is the *correct* priced-floor input for a fixed retail tariff (the floor prices the site's own bill, D22). Real half-hourly wholesale prices are needed for a different purpose: valuing exported energy and stressing scenarios — and wholesale stays **gated upside** (`GATED_UPSIDE_STREAMS`, never `FIRM`, never in the priced floor) unless a decision reverses that (§2).
4. **"15–20% IRR if everything is working"** — IRR (project + equity) is already computed (`src/gridq/finance/returns.py:108,158`). The target is an outcome to measure, never an input to tune toward. If Robert Thomas doesn't clear it on real data + versioned assumptions, the platform must say so but most likely something is wrong with the revenue streams if this is the case(§6).

## 1. Ground truth (verified 2026-07-17)

- Objective prices exactly one thing: `Σ price_t·(discharge−charge)·Δt` where `price = energy band rate + DUoS rate` (`milp_solver.py:429,650-664`). CM and local flex are valued **post-solve** as conditional upside (`optimisation/valuation.py:197-265`); wholesale/BM/ancillary are gate-only £0.
- A wholesale forecast ingestion path exists (`ingestion/market_forecasts.py`, `gridq run --forecast`) but is **provenance-only** — nothing converts it to a per-HH price array or feeds the solver. No HH price series field exists on `SiteModel`/`OptimisationRequest`.
- `LocalFlexPicloStream` exists (`value_stack/gb_streams.py:563`): `utilisation_price_gbp_per_mwh (150, needs_review) × tender_window_discharge_mwh`, gated on `dno_region` + an open tender (`GateContext.local_flex_tender_open`). **No aggregator-cut concept exists anywhere.**
- App intake: wizard uploads accept `.csv,.xlsx,.xls` but the backend parses **CSV only** (`app.py:282` → `pd.read_csv`); the bill upload (`.pdf/.png/.jpg`) is collected but **never parsed**; equipment datasheets are provenance-only.
- Ingestion HH columns: `timestamp` + one of `import_kw`/`import_kwh` (aliases at `ingestion/hh_data.py:44-53`). The delivered Robert Thomas CSV uses a bare `kwh` column — **not currently an accepted alias**.
- Robert Thomas prefill mechanism exists: gitignored `examples/real/haywood_oaks_prefill.json` → `real_form.html` (`app/demo.py:81-105`). 8.3 months HH ⇒ **T1** (T2 needs ≥17,424 periods, ≥90% completeness, ≥363-day span).
- No MIC/connection-capacity estimation exists; MIC is a required user input, missing values become ranked data-asks.
- Bankability: zero `confirmed` assumptions in every release through `gb_v1_4` ⇒ everything stays `not_bankable` by code-enforced design. This drive does not change that.
- `project_status.md` is stale (stops 2026-07-02; missing D34–D42); README is fresher but predates this drive.

## 2. Decision gates — file BEFORE building the gated slices

These require a joint D-event / Olly sign-off (frozen-seam rule). The execution agent must **stop** at these boundaries unless the decision is on the record:

- **G1 (blocks WS-A2/A3): export decision variable.** New `export_t` var, `charge/discharge/export` coupling, opening `export_limit` out of `_DEFERRED_KINDS` (`milp_solver.py:118`), and a per-HH export-value accumulator. Touches Olly's solver and the no-export floor that Wave 7/D42 physics rely on (firming envelope `min(power−aux, load)`, provisioning reservations, `_EPS` tie-breaks). Proposal: export exists **only when consents cohere** (G99 `approved` + `export_mpan` + `mec_kw>0`), bounded by `min(power, mec − …)`, **excluded from the priced-floor objective** (weight 0 in the objective or valued post-solve only), with exported MWh × HH wholesale price reported under `WHOLESALE_P415` as **gated upside, CONDITIONAL, never FIRM** — consistent with B002 and north_star Wave 10. File as a new OPEN_COMMENTS entry + decision (D43 candidate).
- **G2 (blocks WS-C3 record shape): net-of-aggregator pricing on the `ValueStreamResult`.** Showing gross £, cut %, and net £ per stream extends a frozen contract — fold into the already-agreed-in-direction C027 growth (availability/utilisation split) as one joint event, or apply the cut inside `_price` and put gross/cut in `notes`/inputs (no contract change) as the v1 fallback.
- **G3 (informational): if anyone proposes putting wholesale £ in the priced floor — that reverses D20/B002 and north_star; do not build it in this drive.**

## 3. Workstreams and slices

Every slice: lands with tests; `pytest`, `ruff check .`, `ruff format`, `mypy`, `python -m gridq.dev.review_gates` green; schema additions are **additive with `None` defaults** and hash-stable for absent fields (verify against `quotes/assembly._canonical_run_input` — the C028/017 pattern); no invented numbers — gaps become validation warnings/ranked data-asks; telemetry keeps emitting one `quote_created` per run with no priced £ in it.

### WS-A — Dispatch realism: diagnosis, then consent-gated export (the real "LP→MILP" ask)

- **A1 — Diagnosis on the record (no code).** Run the demo and the Robert Thomas T1 case; extract the dispatch schedule; document *why* discharge is ≈0 (expected: no load coincident with red band ⇒ `discharge_ub=0` there). Deliverable: a short findings note (F-numbered, `docs/goal/FINDINGS.md` style) proving whether it's load-coincidence (expected) or a defect. **Do this first — it either validates or kills the need for A2/A3.**
- **A2 (gated on G1, Olly co-owned) — export variable.** As specified in G1. Constraints: SoE balance gains `− export·dt`; connection export bound `export ≤ mec − …`; `export ≡ 0` when consents don't cohere (byte-identical no-op → goldens preserved for all existing sites — the 017 "absent payload" pattern). `deliverable`/reservation aggregation must treat export like discharge for double-sell prevention.
- **A3 (gated on G1) — export valuation as gated upside.** Post-solve: `export_mwh_by_hh · wholesale_price_hh` (from WS-B) reported under `WHOLESALE_P415` in `gated_upside`, `CONDITIONAL`, with the price-series file hash pinned in provenance. £0 when no price series supplied. Never enters P50/P90 cashflows or DSCR.
- **A4 — schema: `ExportArrangement`.** New optional sub-model (site.py additive): `route: enum(none/ppa_export/flexibility_aggregator/unknown)`, `counterparty: str|None`, `aggregator_cut_pct: float|None (0–1)`, `export_tariff_gbp_per_kwh: float|None`, `notes`, provenance. Coherence validators alongside the existing MEC/export-MPAN checks (`validators.py:156-215`). This also carries WS-C's cut.

### WS-B — Real price inputs (historical wholesale, deterministic)

- **B1 — fetch script (off the pricing path).** `python -m gridq.dev.fetch_prices` pulling Elexon **Insights API** (`data.elexon.co.uk`, free, **no API key**) historical HH prices — market index (MID) and/or day-ahead — for an explicit date range, writing a versioned CSV (`timestamp, price_gbp_per_mwh, source, retrieved_at`) compatible with the existing `market_forecasts.py` alias table. Never called inside `gridq run`; the CSV is a hash-pinned input like any other. For Robert Thomas: fetch 2025-04-01→2025-12-08 (the HH window).
- **B2 — HH price-series input path.** Additive optional field (proposal: `OptimisationRequest`/`SiteModel.market_price_series_ref`, mirroring `load_data.half_hourly_file_ref`). Loader: reuse `market_forecasts` parsing; align to load timestamps (reindex, no interpolation unless explicitly allowed — existing `allow_interpolation` discipline); convert £/MWh→£/kWh. Consumed **only** by A3 valuation and the scenario engine — never substituted into the floor objective for a fixed retail tariff.
- **B3 — real tariff bands for the floor.** The floor's price realism for Robert Thomas comes from the **invoice**, not wholesale: extract actual SmartFix unit rate(s), standing charge, DUoS bands, capacity charge into the prefill (day/night labels are already supported by `tariff_calendar`). If the invoice detail isn't extractable → ranked data-ask, run proceeds on what's evidenced.
- **B4 — scenario hook (small).** Allow the scenario engine to reference the price series for stress reporting on the gated-upside line only.

### WS-C — Local flex net of aggregator cut

- **C1 — assumption key.** `local_flex_piclo.aggregator_cut_pct: 0.20`, `status: placeholder`, `confidence: low`, GBP-free ratio, in the next assumption release (follow the gb_v1_4 entry shape). Site-level override lives on `ExportArrangement.aggregator_cut_pct` (A4); precedence: site value → assumption default.
- **C2 — apply net in `LocalFlexPicloStream._price`** (`gb_streams.py:615`): `net = gross × (1 − cut)`. Subject to G2 for where gross/cut appear on the record; v1 fallback = net in `annual_value_gbp`, gross+cut in the stream's inputs/notes for traceability.
- **C3 — app input.** `aggregator_cut_pct` field on `real_form.html` + wizard (step with connection/tariff), parsed in `_site_from_form` (`app.py:120`), **default 20% prefilled**; demo `examples/demo_site/sitemodel.json` gains the `ExportArrangement` preselected (per the meeting: preselected for the demo project).
- **C4 — Piclo investigation (timeboxed, non-blocking).** Piclo Flex has no open public pricing API; access requires registered-provider onboarding (commercial terms = Karim's lane, B002-adjacent). Deliverable: a one-page note + a manual tender-input path (tender window + £/MWh + DNO area → `GateContext.local_flex_tender_open`), so a real tender can be entered by hand today. Do **not** build against a speculative API.

### WS-D — Robert Thomas end-to-end through the real pipeline

- **D1 — ingest the delivered data.** Add `kwh` (and `energy_kwh`) to the HH column aliases (`hh_data.py:44-53`) with tests; place `robert_thomas_haywood_oaks_HH.csv` under `examples/real/` (gitignored, like the prefill); flag data-quality findings as validation warnings, not silent fixes: the all-zero day 2025-06-30, the near-zero-midday/overnight-heavy shape (the open "why?" question — PV vs night-rate operation changes the model; relates C028).
- **D2 — update the prefill.** `examples/real/haywood_oaks_prefill.json` from the site-facts note: supplier SmartestEnergy/SmartFix, postcode NG21 0PE, HH file ref, annual ≈215 MWh (observed-implied, labelled), observed peak 421 kW, company/tariff/MIC/consents fields **left blank where unknown** → they surface as ranked data-asks (that's the product working, not a failure).
- **D3 — wizard parity.** Expose on `new_quote.html` + `_site_from_form`: import MPAN, export MPAN, G98/G99/G100 selects, MEC/ASC, `ExportArrangement` (route + aggregator cut). Keep the wizard honest: unknown = unknown, never defaulted to `approved`.
- **D4 — XLSX parsing.** Backend `read_excel` path (openpyxl) for the already-advertised `.xlsx/.xls` picker, reusing the same column-alias/validation pipeline; reject multi-sheet ambiguity with a clear error naming the sheet found.
- **D5 — connection-capacity helper (estimate, never an input).** From the HH series compute observed peak / p99.5 half-hour kW and render "observed peak 421 kW — confirm MIC/ASC with the DNO/supplier" as a **suggestion + data-ask**. It must never silently populate `mic_kw`; if the user accepts it, it's stored with `needs_review` provenance and the data-ask stays open until evidenced.
- **D6 — bill-PDF prefill (perimeter, human-confirmed) — stretch, last.** v1 is **deterministic text extraction** (pdfplumber + SmartestEnergy layout patterns) → prefill candidates rendered on a confirmation screen the user must approve field-by-field before anything touches `SiteModel`. No LLM required for v1; if an LLM assist is added later it follows the perimeter rule (schema-validated, human-confirmed) and never touches pricing arithmetic. If timeboxed out, the demo story is "upload recorded as provenance; extraction on the roadmap".
- **D7 — the run.** `gridq run` on the assembled Robert Thomas SiteModel: expect **T1**, MILP dispatch, honest verdict. Record IRR/payback/DSCR-availability exactly as computed. Compare against the manual benchmark discipline (`docs/manual_benchmark.md`).

### WS-E — Docs, README, telemetry

- **E1 — new README.** Rewrite root `README.md` post-drive: what runs today (MILP default, export-gated dispatch if G1 landed, real price inputs, local flex net of cut, Robert Thomas as the worked real-site example), the two modes (demo vs real), the honest-status box (still `not_bankable` by design — say why), quickstart, doc map. Keep the current README's "honest status" spirit; this is the meeting hand-out.
- **E2 — refresh `docs/project_status.md`** (stale since 2026-07-02: add D34–D42 + this drive) and `docs/changelog.md` per slice.
- **E3 — governance artefacts:** OPEN_COMMENTS entries for G1/G2 (Olly), decision entries when authorized, `docs/input_data_formats.md` for the new aliases/XLSX, HANDOFF_LOG per session.

## 4. Ordering

1. **A1** (diagnosis — cheap, de-risks everything) ∥ **B1** (price fetch) ∥ **D1/D2** (data in).
2. **A4** (ExportArrangement schema) → **C1/C2/C3** (aggregator cut) ∥ **D3/D4/D5** (intake).
3. **B2/B3** → **D7** first full Robert Thomas run (works without export — behind-the-meter floor only).
4. **G1 decision** → **A2/A3** (export + gated-upside valuation) → re-run D7 with the upside line.
5. **C4, D6** (timeboxed) → **E1/E2/E3** (docs last, once numbers are real).

Milestone for the meeting = end of step 3 (real site runs end-to-end with honest floor economics) + step 5 docs; step 4 adds the export upside story if Olly signs G1 in time.

## 5. Blocking data-asks (the platform will rank these itself; chase in parallel)

1. **Why is load overnight-heavy / midday-zero?** (night-rate operation vs existing PV vs both) — changes the model class (PV ⇒ C028 territory). Ask James.
2. Invoice tariff detail: unit rate(s), standing charge, DUoS bands, capacity/MIC charge, levies.
3. MIC / ASC (kVA) + voltage level + DNO confirmation; any ANM.
4. Export MPAN / G99–G100 status / DNO application state — gates the entire export upside (A2/A3).
5. Company number (credit → rate/tenor mapping).
6. Jan–Mar HH months → T2.
7. Site boundary (Haywood Oaks connection only?).
8. A real battery capex quote — `battery_capex.installed_cost` is a £500k/MW placeholder; IRR is only as honest as capex.

## 6. IRR honesty (read before demoing a number)

15–20% IRR is the *hope*, not a constraint. Structural headwinds on this site: annual spend ≈ £73k (215 MWh @ ~34p all-in) but the load sits mostly in cheap bands already (overnight/green), red-band coincident load is thin, and without export the battery can only displace load that exists. The honest levers, in order: real tariff bands (B3), DUoS red avoidance on the evening ramp, local flex **net of cut** when a tender is actually open, CM conditional upside, and — decision-gated — export at wholesale HH prices as gated upside. If the floor IRR comes out at 6%, the meeting number is 6% with the upside lines shown separately and labelled. Nobody tunes a placeholder to reach 15%.



## 8. Drive acceptance

- Robert Thomas runs `gridq run` end-to-end from its own data at T1 with the MILP, producing spec + underwriting pack + proposal, all warnings/data-asks visible, deterministic (same run_id on re-run).
- Demo project carries preselected consents/MPAN/ExportArrangement/aggregator-cut fields.
- Local flex prices net of the cut with gross/cut traceable.
- HH wholesale series ingestible and hash-pinned; export upside valued only when consents cohere and only as gated upside (if G1 landed).
- New README + refreshed project_status; full quality bar green; every schema change additive + hash-stable; zero invented numbers.

---

## 9. Owner steer — consents are frontend-declared, never a backend block (Olly, 2026-07-17)

Direction given mid-drive; it supersedes the consent-gating framing in §2/§3 where they conflict:

1. **Demo + Robert Thomas get every consent by fiat** — G98/G99/G100 approved, import + export MPAN present, MIC = MEC (import and export capacity equal), ASC set. Implemented **data-driven** (set the two fixtures to all-approved + MEC=MIC), NOT by branching the core on site id (the architecture forbids `market=="GB"`-style core branches; a site-name branch is the same sin). The normal consent logic then enables full export for them. This **re-pins the demo golden hash** (an honest input change) and, once export + prices are wired, adds their gated-upside export line — expected and documented.
2. **Consents are a first-class FRONTEND input step, never a backend hard-stop.** Add a dedicated **"Consents & grid arrangements" screen** to the data-input flow (wizard) capturing: G98/G99/G100 status; import MPAN; export MPAN; MIC; MEC; ASC; DNO region; export route; and the **agreed rates where appropriate** (export tariff £/kWh, aggregator cut %, local-flex agreed rate). These flow through `_site_from_form` → `ConsentState` / `ConnectionDetails` / `ExportArrangement`. The backend **reflects declared state and raises ranked data-asks/warnings — it must never hard-block underwriting on consent state.** Audit and remove/soften any backend path that stops a run for missing consents (keep genuine structural guards like "no HH data / no max_import" — those are data-existence, not consent).
3. **Visuals & menu titles updated**: the new wizard step gets a design-system-compliant eyebrow/title (mono uppercase, e.g. "0X · CONSENTS & GRID"), the step indicator/nav reflects it, and any run/spec/proposal surface that shows consent or export status is updated. Design/execution routed through a **Fable design pass** (per owner instruction) → `docs/issues/018-D8-consents-screen-design.md`.

This resolves the R6 consent-predicate ambiguity in [018-A2A3-export-design.md](018-A2A3-export-design.md): the frontend declares consents; the engine reads declared state; demo/RT declare everything.
