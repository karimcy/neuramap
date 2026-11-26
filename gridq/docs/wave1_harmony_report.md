# Wave 1 Harmony Audit

**Date:** 2026-06-20 · **Auditor:** harmony check (read-only, integration worktree `agent/ingestion`) · **Scope:** does everything produced in Wave 1 fit together as one coherent system?

## Verdict

**Wave 1 is coherent and green.** All lanes build against the *same* shared schemas, use the same names and meanings, invent no data, and keep placeholders labelled. The full quality bar passes across the integrated tree:

| Check | Result |
|-------|--------|
| `pytest` | **281 passed** |
| `ruff check .` | **All checks passed** |
| `ruff format --check .` | **81 files already formatted** |
| `mypy` | **Success — no issues in 60 source files** |

The "repo-wide `ruff`/`mypy` RED" warnings in older `TEAM_SYNC`/`HANDOFF_LOG` bullets were **transient mid-build states** (one lane seeing another lane's half-finished files) and are now **resolved** — the integrated tree is clean. No blocking shared-contract issue was found.

The mismatches that remain are **latent** (they produce no wrong output today and break no frozen contract) and each lives in one lane's module, so per the audit rules they are routed to Wave 2 / the owning lane rather than fixed here.

---

## The 10 checks

### 1. Shared schemas use the same names and meanings — ✅ CLEAN
- No shared model **name** (`SiteModel`, `OptimisationResult`, `ValueStreamResult`, `FinancialCase`, `QuoteRecord`, `TelemetryEvent`) is re-defined outside `src/gridq/schemas/`. Every lane imports them from `gridq.schemas`.
- Shared enums (`DataTier`, `MarketStream`, `Firmness`, `BankableStatus`, `ConsentStatus`, `PriceStructure`, `QuoteMode`, `SolverStatus`) are defined once in `schemas/enums.py` and imported everywhere.
- The other `StrEnum`s found (`ScenarioName`, `GateStatus`, `ManualQuoteType/Outcome`, `AssumptionStatus`, `Severity`) are **lane-local concepts** — none shadows a shared enum.
- Code consistently uses the **implemented** name `value_streams` (the data-contract doc's older `value_by_stream` appears nowhere in code — tracked by C002).

### 2. Real half-hourly data input support — ✅ INTACT, end-to-end
`load_half_hourly_csv` → `validate_half_hourly_coverage` / `summarise_half_hourly_data` (duplicates, missing intervals, negatives, coverage are **counted, never filled**; kWh→kW is a labelled transform; row count preserved) → `to_load_data_summary()` maps into the **shared** `LoadDataSummary` using the exact field names (`half_hourly_rows`, `start_date`, `end_date`, `max_import_kw`, `max_export_kw`, `missing_intervals`) → `SiteModel.load_data`. Value-stack then reads `load_data.max_import_kw` / `has_hh_coverage`. The names line up the whole way; an ingested HH series correctly yields `has_hh_coverage = True`, opening the MIC/ASC gate.

### 3. Real market forecast input support — ✅ INTACT (two representations; one naming follow-up)
Both forecast paths support real Modo/wholesale files and never invent prices:
- **Ingestion** `MarketForecast` + `load_market_forecast_{csv,json}` (+ `hash_market_forecast`), hashed via `gridq.provenance.hashing`; opt-in **labelled** interpolation that never extrapolates.
- **Scenarios** `ForecastReference` (`build_forecast_reference` hashes a *real* file; a malformed hash is rejected; `verified` flag; an unverified ref forces the run not-bankable).

→ Wave 2 follow-up (naming, see §below): the two modules name the forecast-file hash differently (`content_hash`/`file_sha256` vs `ForecastReference.input_hash`), and there is no converter wiring `ingestion.MarketForecast` → `scenarios.ForecastReference` yet.

### 4. No module invents or hallucinates data — ✅ CLEAN
- **value_stack** returns `0.0` revenue (the gate decides priceability, not money); missing data → missing requirement.
- **finance** invents no capex/revenue/debt — a missing input is an *unavailable* line + warning; DSCR/price/levies are **UNAVAILABLE** in v1, not guessed.
- **ingestion** counts gaps/negatives, never fills; interpolation is opt-in, labelled, and never extrapolates.
- **scenarios** assert no invented spread; forecast-backed prices require a hash-pinned reference.
- **dev/review_gates** runs a banned-string scan of core modules for invented-number / "fake bankable" smells (test green).

### 5. Placeholder assumptions clearly labelled — ✅ CLEAN
`gb_v0_1.yaml` is entirely `placeholder`/`needs_review` (`confidence: low`). Finance warns per non-confirmed assumption and marks the case **not bankable**; scenarios mark placeholder-weighted sets **NOT_BANKABLE**; value-stack gates stay conditional until an assumption is `confirmed` and positive. Nothing placeholder-driven is presented as bankable.

### 6. Provenance & traceability fields line up — ✅ MOSTLY CLEAN (one overload → Wave 2)
All hashing goes through `gridq.provenance.hashing` (`hash_file` / `hash_json`). `build_run_context` → `RunContext` pins `run_id` + `input_hash` + `assumption_version` + `code_version` + `schema_version`; finance stamps it at run assembly. Producers that sit *before* run assembly (value-stack, scenarios, ingestion) correctly do not mint a `RunContext`.
→ One wrinkle: the term **`input_hash` is overloaded** — the run-level determinism pin (`RunContext`/`QuoteRecord.input_hash`) vs `ForecastReference.input_hash`, which is actually a *forecast-file content hash*. Same item as §3. Latent; Wave 2.

### 7. Quote / telemetry / status names consistent — ✅ CLEAN
`QuoteRecord(quote_mode, data_tier, input_hash, assumption_version, code_version, optimisation_result, financial_case)` and `TelemetryEvent(event_id, quote_id, event_type, data_tier, human_minutes, contributor, installer_iterations, missing_data_count, notes)` are used coherently. `manual_benchmark` converts to the shared `TelemetryEvent` with **exactly** those field names and `event_type="manual_benchmark"` (C011 tracks whether that should become a first-class flag). Minor: `ManualQuoteType` values (`indicative`/`underwritten`/`proposal`/`other`) partially overlap `QuoteMode`, but model a different thing (artifact type, not lifecycle) — a glossary note suffices; safe for Wave 2.

### 8. Value-stack gates and finance outputs use the same shared concepts — ✅ SHARED VOCAB; one latent semantic gap → Wave 2
Both lanes speak `MarketStream` + `ValueStreamResult` + `Firmness`. They agree the priced floor excludes gated upside.
→ **Latent gap:** value-stack (and `docs/03_data_contracts.md`) define the priced floor as `firmness ∈ {firm, contracted}` (conditional **excluded**); finance's `revenue_by_stream` uses `_is_priced = firmness != EXCLUDED`, which would also count **conditional**. Harmless today (the baseline engine emits only `excluded` value streams + a separate `gated_upside` list, all at £0), but when M3's real engine emits *valued conditional* streams, finance would over-count. → Wave 2 follow-up for the finance lane to align `_is_priced` to `{firm, contracted}`.

### 9. Dev tooling / handoff / open comments / blockers / team-sync agree on state — ⚠️ ONE STALE FILE (fixed in this pass)
`TEAM_SYNC.md`, `HANDOFF_LOG.md`, `OPEN_COMMENTS.md` (C001–C013, unique ids) and `BLOCKERS.md` (B001/B002) agree on the Wave 1 state and already capture the cross-lane seams.
→ **`docs/project_status.md` was stale** — it still showed Milestone 3 "in progress / finance+scenarios next", ingestion "◻️", and a "Next actions (Milestone 1)" block, contradicting the five Wave 1 lanes that are done and green. **Updated in this pass.** The "repo-wide RED" lines inside older `TEAM_SYNC` lane-bullets are now stale (everything is green); a Wave 1 harmony note has been added rather than rewriting each historical bullet.

### 10. Test names and fixtures reflect the contract names — ✅ CLEAN
One `tests/conftest.py` (`make_site` builds a `SiteModel` with the real schema field names). Test modules map 1:1 to source modules (`test_value_stack_*`, `test_finance_*`, `test_ingestion_*`, `test_scenario_*`, `test_market_forecast_loader`, `test_manual_benchmark`, `test_review_gates`, `test_skill_update_protocol`, `test_schemas`, …). 281 tests pass.

---

## Tiny shared-contract fixes applied

**None required.** Wave 1 is internally consistent and the full quality bar is green; no frozen contract (`SiteModel` / `OptimisationResult` / stream registry) is violated. The mismatches below are **latent** (no current wrong output) and each lives in another lane's working module, so per the audit rules ("do not refactor working modules unless a tiny compatibility fix is needed; do not chase another lane's refactor unless it breaks a shared contract") they are routed to Wave 2 rather than edited here. The only changes made are **documentation harmony**: this report, the stale `project_status.md`, the team-sync/handoff/changelog updates, and two new OPEN_COMMENTS (C014, C015) routing the follow-ups to their lanes.

## Remaining mismatches

### Safe to leave for Wave 2 (cosmetic / latent / decision-only)
- **`ManualQuoteType` vs `QuoteMode` value overlap** (`indicative`/`underwritten`) — distinct concepts (artifact type vs lifecycle state); a glossary note would settle it.
- **Doc-vs-code reconciliation (C002/C003/C004/C005):** `03_data_contracts.md` still describes kVA / `value_by_stream` / `chosen_spec` / old `meta`; code is authoritative (kW, `value_streams`, flat `OptimisationResult`, `quote_mode`, `SolverStatus.INSUFFICIENT_DATA`). Doc catch-up, not a code break.
- **Labelled structural parameters (C009/C013):** `DEFAULT_HORIZON_YEARS` home and the T2 completeness floor — team decision on whether to version them in X.1.

### Needs a follow-up prompt (real harmony work, owning lane)
- **Finance "priced" semantics (check 8):** align `cashflows._is_priced` to `firmness ∈ {firm, contracted}` so conditional streams are not counted in the priced floor once a real engine emits valued conditional streams. *(finance lane — new C014)*
- **Forecast provenance naming + seam (checks 3/6):** rename `ForecastReference.input_hash` → `content_hash` (matching ingestion; stop overloading the run-level `input_hash`), and decide the one converter `ingestion.MarketForecast` → `scenarios.ForecastReference`. *(scenarios + ingestion lanes — new C015)*
- **SiteModel missing gate inputs (C006):** decide the home for CM deliverability / Piclo tender availability / portfolio-MW (on `SiteModel` vs a separate market/portfolio context) — needed before CM/Piclo/BM/ancillary can ever open. *(shared contract — value-stack + schema)*
- **DispatchEnergy seam (C010):** `OptimisationResult` carries no per-year charge/discharge volume, so finance's levies-on-charge and price-to-target stay *unavailable* until M3 supplies it. *(dispatch + schema)*

### Blocking shared-contract issue
- **None.** No frozen contract is violated; all lanes build on the same schemas and the suite is green. B001 (M9 credit-tool ownership) and B002 (RTM partner/API) remain **external** blockers with v1 workarounds — they do not block Wave 1 coherence.

---

## What is purely another lane's transient refactor (no action)
- Older `HANDOFF_LOG`/`TEAM_SYNC` notes of "ruff RED in `ingestion/`/`finance/`/`dev/`", a scenarios `ForecastReference` test failure, and a finance `F821 _result_is_bankable` — all were mid-build states across the shared worktree. They are **resolved**: `_result_is_bankable` is defined, the scenarios hash test passes, and `ruff`/`mypy`/`pytest` are clean tree-wide.
