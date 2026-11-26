# Wave 3 Harmony Audit

**Date:** 2026-06-23 · **Auditor:** harmony check (read-only audit + tiny adapter fix, integration branch `agent/integration`) · **Scope:** do all Wave 3 lanes — value-stack/real value functions (W3-1), dispatch + sizing (W3-2), engine conformance + `optimisation_contracts` removal (W3-4), outputs/M8 (W3-3), finance/credit, telemetry, and the demo app (W3-7a/b) — fit together as one coherent system before Wave 4, with no contract drift from the parallel agents?

## Verdict

**GO — the shared core is coherent and green; Wave 4 can start.** Every lane builds against the *same* frozen schemas, no shared model or enum is redefined, no frozen contract was mutated except by recorded decisions (D12/D13/D15/D16/D17), the deterministic core stays market-agnostic, the priced floor is still `{FIRM, CONTRACTED}`, and nothing invents a number. The full quality bar passes on the integrated tree:

| Check | Result |
|-------|--------|
| `pytest` | **417 passed**, 1 warning |
| `ruff check .` | **All checks passed** |
| `ruff format --check .` | **clean** |
| `mypy` | **Success — no issues in 86 source files** |
| `gridq.dev.review_gates` | **GREEN** (0 blocking, 0 warnings) |

The 1 pytest warning is a **third-party** `StarletteDeprecationWarning` (httpx/`TestClient`) from the optional app lane — not a product issue (see *Transient / external*, below). One **tiny adapter fix** was applied (index files out of step with the merged package set — §4); no lane logic was touched.

---

## The six checks

### 1. Merge integrity — ✅ CLEAN
All 15 expected packages are present and non-empty under `src/gridq/`: `schemas, ingestion, assumptions, provenance, engine` (incl. `conformance.py`), `value_stack, scenarios, finance, quotes, outputs, dispatch, sizing, telemetry, dev, app` (+ `cli.py`). **No VCS conflict markers** (`<<<<<<<`/`=======`/`>>>>>>>`) anywhere in `src/`, `tests/`, `docs/`. No orphaned/empty stubs — every `__init__.py` is populated with an `__all__`. `gridq.optimisation_contracts` is **removed** (W3-4/D16); the only residual mentions are stale build-generated `src/gridq.egg-info/{PKG-INFO,SOURCES.txt}` (regenerate on next build — §*Transient*).

### 2. Single source of truth — ✅ CLEAN
Every shared model (`SiteModel`, `OptimisationRequest/Result`, `SolverMetadata`, `ValueStreamResult`, `FinancialCase` + sub-models, `QuoteRecord`/`StatusHistoryEntry`, `TelemetryEvent`, `LoadDataSummary`/`ConnectionDetails`/`ConsentState`/`Counterparty`/`TariffStack`/`PhysicalGateStatus`, `GridQModel`/`Provenance`) is defined **exactly once** in `src/gridq/schemas/` and imported everywhere. `ForecastReference` is the one deliberate exception (lives in `scenarios/forecast_reference.py` — a scenarios artifact, not a core schema; imported by quotes/outputs from there). Lane-local types (`ValueStream`/`GateResult`/`GateStatus`/`GateContext` in `value_stack/interface.py`; `DispatchProblem`/`DispatchResult` in `dispatch/contract.py`; `SearchSpace`/`SizingObjective` in `sizing/`; `RunContext` in `provenance/`; `ScenarioName`, `AssumptionStatus`, `Severity`, `ManualQuoteType/Outcome`) are correctly scoped and **none shadows a shared name**.

### 3. No mutation of frozen contracts — ✅ CLEAN
Enum members are unchanged everywhere — verified against `docs/03_data_contracts.md` §7: `Firmness {firm,contracted,conditional,excluded}`, `SolverStatus {baseline,mock,optimal,feasible,infeasible,insufficient_data}`, `MarketStream` (9), `QuoteMode` (6), `DataTier` (3), `BankableStatus` (3). No lane added/renamed/reordered a member or monkeypatched an enum. The frozen schemas (`OptimisationResult`, `SiteModel`, `QuoteRecord`, `FinancialCase`, `ValueStreamResult`) are field-identical to the contract doc — **no Wave 3 lane added a field**. The additive seams that *were* discussed are correctly **deferred, not landed**: dispatch energy stays the lane-local `DispatchResult.annual_charge_kwh/annual_discharge_kwh` hook (not on `OptimisationResult`) per **D13**; gate inputs stay off `SiteModel` (generic `GateContext` instead) per **D12**; bill/savings stay a labelled-pending seam off `FinancialCase` per **D15**. Provenance is intact end-to-end: `OptimisationRequest.assumption_version` → `build_run_context` → `RunContext(run_id, input_hash, assumption_version, code_version, schema_version)` → flat `QuoteRecord.input_hash/assumption_version/code_version`; `SolverMetadata` carries the engine-level versions (conformance now *asserts* these are non-empty).

### 4. Indexes / imports / naming — ✅ CLEAN (one tiny fix applied)
All packages import cleanly; **no circular imports** (the `scenarios → ingestion` forecast loaders stay behind a local/`TYPE_CHECKING` import to keep the scenarios path pandas-free; `quotes → finance/engine` and `outputs → record` are one-way). Package naming is **settled, not ambiguous**: `engine` is the outer `OptimisationEngine` seam (+ baseline + conformance), `dispatch` the inner dispatch-solver seam (M3), `sizing` the M4 outer loop; the real MILP will land as `gridq.optimisation` implementing the same Protocols (D16/D17; `engine/README.md`). `gridq.app` imports behind the optional `app` extra.
→ **Tiny fix applied (this pass):** `cli.py` `_MODULES` and `tests/test_imports.py` `SUBPACKAGES` had not been updated for the five newer packages. Added `dispatch`/`sizing`/`telemetry`/`dev`/`app` to the `gridq info` table, and `dispatch`/`sizing`/`telemetry`/`dev` to the import smoke-test (`app` is intentionally left to `tests/test_app_smoke.py` so the core scaffold test stays importable without the optional extra). Index/adapter files only — no lane logic touched.

### 5. Green bar — ✅ GREEN
`pytest` **417 passed** (1 benign third-party warning), `ruff check .` clean, `ruff format --check .` clean, `mypy` clean (86 files), `gridq.dev.review_gates` GREEN. No real failures. The transient repo-wide "ruff RED" notes in earlier mid-build `HANDOFF_LOG`/`TEAM_SYNC` bullets (one lane seeing another's half-finished files) are **resolved** — the integrated tree is clean.

### 6. Gating discipline intact — ✅ CLEAN
**No `market == "GB"` branch in the core** — the only matches are *docstrings asserting the rule* in `value_stack/{__init__,interface,streams,registry,gb_streams}.py`; GB specifics live solely in `gb_streams._price` hooks + `assumptions/gb_v0_1.yaml` content. **Priced floor = `{FIRM, CONTRACTED}`** — the same frozenset is mirrored in `finance/cashflows.py` (`_PRICED_FIRMNESS`, gating both `revenue_by_stream` and `dispatch_cost_total`), `engine/conformance.py` (`PRICED_FLOOR_FIRMNESS`, with a "teeth" check that a `contracted` gated stream over-counts), and `outputs/viewmodel.py` (`_FLOOR_FIRMNESS`); gated upside is barred from firm at three layers (schema validator, `GatedUpsideStream._gate`, conformance). W2-2's `!= EXCLUDED` → `{FIRM, CONTRACTED}` fix is intact; no lane reintroduced it. **No invented numbers** — every £ traces to `gb_v0_1` × dispatched/known quantities; missing inputs become *unavailable*/warnings/data-asks (finance raises `InsufficientInputsError` rather than assume a system; value functions return `0.0` + a "not priced: <key> missing" note); the M8 HTML renderer is **arithmetic-free by an AST test**, prose is categorical-only, placeholders stay loudly labelled, and the `review_gates` banned-string scan still covers the new core modules. `BaselineEngine.BASELINE_POWER_FRACTION` and `dispatch.SimpleSolver` are labelled integration heuristics (never library assumptions; results always `not_bankable`).

---

## Tiny shared-contract fixes applied

- **`src/gridq/cli.py` `_MODULES`** + **`tests/test_imports.py` `SUBPACKAGES`** — brought the module index and the import smoke-test in step with the merged package set (added `dispatch`, `sizing`, `telemetry`, `dev`, `app`; `app` excluded from the mandatory smoke-test as an optional extra). Index/adapter files only; the bar stays green (417 passed). No lane module was rewritten.

## Remaining mismatches

### None blocking. Hygiene notes (no product impact)
- **Uncommitted working-tree delta** atop `cd981df`: the app lane (`src/gridq/app/`, `tests/test_app_smoke.py`), conformance refinements, and several docs are staged-in-tree but uncommitted. Functionally green; flagged for **merge hygiene** — commit the delta so the recorded history matches the audited tree.
- **Stale `src/gridq.egg-info/{PKG-INFO,SOURCES.txt}`** still name `optimisation_contracts/` — build-generated metadata that regenerates on the next `pip install -e`; not a live import. No action.

### Routed to owning lanes (decision/seam follow-ups, not drift)
- Additive `OptimisationResult.annual_charge_kwh/annual_discharge_kwh` lands **with M3** (Olly, conformance-tested) — D13; until then finance levies-on-charge / price-to-target stay *unavailable*, by design.
- `GateContext` populated (CM deliverability / Piclo tender / portfolio-MW) when the producing lanes land — D12 (gated by C007).
- Bill/savings fields on the finance/quote record — D15 / C017, when the demo needs real before/after numbers.

### Blocking shared-contract issue
- **None.** No frozen contract is violated; all lanes build on the same schemas; the suite is green. B001 (M9 credit-tool ownership) and B002 (RTM partner/API) remain **external** blockers with v1 workarounds — they do not block Wave 3 coherence or starting Wave 4.

---

## Transient / external (no action)
- The single `pytest` warning is a `StarletteDeprecationWarning` from `fastapi.testclient` (httpx) — a third-party deprecation surfaced by the optional app lane's tests, not a GridQ defect.
- Older "repo-wide ruff/mypy RED" lines inside historical `HANDOFF_LOG`/`TEAM_SYNC` lane bullets were mid-build states across the shared worktree; the integrated tree is clean now.

---

## Go / No-go

**GO.** The shared core is coherent enough to start Wave 4: single source of truth holds, no frozen contract drifted, the green bar is clean across 86 source files / 417 tests, gating + no-invented-numbers invariants are intact, and the only fix needed was an editorial index sync (applied). Recommend committing the uncommitted working-tree delta before Wave 4 so history matches the audited state.
