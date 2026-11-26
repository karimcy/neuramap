# WP-CORE-01: triage of the 27 pre-existing test failures (2026-11-16)

**Owner:** Olly. **Roadmap:** WP-CORE-01 (spec A10-02), "GridQ clean baseline".
**Scope:** the 27 tests that failed on the full suite at HEAD `55b168c` (22 in the fast tier, 5 in
the slow tier). Each cause is established from the code and the git history, and each is classified:

- **(a)** a stale expectation superseded by a documented decision;
- **(b)** a real defect;
- **(c)** environment or solver version;
- **(d)** unknown.

**Rules applied.** An (a) is re-pinned **only** where the new value is recomputed by hand from the
documented change and agrees with the measurement. The evidence is written into the test as a
comment, and each re-pin was **mutation-checked**: the superseding behaviour was reverted in memory
by a pytest plugin (never on disk), and the re-pinned test was confirmed to go red. Where the new
value is not determinate, the test is left untouched and the reason recorded. Nothing was skipped,
xfailed or loosened. Dependency pins (`requirements.txt`, the lock) were not changed.

## 1. Baseline, reproduced

- **Environment.** `uk/.venv`: Python 3.12.11, numpy 2.5.1, highspy **1.15.1**. The lock pins
  highspy 1.14.0; that choice is the owner's (see §5).
- **The 27 reproduce.** Fast tier at `-n 8`: 3,097 tests, of which 20 failed on assertions and 4
  failed plus 6 errored with **`OSError: [Errno 28] No space left on device`** (linopy temp `.lp`
  files and `pytest-of-*` temp dirs; the system volume was 99–100% full during the run, with
  0.5–2.9 GB free). Re-run serially once space was available, the ENOSPC modules give **2 more
  genuine failures**, so the fast tier has 22. The slow tier adds **5 genuine failures**; its 4
  ENOSPC failures and 19 ENOSPC errors were re-run separately (§4). Total: **27**, which matches the
  2026-11-16 19:25 handoff.
- **All 27 fail identically on a clean `git archive` export of HEAD `55b168c`.** The 22 fast-tier
  failures were run there by node id (22/22 failed, same messages). Four of the five slow-tier
  failures were run the same way (4/4 failed); ac9 was traced to `12033d0`. So none is caused by
  today's uncommitted MPC edits.

## 2. Triage table

Commits are cited by hash; "changelog" means `docs/changelog.md`.

| # | Test | Class | Evidence (cause, decision, commit, recomputation) | Action |
|---|---|---|---|---|
| 1–3 | `test_assumptions_gb_v1_11.py::test_gb_v1_11_capex_and_fixed_stack_reach_the_cost_model[25 / 100 / 750 kW]` | **(a)** | **D49** alternative (g) recorded wiring the GBP 1,557 DNO assessment-and-design one-off into capex as "the right thing to do", left it unconsumed, and stated the direction (capex understated). **`f3aedd8`** (FIX 3/7, 2026-08-19; changelog 2026-08-19 item 3) did it: `_fixed_capex_gbp` adds the key to both capex shapes. By hand, capex = 210,000 £/MW × MW + 245 £/kWh × kWh + 1,557: **19,057 / 71,557 / 526,557** (was 17,500 / 70,000 / 525,000). Fixed stack rises by insurance 0.5% × 1,557 = 7.785, giving **595.28 / 2,357.79 / 17,632.78**. Measured: identical. | Re-pinned. Mutation `_fixed_capex_gbp → 0`: 3/3 red. |
| 4 | `…::test_gb_v1_11_net_effect_is_worse_at_the_reference_size` | **(a)** | Same cause (D49 (g), `f3aedd8`). gb_v1_10 has no such key, so it adds 0 there. Fixed delta = −2,600 + 7.785 = **−2,592.21**. Capex delta = 20,000 + 1,557 = **21,557**. Capital charge = 21,557 × CRF(8.9%, 10 y) 0.1551349 = **3,344.24**. Net = **+752.03** (was +502.70). The invariant (net > 0, worse) still holds. | Re-pinned. Mutation: red. |
| 5 | `…::test_gb_v1_11_is_better_for_small_sites_and_worse_for_large_ones` | **(a)** | Same cause. Each net moves by (0.005 + CRF) × 1,557 = +249.33: 25 kW **−1,650.00** (measured −1,650.0004), 750 kW **21,569.56**. The crossover moves from ~84.3 kW to ~76.5 kW; the sign holds at both ends. | Re-pinned. Mutation: red. |
| 6 | `…::test_gb_v1_11_priors_identical_to_gb_v1_10_except_the_enumerated_drift` | **(a)** | **D54** verbatim: "the zone clears are added to gb_v1_11 as `local_flex_piclo.awarded_zone_clears` (context, never a default)". The test and the YAML landed together in **`12033d0`**, and the test already failed at that commit (run on a clean export of `12033d0`). The expectation predates D54. | Re-pinned: the D54 key added to the expected set, with the citation. |
| 7 | `test_local_flex_owner_attested_award.py::test_the_availability_leg_is_priced_from_the_awards_own_mw_and_window_hours` | **(b)** | **`339892c`** (2026-08-19) made the valuation bill the SOLVED candidate (owner-quoted: "its not right that every size would get the same amount for all battery size"; also in the changelog 2026-08-19 D60 entry). It left the pre-solve dispatch reservation (`gb_streams.local_flex_award_constraints` → `clamped_local_flex_awards(site, assumptions)`, no candidate) at the SEARCH FLOOR. The invariant FIX 1 (`e005d08`) established, and the constraint builder's own docstring still states ("so the availability INVOICE and the dispatch RESERVATION read the same volume"), is now false. The commit message says "that property is kept"; it is not. At this test's 250 kW schedule the invoice is £67.64/yr on 46.32 kW (`= 1.33 × 0.04632 × 1,098`), while the dispatch reserves the floor-bound 2.247 kW. The test is red because it asserts the invariant ("THE INVOICED MW IS THE MW IN THE DISPATCH RESERVATION"). | **Not fixed; test untouched.** A proper fix makes the dispatch reservation per-candidate (solver/engine side, `milp_solver.py`/`engine.py`, which carry today's uncommitted MPC edits). The alternative, billing the floor, is the size-invariance the owner rejected. Either way every priced number and the sizing selection move. **Owner decision (§5, item 1).** |
| 8 | `…::test_the_availability_hours_come_from_the_awarded_services_published_count` | **(b)** | Same defect. Harwell at 250 kW: the invoice is 174.99 × 0.21381 MW × 136 h = **£5,088.39** on the declared 213.81 kW, against the floor-bound reservation of 10.37 kW. The hours assertions (136 h, PUBLISHED) still pass; only the £ line and the invariant fail. | As #7. |
| 9 | `test_market_entry_eligibility.py::test_ineligible_streams_do_not_perturb_the_shared_capacity_budget` | **(a)** | **`05133e4`** (FIX 5/7, 2026-08-19; changelog 2026-08-19 item 5, "THE CM DE-RATING LANDS ONCE"): a consented CM rate is treated as quoted on de-rated capacity at the 2 h reference bin, divided back out, and de-rated once on the volume. gb_v0_1's 2 h bin is 0.65: (50 / 0.65) × 0.33 MW × 1000 = **£25,384.62** (was £16,500). The test's own point, no power-budget rescale, is unchanged and still asserted. | Re-pinned. Mutation (no reference bin): red. |
| 10 | `test_milp_dispatch_physics.py::test_deg01_the_cycle_cost_stops_a_trade_whose_own_wear_erases_its_margin` | **(a)** | **D57 limb 1** (DFS "OPEN and PRICED on every site") and **D57 item 4, `205ed92`** (2026-08-18, "build the DFS engine branch"). DFS utilisation is in the objective on every release. The 7-day fixture has 8 DFS-called half-hours at £0.1597/kWh. Measured with wear priced: 19,565.734 kWh/yr discharged, **all** of it DFS delivery (`dfs_delivered_mwh` 19.565734) and **0.0** outside DFS periods. With wear at zero, 117,394 kWh/yr is discharged outside them. DEG-01's claim is intact. | Re-stated where the claim lives: zero discharge outside DFS periods, and total = DFS delivery. Mutation (wear → 0): red. |
| 11 | `…::test_deg01_applies_on_the_aging_weight_branch_too` | **(a)** | Same cause and the same measurement on the aging-weight branch. | Re-stated as #10. **Caveat:** under the wear-to-zero mutation this test stays GREEN. The aging weights alone already suppress the thin trade (measured: 0.0 kWh outside DFS with and without wear), so it never isolated `c_deg`. That weakness pre-dates today and is recorded, not redesigned. |
| 12 | `test_optimisation_milp_solver.py::test_g9_peak_tiebreak_pins_levelled_red_band_discharge` | **(a)** (cause), **not re-pinned** | Same DFS cause (`205ed92`). On day 0 the battery puts 100 kW into the two red half-hours that are also DFS-called (indices 36–37), leaving 32–35 unshaved. The weekly-max epigraph is then set by day 0 (320 kW), so it no longer binds on days 1–4. Their red-band placement becomes a degenerate, solver-chosen vertex (for example [100, 75.2, 0, 100, 0, 100]), not the levelled 62.539 kW. | **Untouched.** The asserted placement is no longer determinate; a re-pin needs a fixture without DFS-called periods, which is a test redesign. **Owner decision (§5, item 3).** |
| 13 | `test_response_market_volume_basis.py::test_a_standalone_fractional_unit_is_still_truncated_away` | **(a)** | **D57 item 2, `7405d4b`** (2026-08-18): the engine volume now comes from `gb_streams.sellable_volume_mw`, which truncates on the standalone route and states why ("sell orders are accepted only in WHOLE MW, so 1 MW of the unit's 1.600 MW is offerable"). The £ (1 MW × rate) still passes. The engine's own "whole-MW sell orders" note fires only when `sellable != mw`, which is now unreachable because `mw` arrives already truncated. | Re-pinned the reason string to its new source. Mutation (volume halved): red. |
| 14 | `test_sizing_deliverability.py::test_producer_asserts_deliverability_only_with_reference_and_evidence` | **(a)** | **D57 limb 2** (aggregation assumed approved) via **D57 item 1, `7405d4b`**: `produce_gate_context` now ALWAYS returns a context carrying `portfolio_mw = 1.0` (the largest sourced minimum) and ALWAYS emits the one D57 note. Was `warns == []`; now exactly that one note. | Re-pinned. Mutation (old no-context return): red. The function's docstring, which still claimed "(None, warnings) … byte-identical", is corrected. |
| 15 | `…::test_producer_never_asserts_from_an_unreferenced_commitment` | **(a)** | Same. Was `ctx is None`; now a context with `deliverability_confirmed` and `local_flex_tender_open` None, `evidence == []`, and portfolio 1.0/1.0. "Nothing assertable" still holds for the deliverability facts. | Re-pinned. The mutation used reverts only the no-commitment branch, so this one stays green under it (stated, not hidden). |
| 16 | `…::test_producer_with_no_commitments_is_a_no_op` | **(a)** | Same. Was `(None, [])`. | Re-pinned. Mutation: red. |
| 17 | `test_valuation_consent_pricing.py::test_cm_consent_and_agreed_rate_prices_at_the_agreed_rate` | **(a)** | **`05133e4`** (FIX 5/7), as #9: (50 / 0.65) × 0.2 MW × 1000 = **£15,384.62** (was £10,000). | Re-pinned (literal plus formula). Mutation: red. |
| 18 | `…::test_bm_consent_and_agreed_rate_prices_and_moves_into_the_priced_floor` | **(a)** | **D57 item 2, `7405d4b`**: the BM volume is the unit's own MW, not de-rated (D-102). £10,000/MW/yr × 4.0 MW = **£40,000** (was £20,000 on 2.0 MW de-rated). | Re-pinned. Mutation: red. |
| 19 | `…::test_ancillary_consent_and_agreed_rate_prices_into_the_floor` | **(a)** | Same. The record's own arithmetic: "£8,000.00/MW/yr x 4 MW x F 1.0000 x K 1.0000 = £32,000.00/yr" (was £16,000). | Re-pinned. Mutation: red. |
| 20 | `test_valuation_zero_dispatch_note.py::test_zero_dispatch_note_states_the_decision_and_its_arithmetic` | **(a)** (cause), **not re-pinned** | The DFS cause (`205ed92`). The fixture's premise, "the optimum is a NULL dispatch", cannot hold while DFS is in the objective: every kWh discharged is DFS delivery (measured, 0.0 outside DFS periods). The zero-dispatch note keys on `annual_discharge_kwh == 0`, so it never fires. | **Untouched.** No determinate new value. **Owner decision (§5, item 2):** with DFS priced on every site, `valuation._zero_dispatch_note` (the "declined a loss-making trade" explanation) is unreachable on any site with DFS-called periods. The question is whether it should key on non-DFS discharge. |
| 21 | `test_no_spec_dispatch_evidence.py::test_zero_dispatch_record_reports_a_measured_zero_with_its_arithmetic` | **(a)** (cause), **not re-pinned** | Same. The zero-dispatch fixture's best candidate (350 kW / 3 h, gb_v1_8) discharges 34,414.286 kWh/yr, **all** DFS (0.0 outside DFS periods), so the record takes the "DISPATCH WAS SOLVED AND RAN" branch instead. | Untouched, as #20. |
| 22 | `…::test_value_measured_now_distinguishes_the_two_records` | **(a)** (cause), **not re-pinned** | Same (`StopIteration`: no zero-dispatch note exists to read). | Untouched, as #20. |
| 23 | `test_register_moves_no_priced_number.py::test_the_register_is_read_only_by_identity_disclosure_and_the_confidence_band` | **(a)** | **D56** limb 2 ("every assumption is enumerated on a dedicated assumptions page in every report"), landed in **`12033d0`**: `outputs/viewmodel._assumptions_page` reads `site.assumptions_requiring_confirmation` to list the records. It is an `outputs/` renderer outside PRICING_PATH and moves no £; the pricing-path sweep in the same file still passes. | Re-pinned: added to the allowlist with its reason (the deliberate act the test asks for). |
| 24 | `test_windowed_levy_capacity_market.py::test_capacity_market_levy_is_what_makes_the_optimiser_discharge_in_the_window` | **(a)** (cause), **not re-pinned** | The DFS cause (`205ed92`). Without the levy: 12,513.613 kWh/yr discharged, all DFS (0.0 outside). With it, the in-window share is **0.8687** against the test's 0.9 threshold, because DFS competes for discharge (119 DFS-called half-hours, 42 overlapping the CM window). | **Untouched.** The 0.9 is a judgement threshold, not a derived value; re-pinning means choosing a new one. **Owner decision (§5, item 3).** |
| 25 | `…::test_windowed_levy_raises_year_one_cashflow_at_a_FIXED_system_size` | **(a)** (cause), **not re-pinned** | The DFS cause. The test's premise "the floors are identical and only the credit differs" no longer holds. With the levy, DFS falls £2,051.97 → £1,744.84 and DUoS rises £3,761.13 → £3,929.69: the optimiser trades DFS half-hours for CM-window discharge. Its own claims (year-1 on > off, by more than £1,000) are not what fails; the attribution equality is. | **Untouched.** The attribution needs restating as a law (delta = (credit + Δfloor) × availability, Δfloor ≤ 0), which is a test redesign. **Owner decision (§5, item 3).** |
| 26 | `test_priced_floor_arithmetic.py::test_f03_the_levy_stack_stops_a_cycle_whose_margin_it_erases` | **(a)** | The DFS cause (`205ed92`). Levied arm: 19,565.734 kWh/yr discharged, all DFS, 0.0 outside DFS periods. Charge 22,233.789 = 19,565.734 / 0.88. "Arbitrage" = that DFS cycle's own spread, 19,565.734 × 0.1363 − 22,233.789 × 0.10 = **£443.43** (exact). Unlevied: 117,394 kWh/yr outside DFS. F-03's claim is intact. | Re-stated: zero outside DFS, total = DFS delivery, RTE identity, arbitrage = hand formula. Mutation (levy out of objective): red. |
| 27 | `test_e2e_examples_real.py::test_ac9_outputs_present_the_verdict_not_an_empty_spec` | **(a)** | **`12033d0`**, documented in the changelog 2026-08-17 entry "A null `financial_case` is no longer presented as an economic verdict": the report's system card now states the recorded outcome, "No system was selected.", and reproduces the engine's reason. The old claim "none is economically viable on the priced value floor" was retired because no-spec records carry no case. This is the same class as the `cc36f04` reword the test already tracks. ac4 (the NO_VIABLE_CONFIGURATION status) still passes. | Re-pinned to the literal in `html_report._system`. |

## 3. Summary by class

| Class | Count | Re-pinned | Left untouched |
|---|---|---|---|
| (a) stale expectation, documented decision | **25** | **19** | 6 (#12, #20, #21, #22, #24, #25): the cause is established, the new value is not determinate |
| (b) real defect | **2** | 0 | 2 (#7, #8): fix needs an owner decision and the uncommitted solver files |
| (c) environment or solver version | **0** of the 27 | — | — |
| (d) unknown | **0** | — | — |

**The decisions behind the 25 (a)s.** 9 are D57 limb 1 / item 4 (DFS in the objective,
`205ed92`): #10–12, #20–22, #24–26. 6 are D57 items 1–2 (`7405d4b`): #13–16, #18, #19. 5 are D49 (g)
with FIX 3 (`f3aedd8`): #1–5. 2 are FIX 5 (`05133e4`): #9, #17. 1 is D54 (#6), 1 is D56 (#23), and
1 is the `12033d0` report reword (#27). The FIX 3 and FIX 5
arithmetic corrections and the 12033d0 report reword are documented in `docs/changelog.md` and their
commit messages, but have **no D-number** in `docs/decisions.md`; FIX 3 also rests on D49 (g).

**Class (c), outside the 27, observed today.** (i) **Disk exhaustion.** The system volume ran at
99–100% during the runs, producing 4 + 6 fast-tier and 4 + 19 slow-tier ENOSPC failures and errors.
All are transient and all pass on re-run once space is free (§4). CI is unaffected, but a local
`-n 8` run on this machine is not trustworthy below a few GB free. (ii) The golden pins pass on the
venv's highspy 1.15.1 and fail on the lock's 1.14.0 (handoff 2026-11-16 19:25); see §5.

## 4. Counts after triage

Measured on the working tree after the edits above. Fast tier: `.github/ci/run_tests.sh fast` at
`PYTEST_WORKERS=4`. Full suite: fast + slow + the four heavy modules, each heavy module run alone.

| | Tests | Failed | Errors | Skipped |
|---|---|---|---|---|
| Fast tier, before | 3,097 | 22 | 0 | 0 |
| Fast tier, after | **3,317** (3,097 + 220 new `tests/contracts/` from a concurrent session) | **6** | 0 | 0 |
| Slow tier, after | 186 | **2** | 0 | 1 |
| Heavy modules, after (each run alone) | 18 (golden pins 4/4) | 0 | 0 | 0 |
| Full suite, before | 3,301 | 27 | 0 | 1 |
| **Full suite, after** | **3,521** (3,301 + 220 contracts tests) | **8** | 0 | 1 |

The 8 remaining failures are exactly the ones left untouched by design: #7 and #8 (class b), and
#12, #20, #21, #22, #24 and #25 (class a, not determinate). None is new. The 220 concurrent
`tests/contracts/` tests all pass.

**ENOSPC errors re-run.** The disk-full failures and errors from the first runs were re-run once
space was free: 4 fast-tier modules serially, then `test_optimisation_engine`,
`test_two_stage_sizing`, `test_hgv_provisioning_e2e` and `test_customer_bill` at `-n 3`. All pass.
The final slow tier above ran with 41 GB free and shows no ENOSPC.

## 5. Decisions for the owner

1. **Local-flex invoice vs reservation (#7, #8, a class-(b) defect).** Either (i) make the dispatch
   reservation per-candidate (a solver/engine change: every priced number and the golden pins move),
   or (ii) bill the reservation (size-invariant, which you rejected), or (iii) record that the
   invoice may exceed the reservation, retire the FIX 1 invariant, and re-pin #7/#8 to the solved
   candidate. Today the pack bills availability on up to the declared award MW while the dispatch
   holds only the search-floor MW, and the dispatch is free to sell the rest elsewhere.
2. **The zero-dispatch explanation is unreachable (#20–#22).** With DFS in every objective, no site
   with DFS-called periods ever dispatches zero, so the "declined a loss-making trade" note never
   fires. Should it key on non-DFS discharge?
3. **Three fixtures whose premise DFS broke (#12, #24, #25).** Re-design each fixture without
   DFS-called periods, or restate each claim as a law. Either is a test redesign, which this
   triage does not do on its own authority.
4. **D-numbers for FIX 3, FIX 5 and the 12033d0 reword.** Each moves a priced or rendered figure and
   is documented only in the changelog and commit messages.
5. **highspy 1.14.0 (lock) vs 1.15.1 (venv/CI).** Re-mint the golden pins on the lock, or bump the
   lock. Not changed here.

## 6. Closures (2026-11-23 to 2026-11-25, branch `roadmap/wp-uw16-core01`)

| # | Closed by | Evidence |
|---|---|---|
| 7, 8 | D61, `60af5a1` (per-candidate reservation; the invoice bills the solver's own reservation record) | Both pass **with zero edits** (`git diff 3163dff -- tests/test_local_flex_owner_attested_award.py` shows additions only, from line 734). The `reserved is None` branch they exercise is the seam default. T1 to T9 guard the per-candidate path; mutations M-T3 to M-T9 each red (handoff 2026-11-23). |
| 12 | D62 limb 3, `1cf52d8` | Moved to a no-DFS fixture (`IV1 1AA`); every original assertion unchanged. M12 (`_EPS_PEAK = 0.0`) red. |
| 20, 21, 22 | D62 limb 1, `1cf52d8` | The zero-dispatch note keys on non-DFS discharge. M20 red on #20; M21 red on #21 and #22 (#22 green under M20, so it is guarded by M21). |

Still open: **#24, #25** (D62 limb 2, slow tier, WP-CORE-01 step 9.10). They are not part of
WP-UW-16.
