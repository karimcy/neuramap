# Cloud resume — quality bar against the WP-0..WP-3 remediation branch

**Read this if you are the cloud session picking up the ultracode underwriting-remediation goal.**
Companion to `docs/HANDOFF_ultracode_goal.md` (that one is the goal; this one is the state of step 1).

Written: 2026-08-11 · Contributor: Olly · Branch `agent/revenue-data-drive` · HEAD `11dcb22`

The prior session ran locally on macOS, was stopped at the user's instruction to relocate to the
cloud, and modified **no** `src/` or `tests/` file. Nothing was fixed. Steps 2–6 of the goal are
entirely untouched.

---

## Step 1 — quality bar: 3 of 4 gates complete

| Gate | Result |
|---|---|
| `python -m gridq.dev.review_gates` | **GREEN** — 10/10 PASS, 0 blocking, 0 warnings |
| `mypy` | **CLEAN** — "Success: no issues found in 155 source files" |
| `ruff check .` | **25 errors**, confined to 2 files (as expected) |
| `ruff format --check .` | **28 files** — count as expected, distribution NOT |
| `pytest` | **INCOMPLETE — still the real unknown** |

Environment those came from: macOS, Python 3.12.11 (`.venv`), pytest 9.1.1, ruff 0.16.1, mypy 2.3.0.
Re-run the three fast gates on Linux once as a host-parity check before trusting any pytest failure —
it separates real defects from toolchain/platform drift.

### `ruff check` — 25 errors, pre-existing and confined
- `docs/grundon_harwell/matrix.py` — 18
- `docs/grundon_harwell/repair_v5.py` — 7
- Rules: E701 ×9, E702 ×8, E501 ×5, E741 ×2, UP031 ×1
- **Nothing in `src/` or `tests/`.** The "src/ and tests/ pass ruff check" claim holds.

### `ruff format --check` — 28 files, and NOT confined to docs/
The goal brief said "src/ and tests/ pass ruff and mypy". That is true for `ruff check` and `mypy`,
and **false for `ruff format --check`**: tests 10, src 7, docs 7, scripts 3, examples 1.

The 7 `src/` files are largely this goal's own new WP modules — drift introduced by the remediation
work, not inherited: `app/app.py`, `engine/README.md`, `ingestion/hh_synthesis.py`,
`optimisation/valuation.py`, `quotes/assembly.py`, `quotes/confidence.py`,
`value_stack/capacity_budget.py`.

tests: `test_capacity_budget`, `test_floor_equals_objective`, `test_hh_synthesis`,
`test_price_to_target_nets_stack`, `test_quotes_assembly`, `test_quotes_lifecycle`,
`test_schemas_stream_consent`, `test_truth_in_labelling`, `test_valuation_consent_pricing`,
`test_value_stack_value_functions`.

### ⚠️ TRAP — do not run a blind `ruff format .`
**ruff 0.16 formats Python code blocks inside Markdown.** Seven of the 28 are `.md`. Verified on
`docs/review_gates.md`: ruff would collapse deliberately aligned trailing comments in documentation
examples (the `{"op": "append", ...}` block), degrading the docs to satisfy a formatter.

Format `src/` and `tests/` explicitly. Do not sweep `docs/`.

---

## pytest — what is actually known

The suite has **never** had a verified full run, and still has not. Two hard facts were established:

1. **Fixed per-process pytest overhead is ~1 second**, not 60–120s (`tests/test_imports.py`:
   16 tests, 1.0s wall). Per-file chunking is therefore cheap — ~2 min across ~135 files — and is
   the right durability strategy.
2. **`tests/test_app_auth_scoping.py` PASSES but takes 220.8 seconds.** One app-auth file burning
   3.7 minutes is a suite-health defect in its own right, and it means total wall time exceeds the
   "~40 minutes" quoted in the handoff. Budget accordingly.

Also observed and worth confirming: the process that looked like it was hanging in collection was at
99.6% CPU with `polars-ooc-cleaner` / `tokio-rt-worker` threads on its stack, i.e. something appears
to do polars-backed data loading **at import/collection time**. Collection-time side effects
(possibly file or network I/O at import) are a suite-health issue distinct from the buffering trap.

Do not conclude "hung" from a silent log: pytest emits nothing between startup and
`collected N items`, so with `-u` the per-test lines only flush **after** collection finishes.

### Use the committed runner
`scripts/run_test_chunks.py` — one pytest process per chunk, appends durable JSONL + a human summary
after **each** chunk, per-chunk timeout, results written outside the repo, and **resumes** by
skipping files already recorded.

```bash
python scripts/run_test_chunks.py                 # 1 file per process (best localisation)
python scripts/run_test_chunks.py --batch 14      # ~10 groups, less overhead
python scripts/run_test_chunks.py --slice 0 40    # first 40 files
```

Do **not** parallelise chunks until it is confirmed that no tests write shared artifacts (golden
files, `outputs/`, `data/fetched/`) — several tests in this repo write generated output.

---

## Cloud environment setup (learned the hard way)

- `pip install -e ".[dev]"` **fails** on a Debian container: it cannot uninstall the distro-managed
  `cryptography` ("Cannot uninstall cryptography … RECORD file not found"). This works:
  ```bash
  pip install -e ".[dev]" --ignore-installed cryptography
  ```
- Prefer **Python 3.12** — the mypy config targets 3.12; a cloud container defaulted to 3.11.15.
- Verify these pins survived the install; they are part of the **D18 determinism contract**:
  `xarray>=2024.2,<2025` and `linopy>=0.5,<0.6`. An earlier cloud install correctly resolved
  xarray 2024.11.0 / linopy 0.5.8. `pandas`, `mypy` and `ruff` are open-ended, so attribute any
  new failure to toolchain drift only after checking versions.
- `graphify` is **not installed** on either host, so CLAUDE.md §11's graphify-first rule is
  inapplicable (it is conditional on `which graphify`); read and grep files directly.

---

## Open discrepancies to resolve, not to paper over

1. **Site count.** The goal says "five sites" and names four; `examples/real/sites/` contains **six**
   JSONs: `bishops_cleeve`, `colnbrook`, `ewelme`, `harwell`, `knowl_hill`, `tanhouse`. §7 of the
   handoff is consistent with six (3 capacity-blocked: harwell/colnbrook/tanhouse; 3 runnable:
   bishops_cleeve 250 kVA, knowl_hill 325, ewelme 750). Treat six as the real set, confirmed from
   the JSONs.
2. **The `ruff format` distribution** above contradicts the brief. Reported, not reconciled away.
3. **`test_app_auth_scoping.py` at 220s.** Reported as a finding; not yet diagnosed.
4. **Customer data is now in git.** `11dcb22` committed and pushed `examples/real/` and `data/`,
   which §5 of the handoff called correct policy to keep **out**. It is what makes cloud validation
   of the sites possible, so it is not being reverted here — but it is on the remote now and is a
   decision worth revisiting once the sites are validated.

## Instruction in force

The user's instruction was: **report the complete failure list first and fix nothing until asked.**
Finish pytest, report everything, then wait. Every later fix lands with a test naming its finding ID.
