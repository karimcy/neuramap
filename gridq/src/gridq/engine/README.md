# gridq.engine

The shared **optimisation-engine interface** and a **baseline implementation** for integration only.

## The interface

`OptimisationEngine` (a `@runtime_checkable` `Protocol` in `interface.py`) is the single seam between Oli's platform and Olly's optimiser:

- `build_optimisation_request(site_model, assumptions, scenario_set) -> OptimisationRequest`
- `solve(request) -> OptimisationResult`

Anything implementing these two methods is an engine. The contracts (`OptimisationRequest`/`OptimisationResult`) live in `gridq.schemas` — the engine never owns them.

## Where the real MILP plugs in

Olly's production MILP will be a **new class that implements `OptimisationEngine`** (e.g. `gridq.optimisation.MilpEngine`), returning a schema-valid `OptimisationResult` with `implementation_level="production"`. Because finance/outputs consume only the `OptimisationResult` contract, swapping the baseline for the real engine requires **no downstream change** — that swap is guarded by the conformance suite (issue 014, see below).

## Conformance suite (`conformance.py`)

`gridq.engine.conformance` is the executable bar **any** engine must pass — the baseline now, the real MILP later — so the two can never silently diverge. It is **pytest-free and import-safe** (it depends only on the frozen `gridq.schemas` contracts + this interface), so Olly runs the *same* checks from his own lane:

```python
from gridq.assumptions import load_assumptions
from gridq.engine.conformance import assert_conformant

assert_conformant(MilpEngine(), assumptions=load_assumptions("gb_v0_1"))
```

- `check_engine_conformance(engine, *, assumptions, sites=None) -> ConformanceReport` — drives the engine across `canonical_conformance_sites()` and returns a structured pass/fail report (`.ok`, `.failures`, `.summary()`).
- `assert_conformant(...)` — raises `ConformanceError` listing **every** failing check.
- `check_result_conformance(result, *, request=None)` — the per-result invariants, usable on a single `OptimisationResult` with no engine.

It checks **contract-level** invariants only — re-validation, request echo, stream-registry membership, gated-upside rules, the priced-floor exclusion, determinism (excluding wall-clock fields), and spec coherence — deliberately **not** specific numbers (the real optimiser legitimately differs from the mock) nor the half-hourly dispatch physics (those live behind `dispatch_schedule_ref`, off the result, and belong in the engine's own M3 tests). Several checks have **teeth beyond Pydantic**: e.g. a gated-upside stream priced into the floor as `contracted` passes schema construction but fails conformance. `tests/test_optimisation_contract_conformance.py` runs the suite against `BaselineEngine` (passes) and against deliberately non-conforming stubs (each fails one check), and exercises the M5 seam end-to-end via `quotes.pipeline.generate_quote`.

## What `BaselineEngine` is for

- Proving the pipeline runs **end to end** (site → request → result → finance → quote → outputs) before the MILP exists.
- **Contract conformance:** it returns a valid `OptimisationResult`, so downstream modules can be built and tested against real shapes.
- Deterministic plumbing: same request → same result.

## What `BaselineEngine` is NOT for

- **Underwriting.** It is `implementation_level="baseline"`, `bankable_status="not_bankable"`, always. The schema even forbids a baseline result from being `bankable_candidate`.
- **Pricing.** It invents **no** revenues — every value stream is `0` with a "not priced yet" warning. It runs no dispatch and no financial maths.
- **Showing capital.** Its numbers are illustrative plumbing (e.g. a 10% `BASELINE_POWER_FRACTION` sizing heuristic), never a recommendation.

## How Olly both extend it

- **Code to the interface, not the implementation.** Build finance/outputs/CLI against `OptimisationEngine` + `OptimisationResult`; accept any engine by dependency injection.
- **Olly** adds the real engine as a separate implementation of `OptimisationEngine`; the baseline stays as the integration reference and conformance baseline.
- **Oli** can extend the baseline's plumbing (e.g. echo more streams) — but only within the "never bankable, never priced" rules.

## How to avoid fake precision

- Never raise `bankable_status` above `not_bankable` in baseline/integration code.
- Never invent a revenue; leave streams at `0` with a warning until a deterministic, assumption-driven calculation exists in the real engine.
- Keep the baseline warnings on every result so no consumer mistakes it for underwriting output.
- Read any number from the assumption library; the lone `BASELINE_POWER_FRACTION` is a labelled integration heuristic, deliberately **not** a library assumption.
