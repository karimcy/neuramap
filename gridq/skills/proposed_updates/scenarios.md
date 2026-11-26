---
proposed_by: agent/scenarios (W4-4, Olly)
date: 2026-06-24
status: proposed   # proposed | accepted | rejected
---

## Operation

`append` — one bullet to the existing **## Assumption / provenance rules** section of `SKILLS.md`
(does not touch the protected slow-update block). One edit only.

## Target

The `## Assumption / provenance rules` section in `SKILLS.md` (the home for the
assumptions-vs-forecasts provenance rules). Append after the existing assumption-library bullets.

## Proposed text

- **External price forecasts (Modo/wholesale) are referenced + fed as a *series*, never as an
  assumption or a derived multiplier.** A forecast enters only as a hash-pinned `ForecastReference`
  (`scenarios.build_forecast_backed_scenario_set` loads + pins a real file onto Low/Central/High).
  It reaches dispatch as a per-scenario price *series* via
  `scenarios.forecast_prices.scenario_price_series(ref, scenario_name=…)`, which **re-verifies the
  content hash** before reading (drift → empty series + a re-pin warning) and **never fabricates an
  absent band** (an unrecognised scenario label is warned, not guessed). Do **not** derive a
  scenario `price_multiplier` from a forecast (multipliers stay explicit, sourced input) and do
  **not** store forecast values in the assumption library — that launders external data and loses
  provenance. A missing/unreadable forecast → an identity set with no reference + a ranked data-ask,
  never a guessed curve or spread. The half-hourly price-series *field* shape on the dispatch input
  stays the real MILP's to define (deferred; see `dispatch/contract.py`).

## Evidence

- **W4-4 build** (`gridq.scenarios.forecast_prices` + `build_forecast_backed_scenario_set`,
  `tests/test_scenario_forecast_prices.py`), recorded as **decision D20**.
- **OPEN_COMMENTS C012** (Modo format unconfirmed — the loader warns on unrecognised labels rather
  than guessing) and **C021** (Olly to confirm the dispatch-side consumption seam).
- Reinforces the existing data-integrity rule (`docs/scenario_engine.md` §3;
  `docs/parallel_development_plan.md` §5): assumptions = GridQ's versioned numbers; forecasts =
  referenced external inputs; neither is hallucinated.

## Notes

This is procedural "how to act" (the rule a future session must follow when wiring a forecast),
not a record of what happened — the *what* lives in `docs/changelog.md` / `docs/decisions.md` (D20).
It belongs beside the assumption/provenance rules because the live failure mode is exactly an agent
either inventing a price spread or quietly converting a forecast curve into a `price_multiplier`;
this bullet says: reference + feed the series, keep multipliers explicit, degrade with a data-ask.
