# Scenario engine (M6)

How GridQ turns one base case into a small set of **discrete, probability-weighted**
outcomes — Low / Central / High price cases, policy branches, and a COD-delay (time-to-
energisation) axis — and weights them into a summary. Deterministic and pure: no
randomness, no Monte Carlo. Package: [`src/gridq/scenarios/`](../src/gridq/scenarios/).

See also: [`SKILLS.md`](../SKILLS.md), [`docs/03_data_contracts.md`](03_data_contracts.md),
[`docs/assumption_library.md`](assumption_library.md), and the data-integrity rule in
[`docs/parallel_development_plan.md`](parallel_development_plan.md) §5.

---

## 1. Discrete scenarios (why, and what they are)

A bankable underwriting case is **honestly coarse**, not falsely precise. Rather than a
continuous probability distribution, GridQ v1 evaluates a handful of named, weighted
cases and reports the spread between them:

- **`Scenario`** — one case: a `name` (`low`/`central`/`high`/`custom`), a
  `probability_weight`, a `price_multiplier` (scales operating/revenue cashflows), a
  `cost_multiplier` (scales the year-0 capex outflow), a `cod_delay_months` (timing), a
  descriptive `policy_branch` label, an optional `forecast_ref`, plus `notes`/`warnings`.
- **`ScenarioSet`** — the set evaluated together: `scenario_set_id`, the
  `assumption_version` it was built against, the `scenarios`, and `validation_warnings`.
- **`ScenarioEngine.run(base_case, scenario_set)`** — applies each scenario to a base
  `FinancialCase` (or any object exposing `p50_cashflows`/`p90_cashflows`) and returns a
  `ScenarioRunResult`: one `ScenarioResult` per scenario plus a `WeightedSummary`.

**Determinism and honesty rules baked in:**

- **Weights must sum to 1.0**, or they are **normalised with a warning** — never silently.
  `ScenarioSet` validation flags an off-sum set; the engine normalises and records it in
  `WeightedSummary.weights_normalised` + `warnings`.
- **No Monte Carlo, no randomness** — see §5.
- **Every parameter comes from an assumption or explicit input.** Weights come from the
  assumption library (`scenario_weights.central_low_high`); price/cost bands are explicit
  input or backed by a `forecast_ref`. The engine invents no spread: `build_standard_
  scenario_set(...)` defaults a missing Low/High band to identity (1.0) **and warns**
  (a ranked data ask), rather than fabricating a number.
- **Central is the base case** — identity multipliers (1.0), so Central reproduces the
  input cashflows exactly.
- **The `policy_branch` is a label, not a number.** In v1 it carries *no* numeric effect on
  its own; any policy impact must flow through the explicit multipliers / COD delay (which
  must themselves be sourced). A non-default branch is flagged "descriptive only" so a
  placeholder branch can never masquerade as calibrated precision.
- **COD delay affects timing only**, and only when clearly modelled: a **whole-year** delay
  shifts operating cashflows later (capex stays at year 0; revenue past the horizon is
  deferred, not created). A **sub-annual** delay is **not** modelled in v1 — the engine
  warns and leaves timing unchanged rather than faking month-level precision.
- **The engine does not compute IRR/NPV/discounting** — that is the finance module (M5).
  Outputs are undiscounted cashflows + simple weighted totals, explicitly labelled coarse,
  and they inherit the base case's bankability (placeholder-sourced weights ⇒ not bankable).

---

## 2. External forecast references

Some scenario inputs — wholesale price curves, ancillary/BM revenue forecasts — are not
GridQ's to assume. They come from **external providers** (e.g. Modo Energy). The engine
never holds those numbers as assumptions; it holds a **reference** to the file they came
from, via [`ForecastReference`](../src/gridq/scenarios/forecast_reference.py):

| Field | Meaning |
|-------|---------|
| `source` | Provider/origin, e.g. `"Modo Energy"`. |
| `file_ref` | Path / stable locator of the forecast file (real data lives under gitignored `data/`). |
| `forecast_version` | The provider's forecast version / vintage. |
| `content_hash` | Canonical content hash of the forecast curve (`sha256:…`), format-independent — matches ingestion's `content_hash`. The provenance identity. |
| `file_sha256` | Literal source-file bytes hash (`sha256:…`), **optional** — matches ingestion's `file_sha256`; present when built from a real file. |
| `created_at` | When the forecast was produced (metadata). |
| `price_unit` | Unit of the forecast prices, e.g. `GBP/MWh`. |
| `coverage_start` / `coverage_end` | The date window the forecast covers. |
| `verified` | Machine-readable provenance flag (see below). |
| `warnings` | Soft signals surfaced for review. |

The `(source + forecast_version + content_hash)` triple is **required and non-empty**, and
`content_hash` (and `file_sha256`, when present) must be a **well-formed `sha256:<64 hex>`
digest** — a malformed hash is **rejected (raises), not merely warned**. `content_hash`
deliberately reuses ingestion's vocabulary and is a **distinct concept from the run-level
`input_hash`** on [`RunContext`](../src/gridq/provenance/run_context.py) / `QuoteRecord` (a
forecast-content hash, not a run pin) — they no longer collide. Well-formedness still cannot
prove a hash was computed from real data, so a machine-readable **`verified`** flag carries
the provenance:

- `forecast_reference_from_market_forecast(forecast)` — the **single seam from ingestion**:
  converts a loaded `MarketForecast` (`load_market_forecast_csv` / `load_market_forecast_json`)
  into a reference, carrying its `content_hash` / `file_sha256` across 1:1 and deriving the
  coverage window from the forecast's point timestamps. `verified=True` when the forecast was
  loaded from a real file (it carries a `file_sha256`); provenance metadata is never invented.
- `build_forecast_reference(file_path=…, source=…, …)` — **loads and hashes the actual file**
  via the ingestion loaders (so `content_hash` is the canonical content hash and `file_sha256`
  the literal bytes), sets `verified=True`; raises `FileNotFoundError` if the file is absent.
  You cannot create a verified reference to data that does not exist.
- `verify_forecast_reference(ref, file_path)` — **re-loads the forecast** and returns warnings
  if its content hash no longer matches the recorded `content_hash` (content-level drift
  detection); sets `verified=True` on a match and `verified=False` on drift/missing/unreadable.
- A **directly-constructed** reference is `verified=False`. The scenario engine warns on any
  unverified `forecast_ref` and forces the run **not bankable** (see §5 / §1), so a
  fabricated-looking pointer cannot ride through as bankable-backing.
- `require_forecast_reference(ref)` — asserts a reference is present where a scenario
  claims forecast-backed prices.

---

## 3. Why forecasts are input data, not assumptions to hallucinate

GridQ's foundational rule is that **every number traces to input + assumption version +
code version, and nothing on the pricing path is invented** ([`SKILLS.md`](../SKILLS.md)).
A market price forecast is neither a GridQ assumption nor something a model may guess:

- **It is third-party market data.** Its provenance is the provider and the file, not the
  GridQ assumption library. Putting a forecast *value* into `assumptions/*.yaml` would
  launder external data into a GridQ "assumption" and lose its source — and would mean a
  model could fill a gap with a plausible-but-fabricated curve. That is exactly the
  failure mode the data-integrity rule forbids ([parallel_development_plan.md §5](parallel_development_plan.md)).
- **So it is referenced, not copied.** The scenario records *which* forecast (source +
  version + content hash) it used. The quote is then reproducible and auditable: a lender
  can ask "which Modo vintage priced this?" and get an exact, hash-pinned answer.
- **Missing forecast ⇒ a warning or a ranked data ask, never an invented spread.** If no
  Low/High band is supplied, the scenario collapses to Central (identity) and says so. The
  absence is surfaced as a data ask, not papered over.

This keeps the separation clean: **assumptions = GridQ's own versioned numbers; forecasts
= referenced external inputs.** Neither is ever hallucinated.

---

## 4. How Modo price forecasts should enter the system

1. **Obtain the real file.** Place the Modo (or other provider) forecast under the
   gitignored `data/` directory (e.g. `data/forecasts/modo_2026q2.csv`). Real market data
   is never committed (D4 files-first storage; treat as commercially sensitive).
2. **Pin it.** Call `build_forecast_reference(file_path="data/forecasts/modo_2026q2.csv",
   source="Modo Energy", forecast_version="modo-2026-q2", price_unit="GBP/MWh",
   coverage_start=…, coverage_end=…, created_at=…)`. The file is loaded via ingestion, so the
   `content_hash` is computed from the forecast curve and `file_sha256` from the file's bytes.
   (If the forecast is already loaded as a `MarketForecast`, wire it straight through with
   `forecast_reference_from_market_forecast(forecast)` instead — same hashes, no re-read.)
3. **Attach it to scenarios.** Pass the reference into `build_standard_scenario_set(...,
   forecast_ref=ref)`, or use the one-call **`build_forecast_backed_scenario_set(assumptions,
   forecast_file=…)`** ([`engine.py`](../src/gridq/scenarios/engine.py)), which loads + pins the
   file and attaches the verified reference to every Low/Central/High band. The Low/High
   **multipliers** stay **explicit, sourced input** — the engine does **not** read prices out of
   the file to set a `price_multiplier` or invent the band (decision **D20**). The forecast drives
   the *dispatch price series*, not the cashflow multiplier.

   **Feed the per-scenario price series to dispatch.** `scenario_price_series(ref, scenario_name=…)`
   and `price_series_for_set(scenario_set)` in
   [`forecast_prices.py`](../src/gridq/scenarios/forecast_prices.py) resolve each band's price curve
   **from the pinned file**, re-checking the content hash (drift → empty series + a re-pin warning)
   and **never fabricating** an absent band (an unrecognised scenario label is warned, never guessed
   — the Modo labels are unconfirmed, C012). A forecast-backed `Scenario` (carrying the verified
   `forecast_ref`) reaches `DispatchProblem.scenario` through the existing `enumerate_sizing(scenario=…)`
   thread, so the real half-hourly MILP consumes the series via `problem.scenario.forecast_ref`. The
   concrete HH price-series *field* shape on the dispatch input stays the real engine's to define
   (`dispatch/contract.py` "knowingly absent"; D12/D17/D20; flagged to Olly in C021).
4. **Verify before relying.** `verify_forecast_reference(ref, path)` confirms the file still
   matches the recorded hash; on drift, re-pin and reprice.
5. **Gating still applies.** Wholesale / BM / ancillary value remains **gated upside** until
   the route-to-market partner/API is known (BLOCKERS B002) — a Modo reference makes the
   *input* traceable; it does not move that value into the priced floor or make it bankable.

> The forecast *values themselves* are consumed by the dispatch engine: W4-4 (D20) builds the
> `scenario_price_series(...)` loader that resolves each band's curve from the pinned reference and
> feeds it to the `DispatchProblem.scenario` seam, while the scenario engine's own job stays pinning
> the reference and expressing the spread as discrete, weighted cases (multipliers explicit). The
> value-stack separately consumes a scenario's `price_multiplier` when pricing a supplied dispatch
> schedule.

---

## 5. Why Monte Carlo is deferred

The System Brief is explicit: *"Scenario-based probabilistics — discrete weighted cases;
Monte Carlo deferred to portfolio work behind the same interface. Honest coarseness beats
false precision."* Reasons:

- **A lender reviews a handful of named cases, not a cloud of draws.** Low/Central/High +
  DSCR sensitivities are legible and defensible; a Monte Carlo fan chart over placeholder
  inputs would assert precision the inputs do not support.
- **Inputs are still placeholders.** Running thousands of draws over un-calibrated
  assumptions manufactures false confidence. Discrete cases keep the coarseness visible.
- **It belongs at portfolio scale, later.** Monte Carlo is a *portfolio* tool (correlations
  across many sites), which is out of scope for the single-site v1 ([`CLAUDE.md`](../CLAUDE.md)
  scope boundary). It can be added **behind this same `ScenarioSet` → `ScenarioRunResult`
  interface** without disturbing callers.
- **Determinism.** The v1 pricing path is deterministic and seedless-by-construction here
  (no sampling). Adding Monte Carlo later means seeding and recording draws — a deliberate,
  versioned change, not a v1 default.

---

## 6. Status

Implemented on the `agent/scenarios` lane: models, weights, forecast references, the engine,
and `build_standard_scenario_set` (weights read from `gb_v0_1`). Tests:
[`tests/test_scenario_models.py`](../tests/test_scenario_models.py),
[`tests/test_scenario_engine.py`](../tests/test_scenario_engine.py). All scenario parameters
remain **placeholder/needs_review** until real bands and a real forecast reference are
supplied; scenario output is **not bankable** while weights come from placeholder assumptions.
