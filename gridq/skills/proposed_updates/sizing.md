---
proposed_by: agent/dispatch-sizing (Olly)
date: 2026-06-24
status: proposed   # proposed | accepted | rejected
---

## Operation
append (one edit)

## Target
"## Testing and validation rules" — append one bullet.

## Proposed text
- **"One engine, called twice" is enforced by code-sharing + a recomputed fingerprint, never a
  tautology.** When two paths must agree by contract (sizing's per-candidate pricing vs a standalone
  underwriting re-price of the chosen spec), make them call **one** shared primitive, and have the
  second path **reconstruct its inputs independently** from the raw `(site, assumptions, spec)` —
  never hand it the first path's pre-built result. Assert consistency on a **timestamp-free
  fingerprint of the load-bearing numbers + the run's `input_hash`** (and that the two result
  objects are distinct instances) — never object identity and never `created_at`. Comparing
  `f(x) == f(x)` on the same object proves nothing; recomputing from raw inputs and matching the
  input hash proves the seams cannot drift and that no wall-clock leaked into a computed value.

## Evidence
W4-2 (HANDOFF_LOG / changelog 2026-06-24, decision D21, branch `agent/dispatch-sizing`): built the
sizing↔underwriting consistency seam (`sizing._evaluate_spec` shared by `enumerate_sizing` and
`underwrite_spec`; `sizing.financial_fingerprint`). An adversarial design review flagged that the
first-drafted check (call the shared primitive twice, assert equality) was trivially true; the test
`test_sizing_eval_equals_underwrite_reprice` was rebuilt to reconstruct the call path independently
and compare the fingerprint + `run_context.input_hash` with `under.financial is not run.selected.financial`.

## Notes
Procedural ("how to act" when testing a cross-seam consistency contract) — generalises beyond this
task to any "same numbers, two call paths" guarantee (e.g. mock↔real engine conformance). The
*event* (W4-2 shipped) is in `docs/changelog.md`; this is the reusable rule. Bounded to a single
bullet under an existing heading; touches no protected block.

---

<!-- Second proposed edit (W5-0, D23) — appended; the W4-2 proposal above is unchanged. -->

## (W5-0) Operation
append (one edit)

## (W5-0) Target
"## Architecture and seam rules" (or the nearest seam/contract-discipline heading) — append one bullet.

## (W5-0) Proposed text
- **To surface data an inner layer already computes but a frozen contract drops, mirror the
  existing out-of-band hook — never mutate the frozen contract.** When a downstream consumer needs
  richer data than a frozen shared seam carries (e.g. the 3D surface needs the *whole* evaluated
  sizing grid, but `OptimisationResult` carries only the selected spec), don't promote a field onto
  the frozen contract (that's a governance-gated, conformance-/hash-affecting joint event). Instead:
  (1) add a **duck-typed accessor on the producer** that mirrors the established hook
  (`MilpEngine.last_sizing_run()` alongside `selected_dispatch_energy()`), read by the pipeline via
  `getattr`/`callable` so hookless engines are unaffected; and (2) persist it onto a layer you own
  (the `QuoteRecord`) as an **additive, Optional** field that is **excluded from the identity hash**
  (`assembly._canonical_run_input`) — so `input_hash`/`quote_id` and `SCHEMA_VERSION` are unchanged
  and old records/fixtures validate untouched. Copy producer scalars **verbatim**; leave a genuinely
  unavailable metric `None` (never coerce to `0`/`False`), and leave the "winner" `None` when there
  honestly isn't one. Prove it with tests for grid/projection **completeness vs the source**, a
  **hash-unchanged** assertion (field present vs absent), and a **no-hook backward-compat** path.

## (W5-0) Evidence
W5-0 (changelog 2026-06-24, decision **D23**, branch `agent/sizing`): exposed the full sizing grid via
`SizingRun.sizing_grid()` → `gridq.schemas.sizing.SizingGrid`, `MilpEngine.last_sizing_run()`, and an
additive `QuoteRecord.sizing_grid` — frozen `OptimisationResult` untouched (D13), `SCHEMA_VERSION` not
bumped, `input_hash` unchanged. 12 tests incl. completeness/determinism/hash-unchanged/no-hook paths.
An adversarial multi-lens review (correctness/determinism/governance/honesty/tests + skeptic
verifiers per finding) was run before handoff.

## (W5-0) Notes
Procedural + reference — generalises the D13 `selected_dispatch_energy()` precedent into the standing
rule for *any* "inner layer computed it, the frozen seam drops it, a consumer needs it" situation. The
event (W5-0 shipped) is in `docs/changelog.md`; this is the reusable rule. One bullet under an existing
heading; touches no protected block.
