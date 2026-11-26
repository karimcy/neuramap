# Architecture (short)

The one-page version. Full detail in [02_architecture.md](02_architecture.md); contracts in [03_data_contracts.md](03_data_contracts.md).

## Principle

**Deterministic pricing core, agentic perimeter.** The pricing path (`SiteModel → Optimisation → Financial model → QuoteRecord`) is pure, versioned, reproducible numerical code — **no LLM or stochastic AI ever touches a number a lender relies on**. AI lives only on the perimeter (ingestion assistance, credit research, document drafting), schema-validated and human-confirmed. The core is **market-agnostic**; GB specifics are *content* in the assumption library, never branches in the code.

## Pipeline

```
SiteData → Ingestion(M0) → SiteModel(M1) → Optimisation(M2·M3·M4) → Financial model(M5) → QuoteRecord(X.2) → Outputs(M8) → Telemetry(X.3)
                                                  ▲                        ▲
                                       Scenarios(M6)            Credit(M9)
              Assumption library(X.1) feeds every step · Provenance(X.2) stamps every number
```
In v1 the only **mocked** step is the optimiser; everything else is real, so swapping the mock for Olly's real MILP is the one seam that changes.

## Stack

Python package (`src/gridq/`); **Pydantic** contracts → JSON Schema; **YAML/JSON** versioned assumptions (files first, SQLite only if needed); **pytest** + ruff + mypy; a thin **typer** CLI now, FastAPI/Next.js later. See [02_architecture.md §2](02_architecture.md) and [decisions.md](decisions.md).

## Folder map

`src/gridq/`: `schemas/` · `ingestion/` (M0) · `assumptions/` (X.1) · `provenance/` (X.2) · `optimisation_contracts/` (interface + mock) · `scenarios/` (M6) · `finance/` (M5+M9) · `quotes/` (lifecycle + X.3) · `outputs/` (M8). the real engine lands in a separate `gridq.optimisation/` behind the same interface.

## The two frozen contracts (the platform↔engine seam)

`SiteModel` (input) and `OptimisationResult` (output). `OptimisationRequest` is the input envelope; the stream registry is part of the result contract. Frozen at Milestone 1; conformance tests guard the mock↔real swap. Field-level detail: [03_data_contracts.md](03_data_contracts.md).

## Determinism & traceability

`output = f(input, assumption_version, code_version, model_version, seed)`. Every quote records `input_hash`, the three versions, `seed`, `output_hash`, `reproduction_env`, timestamp, data tier, human-minutes and lifecycle status — so any number regenerates exactly (byte-identical on the same platform; tolerance across platforms).

## See also

- [00_project_boundary.md](00_project_boundary.md) — scope & ownership
- [01_project_spec.md](01_project_spec.md) — spec, module map, milestones
- [module_ownership.md](module_ownership.md) · [project_status.md](project_status.md) · [glossary.md](glossary.md)
