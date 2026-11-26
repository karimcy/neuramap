# 018 · A2/A3 — export-in-dispatch design (Fable build-planner pass, 2026-07-17)

**Status:** DESIGN AGREED (pending owner-authorized G1/D43 filing — see below). Execution is for a `feature-builder` (Opus 4.8, max), one task at a time, AFTER the safer 018 slices land. This is the substance for the G1 OPEN_COMMENTS entry + D43 decision.

> **Gate note (Olly, 2026-07-17):** issue 018 §4 owner line was set to "approve without gate required" — the export/solver work is owner-authorized to proceed without a separate Olly sign-off *ceremony*, mirroring how D42/C033 was handled (authorized in-session; Olly review is amendments-not-blockers). The G1/D43 record is still to be FILED for traceability before execution, and the Olly-review items below (consent predicate, R1–R7) are logged as amendments, not blockers.

## Recommendation: architecture (b) — a separate gated-upside overlay solve

The floor solves exactly as today (model matrix untouched when export is off — **byte-identical by construction, not tolerance**). Then, only when consents cohere AND an aligned HH wholesale series exists, the engine runs **one extra deterministic solve of the selected spec on a fresh solver instance** with an `export` variable enabled, and reports:

`upside £ = (overlay floor-terms + wholesale export revenue) − floor-solve floor-terms`

under `WHOLESALE_P415` in `gated_upside`, firmness **CONDITIONAL** (never FIRM). The delta basis (i) is ≥0 by construction (export=0 recovers the floor optimum), (ii) auto-nets the retail cost of energy bought to export and any floor arbitrage cannibalised, (iii) is £0 when export never pays. Reporting **gross** export revenue instead would double-count the floor's own arbitrage energy — rejected inside (b).

### Why (a) and (c) are rejected
- **(a) fold export into the floor objective at wholesale** — co-optimises behind-the-meter dispatch against wholesale; floor dispatch/£/bill/peak and every golden move; wholesale £ lands on the priced floor. Direct reversal of D20/B002/north-star + `GATED_UPSIDE_STREAMS`. Contamination by definition.
- **(c) two-tier/subordinate objective** — provably vacuous: every exported kWh must be recharged through the meter (cyclic SoE wrap) and retail `price_t = energy band + DUoS > 0` everywhere, so any `export > 0` strictly decreases the floor term. Epsilon-subordinate ⇒ `export ≡ 0`; lexicographic (`F ≥ F*` then max export) ⇒ infeasible for `export > 0` ⇒ `export ≡ 0`. Export value is intrinsically a trade (spend retail £ + some floor value to earn wholesale £), so any formulation forbidding touching floor £ forbids export. Also cannot blend real wholesale coefficients below the `_EPS`/`_EPS_PEAK`/`_EPS_AGE` hierarchy without perturbing it.
- **The overlay frees the WHOLE schedule** (charge, discharge, export), not a frozen-charge overlay (also vacuous — no spare stored energy in a cycle-balanced optimum). Honesty comes from reporting the delta, not from freezing.

## Ordered tasks
- **T0 — governance (blocks T3/T5):** file G1 OPEN_COMMENTS + D43 in `docs/decisions.md` from this design (export variable, overlay architecture, consent predicate, `export_limit` kind reclassification, delta valuation basis). Owner-authorized (see gate note); Olly review = amendments.
- **T1 — consent predicate (standalone):** `export_dispatch_enabled(site)` in `src/gridq/value_stack/gates.py` beside `has_export_consent`. **Divergence to resolve:** issue §2 says "G99 approved + export_mpan + mec_kw>0"; existing `has_export_consent` requires G99∧G100 approved + MEC>0; validators treat G99/G100 `not_required` as incoherent for export. Recommend strictest intersection: **G99 ∧ G100 approved ∧ MEC>0 ∧ export_mpan present**. One predicate, one home; engine/valuation/tests all read it. (R6 — owner/Olly confirm.)
- **T2 — schedule contract (pure, additive):** `schedule.py` gains `export_kw`, `export_price_gbp_per_kwh` (both `None` default), accessors `annual_export_mwh`, `export_revenue_gbp`, and `priced_floor_terms_gbp` (the **unclamped** `Σ(energy+duos)·(discharge−charge)·dt` — the delta needs exact objective terms, not the clamped/rounded per-component values). `ref()` untouched when fields None.
- **T3 — solver overlay (Olly's file, heart of A2):** `milp_solver.py` `_solve_milp` gains kw `export_price_gbp_per_kwh=None` (default = today's model, no new var — structural byte-identity). Behind `is not None`: variable `export` (0..`min(power, mec_kw)`); SoE balance `soe − soe[pred] − charge·rte·dt + (discharge + export)·dt == prov_rhs`; inverter `discharge + export ≤ power − prov_unmet` (per-instant `discharge_ub = min(max(power−prov_unmet,0), load)` stays — export is the only past-boundary flow); warranty cap `Σ(discharge+export)·w·dt ≤ cap_rhs`; reservation aggregate `discharge + export − charge ≤ envelope − committed` with envelope unchanged at `min(power−aux, load)`; objective `+ Σ export_price·export·dt`, `_EPS` extends to `(charge+discharge+export)`, `_EPS_PEAK`/`_EPS_AGE` unchanged (conservative). **Wash prevention:** surgical `is_charging` binaries ONLY at instants where `wholesale_t·rte > price_t + tol` (elsewhere the wash is loss-making and `_EPS` kills it); drop IPM/crossover kwargs when any binary present. New method `solve_with_export(problem, export_price_by_timestamp)` — reuses `solve()` preamble, aligns prices to loaded timestamps (**full coverage or return None+warning; never interpolate**), £/MWh→£/kWh, does NOT set `_last_schedule`. Move `"export_limit"` DEFERRED→STRUCTURAL (behaviour-neutral; both sets `continue`).
- **T4 — valuation (A3):** `valuation.py` `export_upside_stream_result(base, delta_gbp, export_mwh, price_content_hash, price_source_ref)` rebuilds the WHOLESALE_P415 entry, `annual_revenue_gbp=delta`, CONDITIONAL (schema enforces never-FIRM), notes: incremental-total-value basis vs no-export floor, exported MWh/yr, price `sha256` + coverage window verbatim, "perfect-foresight historical, indicative", "never in P50/P90/DSCR".
- **T5 — engine overlay (A3):** `engine.py` `_export_upside(...)` mirroring `_provisioning_opportunity_cost`: guard on `export_dispatch_enabled(site)` AND B2 price series present AND `isinstance(solver, MilpDispatchSolver)`; fresh solver at `self._seed`; same `DispatchProblem` as `_dispatch_energy_for`; `delta = round(max((overlay.priced_floor_terms + overlay.export_revenue) − floor.priced_floor_terms, 0.0), 2)`; stash + reset in `solve()`; `_final_result` swaps the WHOLESALE_P415 entry via `model_copy` only when stashed; `last_export_upside()` hook for optional later persistence.
- **T6 — tests; T7 — docs** (changelog, tick issue 018, D43, HANDOFF).

T1/T2/T3 land green with ZERO behaviour change (no caller passes prices until T5), so A2 can merge before B2 exists. B2 (price loader + `market_price_series_ref` field + run-identity hash pin) blocks T5 only.

## No frozen-contract schema change
`OptimisationResult`/`DispatchResult`/`ValueStreamResult` untouched — `gated_upside` is already the slot and its validators enforce never-FIRM. The WHOLESALE_P415 £/notes change only when the overlay ran; a run with export upside gets a new run identity (a real input — the price series — entered), while every existing site's hash stays byte-stable.

## Risks (route to optimisation-interface-reviewer)
- **R1 (~80%)** surgical wash binaries: trigger-set correctness (`wholesale·rte > price`), HiGHS B&B determinism under single-thread/seed, annual-horizon runtime. Fallback: full `use_binary=True` overlay + runtime-guard test.
- **R2 (~85%)** reservation envelope kept at `min(power−aux, load)` (never extended by MEC) — conservative; confirm intent.
- **R3 (~85%)** `export_limit` DEFERRED→STRUCTURAL behaviour-neutral but it's Olly's audit surface.
- **R4** `_EPS_AGE` excludes export (epsilon don't-care) — confirm.
- **R5 (~85%)** no foresight haircut on the upside delta (D30 haircut is a floor/finance concept); note declares "perfect-foresight, indicative".
- **R6** consent predicate strictness (G99 vs G99∧G100) — a G1 line-item.
- **R7** price-alignment strictness (full-coverage-or-skip, no interpolation) — matches `allow_interpolation` discipline.

## Acceptance
1. Export off ⇒ every existing test green unchanged, result byte-identical (not even a warning moves).
2. Export on ⇒ floor value_streams £, customer bill, volumes, schedule ref, DSCR/IRR identical to floor-only; only the WHOLESALE_P415 gated_upside entry gains £+notes (CONDITIONAL).
3. Upside £ = delta (≥0, £0 when export never pays), never gross; never in P50/P90/DSCR.
4. Deterministic double-run reproduces delta/MWh; full quality bar green.

**New tests** (`tests/test_optimisation_export.py`): incoherent-consents no-op deep-equal; coherent-but-no-prices no-op; closed-form positive case (exact delta+MWh, floor identical); MEC binds; inverter sharing with `prov_unmet`; reservation curtails export in committed windows; warranty counts export; wash-prevention instant; financial case identical; overlay determinism; delta=0 still notes basis honestly. **Must stay byte-identical:** `test_determinism_golden.py`, `test_optimisation_milp_solver.py` G1–G9, `test_optimisation_commitments.py`, `test_optimisation_provisioning.py`, `test_e2e_examples_real.py`, `test_quotes_assembly.py` hash-stability.
