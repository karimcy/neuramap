# Provenance

How a GridQ run is made **reproducible and traceable**. Determinism is a property we test, not hope for: identical inputs + assumption version + code version produce identical outputs.

## Utilities (`gridq.provenance`)

| function | purpose |
|----------|---------|
| `stable_json_dumps(obj)` | Canonical JSON — sorted keys, compact, deterministic. Renders Pydantic models, dates and enums consistently, so equal-by-value objects serialise identically (key order doesn't matter). |
| `hash_json(obj)` | `sha256:<hex>` of the canonical JSON — the input/output hash primitive. |
| `hash_file(path)` | `sha256:<hex>` of a file's bytes (e.g. a half-hourly data file). |
| `get_git_commit()` | Current git commit SHA, or `"unknown"` if not a git checkout / git unavailable (never raises). |
| `get_code_version()` | `git:<sha>` when available, else `pkg:<version>`. |
| `build_run_context(input_obj, assumption_version, schema_version, *, warnings, created_at)` | Builds a `RunContext` for a run. |

## RunContext

The provenance stamp attached to every engine/finance/quote run:

- `run_id` — derived **deterministically** from the input hash + assumption/code/schema versions (not from the clock), so identical inputs yield the same id.
- `input_hash` — `hash_json` of the run's input object.
- `assumption_version` — the assumption-library release used (e.g. `gb_v0_1`).
- `code_version` — `get_code_version()`.
- `schema_version` — the schema version the run was built against.
- `created_at` — wall-clock metadata only; **never feeds a calculation** (a clock in the maths would break reproducibility).
- `warnings` — non-fatal notes carried with the run.

## Why every quote output must show the assumption version + input hash

A number is only **bankable** if it can be regenerated and audited. The pins do that:

- **Reproducible:** re-running with the same `input_hash + assumption_version + code_version` (+ seed, on the same platform) reproduces the result exactly — so a lender's number can be re-derived, not taken on trust.
- **Traceable:** the `assumption_version` ties every figure to the exact values (and their `source`/`status`/`confidence`) that produced it; the `input_hash` ties it to the exact `SiteModel` + request.
- **Valid only against its version:** a quote is valid only against the assumption version that produced it; if assumptions move beyond tolerance, the quote reprices rather than drifting silently.

This is why outputs (memo, proposal, quote record) must carry the assumption version and input hash — without them, "consistent by construction" and "reproducible" are unverifiable. See [assumption_library.md](assumption_library.md) and [02_architecture.md §6–§7](02_architecture.md).

## Where runs are persisted (Wave-5 UI)

The web UI (`gridq.app`, D24) persists each run so the navigable screens (Inspect → System spec → Memo+Proposal → Telemetry) reload **one** traceable record rather than recomputing:

- **Location:** `$GRIDQ_RUN_STORE` (default `<repo>/runs/`), which is **gitignored** — `quote.json` / `site.json` / `telemetry.json` per run, plus uploaded half-hourly files under `runs/uploads/` (content-addressed).
- **Keyed by the deterministic `quote_id`** (`quote_run_<16hex>`, derived from `input_hash` + versions), so writing is **idempotent** and re-submitting the same site overwrites identically — the determinism the smoke test asserts.
- **Privacy (wave5_scope §5):** a user's own real submission is processed for their run and stored only here (gitignored, never publicly indexed); `gridq.app.store.delete_run(quote_id)` is the deletion path. **Real third-party offtaker/meter or counterparty-credit data is never preloaded, committed, or shipped** — the public demo ships only the synthetic `examples/demo_site/` dataset.
