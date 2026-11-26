---
proposed_by: agent/integration
date: 2026-06-23
status: proposed
---

## Operation
insert_after   (one edit)

## Target
`## Testing and validation rules` — immediately after the existing bullet:
> "- Tests land with the change. Determinism gets a golden/repro test. Contracts get conformance tests (mock and real engine both satisfy the schema; M5/M8 need no code change across the swap — *not* identical numbers)."

## Proposed text
- A contract's conformance suite is an **importable harness** in the package (e.g. `gridq.engine.conformance.assert_conformant`), **not** test-only code, so the real implementation runs the *same* checks the mock passes — that is what makes "no silent drift" enforceable rather than aspirational. Keep it dependency-light (frozen contracts only) so it never couples to a churning lane. Pair it with a **deliberately non-conforming stub that must fail**: a suite no stub can break has no teeth. Encode the invariants the schema validators *cannot* (e.g. a gated-upside stream priced into the floor as `contracted`, non-determinism, a dropped request-echo), and **scope it to the contract** — never assert specific numbers or off-contract physics a correct real engine may legitimately differ on.

## Evidence
W3-4 (branch `agent/integration`): built `src/gridq/engine/conformance.py` + `tests/test_optimisation_contract_conformance.py` for the `OptimisationResult` seam (issue 014; decision **D16**; resolves OPEN_COMMENTS **C003**). The placeholder `BaselineEngine` passes all checks; three stubs each fail exactly one check Pydantic does not enforce — the `contracted`-gated-in-floor stub is the concrete case the schema validators (which bar only `firm` for gated streams) let through, the same gap flagged in C014/W2-2.

## Notes
This is procedural ("how to build a conformance bar that actually prevents drift"), not a fact or a decision — the *event* is in `docs/changelog.md` and the *decision* (remove `optimisation_contracts/`, add the harness) is **D16** in `docs/decisions.md`. It sharpens the existing conformance-tests rule with the two lessons that made W3-4 effective (importable harness + a failing stub), without touching the protected slow-update block.

---

<!-- second proposed edit (Wave 3 harmony pass, 2026-06-23, agent/integration) -->

## Operation (edit 2)
insert_after   (one edit)

## Target (edit 2)
`## Working with the shared codebase` (or the integration/merge guidance section) — after the existing coordination bullets.

## Proposed text (edit 2)
- After a multi-lane parallel merge, run the harmony check **and reconcile the cross-cutting INDEX files to the merged package set** — `src/gridq/cli.py` `_MODULES` (the `gridq info` table) and `tests/test_imports.py` `SUBPACKAGES` (the import smoke-test). A new package that no index references is invisible: it ships, imports fine in isolation, and the green bar stays green — so the drift is silent. These are the cheapest "tiny adapter fix in a shared file" a harmony pass should own; lane logic stays untouched. Keep optional-extra packages (e.g. `app`, behind `pip install -e ".[app]"`) **out** of the mandatory smoke-test and cover them in their own extra-guarded test, so the core scaffold still imports without the extra installed.

## Evidence (edit 2)
Wave 3 harmony audit (branch `agent/integration`, 2026-06-23): the merged tree was green and contract-coherent, but `cli._MODULES` + `test_imports.SUBPACKAGES` still omitted five packages that had landed in parallel (`dispatch`, `sizing`, `telemetry`, `dev`, `app`) — three independent audit agents converged on it as the only NEEDS-FIX. Adding them (with `app` left to `tests/test_app_smoke.py` as an optional extra) kept the bar green at 417 tests. Report: `docs/wave3_harmony_report.md`.

## Notes (edit 2)
Procedural, not a fact/decision (the *event* is the Wave 3 harmony entry in `docs/changelog.md`). Pairs with edit 1: edit 1 prevents *behavioural* drift (conformance harness); this prevents *index/visibility* drift after a merge. Does not touch the protected slow-update block.

---

<!-- third proposed edit (Wave 4 harmony pass + Wave 5 readiness gate, 2026-06-24, agent/integration) -->

## Operation (edit 3)
insert_after   (one edit)

## Target (edit 3)
`## Testing and validation rules` (or the harmony-check guidance) — after edit 1's conformance bullet.

## Proposed text (edit 3)
- **Audit the seam, not just the lane — verify the data the *next* phase needs actually survives the contract to the persisted artifact, not merely that it is computed in-memory.** A lane can correctly produce and *retain* a rich result internally while the frozen output contract carries only a projection of it, so the data is silently dropped at the seam and is absent from the saved record/outputs. When a downstream phase (a visual, an export, a re-underwrite) will need a fuller result than the contract exposes, that is a **logged additive-exposure pre-req, not a blocker**: surface it via a duck-typed engine accessor (the established `selected_dispatch_energy()` pattern — no frozen-field promotion) or an additive optional field under a recorded decision + `SCHEMA_VERSION` note. Trace the field from producer → contract → persisted artifact, not just to where it is first computed.

## Evidence (edit 3)
Wave 4 harmony audit & Wave 5 readiness gate (branch `agent/integration`, 2026-06-24; reports `docs/wave4_harmony_report.md` / `docs/wave5_scope.md`): the sizer *already retains* the full evaluated grid (`SizingRun.candidates_evaluated` — power×duration×levered-return×P90-DSCR×feasibility, the exact inputs the Wave 5 3D surface needs), but `MilpEngine.solve()` returns only the chosen candidate's frozen `OptimisationResult`, so the grid never reaches the `QuoteRecord`/outputs and the surface is not plottable from persisted data. Caught only by tracing the field through the seam rather than stopping at "the grid is computed." Logged as a Wave 5 additive fix + a "Planned (Wave 5, additive)" note in `docs/03_data_contracts.md`; not a Wave-4 blocker (gate verdict: GO).

## Notes (edit 3)
Procedural, not a fact/decision (the *event* is the W4-6 entry in `docs/changelog.md`). Complements edits 1–2: edit 1 prevents behavioural drift, edit 2 prevents index/visibility drift after a merge, and this prevents *seam-projection* drift (a real result that the contract quietly truncates before persistence). Does not touch the protected slow-update block.
