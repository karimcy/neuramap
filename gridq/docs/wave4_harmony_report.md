# Wave 4 Harmony Audit & Wave 5 Readiness Gate

**Date:** 2026-06-24 · **Auditor:** harmony check (read-only audit + tiny doc fixes, integration branch `agent/integration`, Olly) · **Scope:** with all Wave 4 lanes in the integration tree — real dispatch MILP (W4-1/D22), real sizing objective (W4-2/D21), `gb_v1_0` sourced assumptions (W4-3/D19), Modo/scenarios forecast wiring (W4-4/D20), counterparty credit→rate/tenor (W4-5/D18), and integration — do they fit together as one coherent, honest system, and is the real engine genuinely ready for Wave 5 (finish + public demo)?

Method: an 8-dimension read-only audit (one agent per dimension) cross-checked against first-hand reads of the go/no-go-critical files and a full run of the quality bar via `/root/.venv`.

## Verdict

**GO — the real engine is the coherent, deterministic default; gating and assumption honesty are intact; the bar is green; a real-data end-to-end run produces all three artifacts.** No Wave-4 blockers. The MILP (`gridq.optimisation.MilpEngine`) is the `gridq run` default, is deterministic, populates dispatch energy, and passes the W3-4 conformance suite; the placeholder stays a loudly-labelled fallback. Every shared model/enum is defined once, no frozen contract was mutated except by recorded decisions (D18–D22), the core stays market-agnostic, the priced floor is still `{FIRM, CONTRACTED}`, and **nothing unsourced is presented as bankable** — so every output stays `not_bankable` on the placeholder libraries, honestly.

| Check | Result |
|-------|--------|
| `pytest` | **515 passed**, 1 warning (≈26 s; incl. a full-year 17,472-HH MILP solve at ≈2.6 s) |
| `ruff check .` | **All checks passed** |
| `ruff format --check .` | **clean** (139 files) |
| `mypy src` | **Success — no issues in 94 source files** |

The 1 pytest warning is a **third-party** `StarletteDeprecationWarning` (httpx/`TestClient`) from the optional `app` lane — not a product issue. Solver backend: **linopy 0.5.8 / HiGHS (`highspy`)**, both installed.

Two **documentation-only** fixes were applied this pass (§ Tiny fixes); **no lane logic was touched.**

---

## Part A — Harmony & integrity

### A1. Merge integrity — ✅ CLEAN
All Wave 4 lanes are present, non-empty and coherent under `src/gridq/`: `optimisation/` (MILP — `engine.py`, `milp_solver.py`, `valuation.py`, `schedule.py`, `tariff_calendar.py`, `_assumptions.py`), `dispatch/` (`contract.py`, `energy.py`, `simple_solver.py`), `sizing/` (`sizer.py`, `search_space.py`, `objective.py`, `underwrite.py`), `scenarios/` (+ `forecast_prices.py`, `forecast_reference.py`), `finance/` (+ `credit.py`), `schemas/counterparty.py`, `quotes/`, `outputs/`, `app/`; `assumptions/gb_v1_0.yaml`. **No VCS conflict markers** (`<<<<<<<`/`=======`/`>>>>>>>`) anywhere in `src/`. No code stubs — the 73 `TODO|FIXME|STUB|placeholder|hack` hits are all docstrings/assumption-status labels, not unimplemented code. `import gridq` succeeds; `MilpEngine()` instantiates; all cross-lane imports resolve. `docs/module_ownership.md` and `docs/parallel_development_plan.md` map cleanly to what is on disk.

### A2. Real engine (MILP) — ✅ CLEAN
- **Default on the product path.** `gridq run` wires `engine=MilpEngine()` ([cli.py:196](../src/gridq/cli.py#L196)); the docstring and decision **D22(5)** confirm the MILP is the run default. `BaselineEngine`/`SimpleSolver` are loudly labelled **integration-only** fallbacks ([engine/baseline.py:1-7](../src/gridq/engine/baseline.py#L1-L7), [dispatch/simple_solver.py:1-9](../src/gridq/dispatch/simple_solver.py#L1-L9)) that always emit `implementation_level=baseline` / `bankable_status=not_bankable`.
  - *Recorded nuance (not a defect):* by **D22(5)** the library helper `generate_quote`/`run_quote_pipeline` keeps `BaselineEngine` as its **no-arg signature default** ("lowest blast radius") — the production entrypoint passes `MilpEngine` explicitly. Wave 5 may revisit flipping the no-arg default (see `wave5_scope.md`); it is deliberate today, not an oversight.
- **Deterministic.** `MilpDispatchSolver` runs HiGHS single-threaded, fixed `random_seed`, deterministic presolve, IPM+crossover, all outputs rounded ([milp_solver.py:247-268](../src/gridq/optimisation/milp_solver.py#L247-L268)); a tie-break regulariser makes the optimum unique. Determinism is asserted by tests (`test_optimisation_milp_solver.py`, `test_optimisation_engine.py`) and by `SizingRun.fingerprint()` (timestamp-free).
- **Populates `DispatchEnergy`.** The engine recovers the selected candidate's annual charge/discharge volumes and exposes them via the duck-typed `selected_dispatch_energy()` hook, threaded into finance by the additive optional `generate_quote(dispatch_energy=…)` ([engine.py:143-180](../src/gridq/optimisation/engine.py#L143-L180), [dispatch/energy.py](../src/gridq/dispatch/energy.py)) — **without** promoting a field onto the frozen `OptimisationResult` (D13 honoured).
- **Conformant.** Both `MilpEngine` and `BaselineEngine` pass the W3-4 conformance suite (`tests/test_optimisation_contract_conformance.py`, 11 tests; engine module 30 tests green) — same bar, no engine-specific branch, no silent drift.

### A3. Single source of truth / no mutation — ⚠️ ONE DOC DRIFT (fixed this pass)
- **SSOT held.** Each shared model is defined exactly once — `SiteModel` ([site.py](../src/gridq/schemas/site.py)), `OptimisationResult` ([optimisation.py](../src/gridq/schemas/optimisation.py)), `QuoteRecord` ([quote.py](../src/gridq/schemas/quote.py)), `FinancialCase` ([finance.py](../src/gridq/schemas/finance.py)); no duplicate/redefined models. 13 enums are centralised in [schemas/enums.py](../src/gridq/schemas/enums.py), all `StrEnum` (unknown values are validation errors); subsystem enums (`GateStatus`, `AssumptionStatus`, `ManualQuoteType`) are intentionally lane-local, not contracts.
- **No frozen-contract mutation except by decision.** Every model is `extra="forbid"`; gated-upside membership and firmness rules are enforced by validators at three layers. ("Frozen contract" here means the versioned platform↔engine coupling point, enforced by `SCHEMA_VERSION` + conformance, **not** Pydantic `frozen=True` — a deliberate convention, see [03_data_contracts.md](03_data_contracts.md).)
- **Provenance intact.** `OptimisationResult.solver_metadata` carries `(seed, assumption_version, code_version, model_version)`; `QuoteRecord` carries flat `(input_hash, assumption_version, code_version)`; `RunContext.run_id` is **deterministic** (hash of input+versions, not wall-clock) — verified by `test_provenance.py`.
- **The one drift (now fixed in docs):** `FinancialCase.equity_irr` (additive optional, added W4-2 / **D21**, flagged to Olly in **C022**) existed in code but was **absent from `docs/03_data_contracts.md §4`**. Per the doc's own rule ("the code wins and this doc is a bug") the doc was the bug. **Fixed this pass** — see § Tiny fixes. This closes the documentation side of C022.

### A4. Assumption honesty — ✅ CLEAN
`assumptions/gb_v1_0.yaml` = **25 entries: 0 `confirmed` · 10 `needs_review` · 15 `placeholder`** (locked by `tests/test_assumptions.py`). The two `needs_review` promotions carry real in-repo citations (Neura business plan: `battery_capex.installed_cost` £500k/MW, `financing.target_equity_irr_pct` 12%); the 15 placeholders state "No citable source identified" / "(gated)" — **no number invented**. The false `round_trip_efficiency` citation in `gb_v0_1` was corrected to "NOT sourced" in `gb_v1_0` (D19). **No unsourced value can be presented as bankable:** any non-`CONFIRMED` assumption used in finance appends a warning and forces `bankable=False` ([finance/cashflows.py](../src/gridq/finance/cashflows.py) `_use`/`_uses_unconfirmed` → `financial_model.py`). `docs/assumptions_to_source.md` is current (gaps ranked, sources named; the one noted exception is the in-flight credit/gearing key set, by coordination note). Because nothing is bankable, **every output is honestly `not_bankable`.**

### A5. Gating discipline — ✅ CLEAN
- **Priced floor = `{FIRM, CONTRACTED}` only** — `_PRICED_FIRMNESS` in [finance/cashflows.py](../src/gridq/finance/cashflows.py), mirrored in `engine/conformance.py` and `outputs/viewmodel.py`.
- **P415 / BM / ancillary are gated upside (B002)** — `GATED_UPSIDE_STREAMS` ([schemas/market.py:12-18](../src/gridq/schemas/market.py#L12-L18)); the three GB gated streams return `GateStatus.CONDITIONAL` unconditionally ([value_stack/gb_streams.py](../src/gridq/value_stack/gb_streams.py)), barred from FIRM at three layers (stream `_gate`, `GateResult` validator, `ValueStreamResult` validator). The RTM gate only opens on a `confirmed` RTM assumption with value > 0 — `gb_v0_1`/`gb_v1_0` keep these `needs_review`/£0.
- **Credit is a stub/manual mapping (B001)** — [finance/credit.py](../src/gridq/finance/credit.py) `map_credit_to_rate_tenor` is a pure deterministic band→(rate,tenor) lookup from a versioned assumption; no API, no scoring, no invented band; the Creditsafe tool plugs in upstream by producing the `CreditStatus`.
- **No `market == "GB"` branch in core** — zero conditionals in `optimisation/`, `finance/`, `dispatch/`; the only matches are rule-asserting docstrings. GB is content (`gb_streams._price`, `tariff_calendar`, `assumptions/*.yaml`) composed via the registry, never a core branch.

### A6. Green bar — ✅ GREEN
See the verdict table. 515 tests pass, ruff + format + mypy clean. No real or transient failures on the integrated tree.

---

## Part B — Real-data end-to-end confirmation — ✅ CONFIRMED (packaging caveat)

A genuine real-data run is demonstrable today: `gridq run <site>.json --hh <real_hh.csv> --forecast <real_modo.csv>` drives a **real SiteModel + real half-hourly meter CSV + real Modo forecast** through the **MILP engine** → finance → a stamped `QuoteRecord` + telemetry, then `generate_for_run` writes all **three artifacts**:

1. **Underwriting pack** — `memo.md` (capital-facing)
2. **Customer proposal** — `proposal.md` (plain-language)
3. **HTML report** — `report.html` (self-contained combined view)

Proven by `tests/test_cli_run.py` (real inline HH + Modo CSVs; deterministic `quote_id`/`input_hash`; all three files written) and a live audit run. In the outputs, **firm/contracted floor is clearly separated from gated upside** (e.g. floor £45,982.56 across PPA/tariff/DUoS/MIC vs gated upside £4,246.50 across CM/local-flex/wholesale/BM/ancillary, with per-stream unlock conditions); DSCR, customer bill and counterparty render as **labelled-unavailable, never guessed** ([outputs/viewmodel.py](../src/gridq/outputs/viewmodel.py)); the HTML is arithmetic-free (AST-tested) and prose is LLM-free/categorical-only; a NOT-BANKABLE banner is honest on the placeholder libraries.

**Caveat (→ Wave 5):** there is **no committed real dataset** — real inputs live under a **gitignored `data/`** path (correct for third-party data privacy; `examples/site_t2.json` references `data/site_000123/hh_2025.parquet`, not committed). There is also **no packaged, labelled demo dataset** in `examples/` for an unauthenticated visitor (the `examples/README.md` still describes a never-built `demo_site/`). `examples/real/` does not exist and is not referenced anywhere in the repo — it is an expectation in the gate brief, not a repo convention. Packaging a clearly-labelled demo dataset is Wave 5 scope.

---

## Part C — Wave 5 readiness pre-req verification (capture; full scope in `wave5_scope.md`)

- **3D battery-sizing surface pre-req — ⚠️ PARTIALLY MET (Wave 5 fix logged).** The optimiser **already retains the full evaluated grid**: `SizingRun.candidates_evaluated: list[CandidateEvaluation]`, each candidate carrying `power_kw`, `duration_hours`, `levered_return_pct` (equity IRR), `project_irr_pct`, `min_p90_dscr`, `passes_covenant` — exactly the axes, feasible-region and optimum the surface needs; `SizingRun.fingerprint()` even serialises them ([sizing/sizer.py:75-128](../src/gridq/sizing/sizer.py#L75-L128)). **But** `MilpEngine.solve()` returns only the chosen candidate's frozen `OptimisationResult` ([engine.py:245-255](../src/gridq/optimisation/engine.py#L245-L255)); the `SizingRun` is discarded at the seam and never reaches the `QuoteRecord`/outputs. So the surface is **not plottable from persisted data today.** This is a **Wave 5 prerequisite fix, not a Wave-4 blocker**: expose the grid via a small additive accessor mirroring the existing `selected_dispatch_energy()` hook (e.g. `MilpEngine.last_sizing_run()`), and/or persist a `sizing_grid` onto the run record under a recorded decision + `SCHEMA_VERSION` note. A forward "Planned (Wave 5, additive)" note was added to `03_data_contracts.md` this pass (§ Tiny fixes).
- **Deployment + visual layer.** Target decided — **FastAPI + Jinja2** (`docs/frontend_decision.md`); deps installed; a smoke-tested thin shell exists (`app/app.py`). Gaps for the public demo: only the single combined `report.html` view exists (the 4–5 designed screens are not built out); the **two explicit run modes are not surfaced** in the UI; no demo dataset; no charting/3D libraries. Backend honesty is already in place (engine degrades to `INSUFFICIENT_DATA`; ingestion emits ranked data-asks via `improvement_asks.py`/`missing_data.py` → `SiteModel.missing_data`; never fabricates). Full requirements in `wave5_scope.md`.

---

## Tiny shared-contract fixes applied (this pass)

Documentation-only; no engine/finance/contract **logic** changed; bar stays green (515 passed).

- **`docs/03_data_contracts.md §4`** — added the `equity_irr` row to the `FinancialCase` table and a note recording it as the W4-2 additive optional field (D21, C022). Updated the "no equity IRR" caveat (the field is now a sibling top-level scalar; `irr` stays the project IRR). Resolves the documentation side of **C022** (the doc now mirrors the code field-for-field).
- **`docs/03_data_contracts.md`** — added a brief **"Planned (Wave 5, additive)"** note flagging the forthcoming sizing evaluation-grid exposure for the 3D surface (the small contract note the gate asked for). **No field added now.**

## Remaining mismatches / follow-ups

**None blocking Wave 5.** Carried into `wave5_scope.md`:
- Expose `SizingRun.candidates_evaluated` through the engine seam so the 3D surface is plottable from persisted data (additive; recorded decision + `SCHEMA_VERSION` note).
- Package a clearly-labelled demo dataset under `examples/` (and refresh the stale `examples/README.md`) for the opt-in demo run mode.
- Build out the 4–5 demo screens + the explicit two-mode chooser; choose a public host.
- *(Optional, recorded)* revisit whether `generate_quote`'s no-arg default should flip from `BaselineEngine` to `MilpEngine` (currently deliberate per D22(5)).

## Go / No-Go

**GO for Wave 5.** The Wave 4 engine is coherent, deterministic, the default, conformant and honest; the bar is green; a real-data run produces all three artifacts with firm-vs-gated separated and non-bankable honestly labelled. The Wave 5 prerequisites (sizing-grid exposure, demo dataset, visual build, 3D surface, public host) are captured in `wave5_scope.md`.
