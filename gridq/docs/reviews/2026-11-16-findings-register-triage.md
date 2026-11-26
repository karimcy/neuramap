# Findings register triage, 2026-11-16 (WP-CORE-02, spec A10-01)

**Supersedes the STATUS columns of** [`2026-08-12-findings-register.md`](2026-08-12-findings-register.md)
and [`2026-08-16-tier-a-status-verification.md`](2026-08-16-tier-a-status-verification.md). Their
structure still stands: the id-prefix convention (§0), the KILLED list (§8) and the collision map.
Only the statuses are re-derived here.

| | |
|---|---|
| Verified against | `uk` HEAD `55b168c` plus the working tree of 2026-11-16. The uncommitted MPC fix in `optimisation/{mpc_solver,milp_solver,engine,battery_model}.py` counts as current code; every verdict that depends on it says so. |
| Method | A code predicate per finding, verified by signature rather than line number. The production call path is traced from `cli.py`, `quotes/pipeline.generate_quote` or `app/`. The fixing commit comes from `git log -S`, and the guarding test is named. |
| Call-path audit | `python -m gridq.dev.call_path_audit --no-baseline --check A,B`: 250 findings in 1.98 s. The full listing is in [`2026-11-16-findings-register-evidence/call_path_audit_A_B_2026-11-16.txt`](2026-11-16-findings-register-evidence/call_path_audit_A_B_2026-11-16.txt). The lines relevant to each finding are quoted in its evidence section. |
| Evidence | One section per id: the claim, the grep or trace output, the verdict and the work package. Files: [`finance.md`](2026-11-16-findings-register-evidence/finance.md), [`dispatch.md`](2026-11-16-findings-register-evidence/dispatch.md), [`value_stack_ingestion.md`](2026-11-16-findings-register-evidence/value_stack_ingestion.md). |
| Work packages | Ids from `atlas/roadmap/roadmap.yaml`. |
| Read-only? | Yes for the re-verification. Two findings were closed in this session by documentation-only edits (C-11, and the CLAUDE.md claims under C-1 and C-6), and the DEG-01 guard tests were re-pinned under WP-CORE-01 ([`core01_failure_triage.md`](../shared_work/core01_failure_triage.md)). |

**Acceptance (WP-CORE-02).** "Every finding has status closed, or triaged with a workstream and
reason." Every row below is either **CLOSED** (fixed, superseded or invalid, with evidence) or
**TRIAGED** (open or partial, with a work package or, where none fits, a workstream and the gap
named). "Call-path audit output is attached for each re-verified claim": see the Evidence row
above.

## 1. Counts

The register's universe is its **74 non-fixed filed ids** (66 open + 8 partial). That set is
reconstructed exactly from WP-0 §2–§6, hostile-review §4, the JSON findings file and the register's
own §2–§4 and §10. To it are added the post-register filings: the 08-15 update block, the 08-16
items R1, R2, R8, `dfs_performance_factor`, `eac_response_value(toll_reference=)` and mypy-over-tests,
the engine-review `ER-N1`…`ER-N7`, the MPC quarantine and blocker B001.

| Status | Rows | Notes |
|---|---|---|
| **CLOSED: fixed** (production call site and guarding test named) | 18 | §2.1: 6 · §2.2: 8 · §2.3: 4. Includes N9, N10, N11, N28, H-17 and JSON-F-51, which the register still lists as open or partial. B-01 is closed for the annual MILP; its MPC-simulator residual is triaged on the same row. |
| **CLOSED: fixed in documentation this session** | 1 (C-11) | Plus the CLAUDE.md corrections in §4 |
| **TRIAGED: open** | 45 | §2.1: 13 · §2.2: 17 · §2.3: 15. Each has a work package, or a workstream where no package fits (§5). |
| **TRIAGED: partial** | 18 | §2.1: 6 · §2.2: 6 · §2.3: 6. The open half is named and triaged. |
| **Register contradictions (§2.4)** | 10 | 9 closed; C-13 triaged |
| **Superseded or invalid** | 0 whole findings | JSON-F-34 limb (a) is invalid and JSON-F-33 is half policy (D56); both stay PARTIAL for the other half |

A row may carry merged ids (for example `N13 ≡ H-15`), so the row counts are not id counts.

*Since this snapshot:* WP-UW-16 (2026-11-25) closed D-130 (§2.1, partial to closed). WP-UW-03
(2026-11-26) closed B001 and N25 and moved H-05 and H-03 from open to partial (§2.1). WP-UW-07
(2026-11-26) closed five rows in §2.2 with call site and test named: B-17 and JSON-F-38 ≡ F-41
(were partial), and JSON-F-39, MD-F-18 and R8 (were open). Current rows: **26 closed: fixed**
(§2.1: 9), **38 triaged open** (§2.1: 9) and **17 triaged partial** (§2.1: 7). The table above is
kept as the 2026-11-16 snapshot.
**No finding is left unstatused.**

## 2. The register

Status key: **C** = closed, **T-O** = triaged open, **T-P** = triaged partial. The evidence for each
row is in the named file, under `### <id>`.

### 2.1 Finance, debt, pricing, scenarios, library (evidence: `finance.md`)

| id | Status | WP | Reason / evidence pointer |
|---|---|---|---|
| N1 (Tier B) | T-O | WP-UW-14 | No tax line exists anywhere. The PRE-TAX disclosure has landed (C-7). It needs the owner's tax basis. |
| N3 | T-P | WP-UW-09 | Netting is wired at `financial_model.py:254`. Still open: (a) `annual_dispatch_cost_gbp` is not in the cost base; (b) discounting uses the equity hurdle on total capex; (c) netting happens before the perfect-foresight adjustment and the availability de-rate. **No test fails if the netting kwarg is deleted.** |
| N10 | C | — | `debt.py:348` refuses a partial-window minimum DSCR. Fixed in `642621b`; guarded by `test_finance_debt.py::test_n10_horizon_shorter_than_tenor_returns_unavailable_not_a_partial_minimum`. |
| N11 | C | — | `returns.py:363` extends to max(horizon, tenor). Fixed in `642621b`; guarded by `test_finance_returns.py::test_n11_post_horizon_debt_service_is_never_dropped_from_the_equity_series`. |
| N12 | T-P | WS-UW (no package fits; see §5) | `hits_target_return` is now measured. Price is still levelised over flat year-1 volume (`pricing.py:210`). **No wiring test exists for the measured flag.** |
| N13 ≡ H-15 | T-O | WS-UW (no package fits; see §5) | Cashflows are flat-nominal. `financing.cpi_pct_per_year` (2.0, since gb_v1_8) has no reader, so this is no longer purely Tier B. |
| N14 (Tier B) | T-O | WS-UW (no package fits; see §5) | No terminal value, decommissioning or replacement flows. Disclosure has landed (C-7). |
| N25 ≡ MD-F-14 | **C** (WP-UW-03, 2026-11-26) | — | Was reproduced: gearing 60 vs 40 and fixed price vs capacity fee produced the same `case_id`. `src/gridq/finance/financial_model.py:377` now passes the injected debt terms, the lender terms and the price structure into `_hash_input`, which adds each key only when present or non-default (`:623`, `:625`, `:627`), so a run without them keeps its historical case hash input (D78). Fixed in `06b4137`; guarded by `tests/test_lender_terms.py::test_debt_terms_lender_terms_and_price_structure_split_the_case_id` (red if any of the three payload lines is deleted, mutation-checked) and `tests/test_lender_terms.py::test_a_run_without_terms_keeps_its_historical_case_hash_input`. |
| N26 | C | — | `finance/cashflows.py:856` (parasitic line) and `financial_model.py:362` both call `blended_import_rate_gbp_per_kwh`, which adds `levies`. Fixed in `574487a`/`cfe7120`; guarded by `tests/test_finance_blended_import_rate.py::test_the_parasitic_line_is_priced_at_the_delivered_rate_including_levies`. Re-verified in this session (no evidence-file section). |
| N28 | C | — | gb_v1_10+ carries the 0.5–12 h de-rating curve. Fixed in `d779eb8`; guarded by `test_cm_derating_curve_and_penalty_cap.py::test_every_duration_the_search_can_produce_is_priced_at_its_published_bin`. |
| N31 | T-O (latent) | WP-UW-10 | `scenarios/engine.py:130` still scales net cashflow. Reachable from `outputs/generate.py:104`, with neutral multipliers only. |
| H-03 (Tier B) | T-P (**advanced**, WP-UW-03, 2026-11-26) | WP-UW-09 | Supplied lender terms with a sculpted profile and a `sculpting_target_dscr` now size the debt as min(capex × max gearing, PV of the case CFADS at the target) (`finance/debt.py:405` `target_sculpted_schedule`, D77; guarded by `tests/test_lender_terms.py::test_a_sculpting_target_sizes_the_debt_when_the_cfads_supports_less_than_the_cap`). Still open under WP-UW-09: every other run is capex × gearing, and the sizing loop (`MilpEngine`) never sees the covenant. |
| H-04 | T-O | WP-UW-09 | P90 EBITDA is passed as CFADS (`financial_model.py:189`). |
| H-05 (Tier B) | T-P (WP-UW-03, 2026-11-26) | WP-UW-09 | The tiers are closed: `LenderTerms.covenant` states a default (event-of-default) and a lock-up DSCR, and `src/gridq/finance/financial_model.py:231` takes them from the terms with no library fallback (D76); `CovenantStatus` carries `lock_up_dscr`, `lock_up_passes` and `source`. Guarded by `tests/test_lender_terms.py::test_covenant_tiers_come_from_the_terms_with_a_lock_up` and `::test_terms_without_a_covenant_are_never_tested_against_the_library_placeholder` (both red if that line is deleted, mutation-checked). Open half: the test period is recorded but only an annual test is measured; a semi-annual or quarterly term sheet gets DSCR unavailable with the reason (`::test_a_semi_annual_test_period_is_refused_not_annualised`) until the model has sub-annual CFADS (WP-UW-09). |
| H-08 | T-O | WP-UW-08 | P90 is a flat 15% placeholder haircut, which the stochastic engine replaces. |
| H-16 | T-O | WS-UW (no package fits; see §5) | No construction phase or IDC. Check B: `cod_delay_months` is never supplied. |
| H-17 | C | — | The 95% availability de-rate is live on gb_v1_12 (`cashflows.py:1841`). Fixed in `cfe7120`; guarded by `test_finance_tenor_and_availability.py::test_the_release_that_carries_the_sourced_figure_activates_the_derate`. |
| D-33 | T-P | WP-UW-09 | The CM-flat-for-term path is built, but no production caller supplies a CM term. No CPI. |
| D-130 | **C** (WP-UW-16, 2026-11-25) | — | Was live: `site.local_flex_awards[].tenor_years` never reached finance (`pipeline.py:484` passes no `commitments=`), so Tanhouse's 1-year award earned for 15 years. Fixed where every path arrives: `finance/financial_model.py:168` reads the site's own awards ahead of any caller's commitments, so the quote, the sensitivities and the sizing loop's finance runner all apply the term. Guarded by `test_finance_tenor_threading.py::test_site_award_tenor_reaches_finance_from_generate_quote` (T10) and `::test_sizing_finance_runner_sees_the_award_tenor` (T11), both red if that line is deleted; P90/lender treatment per D66, P50 per D65 (T12). |
| D-25 | T-O | WP-UW-10 | The auction and delivery-year de-rating table is in the library; no code reads it. |
| JSON-F-32 | T-O (latent) | WP-UW-01 | The solver's best status is `REVIEW_REQUIRED` and finance needs `bankable_candidate`, so neither can pass. |
| JSON-F-33 | T-P | WP-UW-01 | Taking 12% is D56 policy. The library note still says "chosen conservatively", which is false. |
| JSON-F-34 | T-P | WP-DATA-02 | Limb (a) is invalid: 15 of 17 historical tables are not duration-proportional. Limb (b) is open: no fetch is checked against its pinned hash. |
| JSON-F-51 | C | — | Both parasitic keys equal 5 kW/MW in gb_v1_12. Fixed in `1964949`; guarded by `test_all_streams_study_basis.py::test_the_two_parasitic_keys_now_carry_one_reconciled_value`. |
| B001 (blocker) | **C** (WP-UW-03, 2026-11-26) | — | Was live: `min_dscr=None` on all 12 latest gb_v1_12 runs, because no real site has a credit band and neither the CLI nor the app could supply debt terms. Fixed by a typed lender-terms input (`schemas/lender_terms.py`, D75) on every entry point: `src/gridq/cli.py:411` (`gridq run --lender-terms FILE`), `src/gridq/app/app.py:1216` (`/api/run`) and `src/gridq/app/app.py:1325` (`/run/real`) pass it to `generate_quote`, which threads it at `src/gridq/quotes/pipeline.py:578` into `run_financial_model`, where `src/gridq/finance/financial_model.py:282` sizes the debt on the terms. A run without terms now carries `DscrSummary.status = no_debt_terms` and the reason "No lender terms supplied: …" (`src/gridq/finance/financial_model.py:493`), never a silent `None` and never a default figure. Fixed in `06b4137` and `2892dc6`; guarded by `tests/test_dscr_on_every_run.py::test_a_run_with_lender_terms_shows_a_measured_dscr` (pipeline and finance lines), `tests/test_dscr_on_every_run.py::test_cli_run_with_lender_terms_records_and_prints_the_dscr` (CLI line), `tests/test_dscr_on_every_run.py::test_app_wizard_hands_the_lender_terms_to_the_pipeline` and `tests/test_dscr_on_every_run.py::test_app_real_form_hands_the_lender_terms_to_the_pipeline` (app lines), and `tests/test_dscr_on_every_run.py::test_a_run_without_debt_terms_shows_dscr_unavailable_with_the_reason` (the reason line); each is red when its line is deleted (mutation-checked 2026-11-26). A real site's DSCR now needs only its lender's terms: a data ask, not a code gap. |

### 2.2 Dispatch MILP, MPC, engine, schedule, tariff calendar, sizing (evidence: `dispatch.md`)

| id | Status | WP | Reason / evidence pointer |
|---|---|---|---|
| DEG-01 | C | — | `deg_term = deg_cost * (export_flow * w_da * dt).sum()` (`milp_solver.py:1948` at HEAD), fixed in `fd6dfd6`. **Its two guard tests had been red since the DFS objective term (`205ed92`) and were re-pinned in this session (WP-CORE-01).** With zero wear, `test_deg01_the_cycle_cost_stops_a_trade_whose_own_wear_erases_its_margin` now goes red. The aging-branch twin does NOT go red: the aging weights alone already suppress the thin trade, a pre-existing weakness that is recorded and not redesigned. |
| B-01 | C (annual) / T-O (MPC simulator) | WP-SIZE-01 | √RTE split in the SoE balance (`fd6dfd6`, tests pass). `battery_model.py:86-88` still books all losses on charge. |
| D-169 | C | — | SoE is bounded by the release's 0.15–0.95 window; tests pass. |
| B-17 | **C** (WP-UW-07, 2026-11-26) | — | Was: unity power factor as a labelled warning, no reactive variable or charge. Now (D106), when the half-hourly file meters both reactive channels, the MILP carries `c_t` inside the inverter's inscribed P-Q octagon, holds the connection to `min(MIC, ASC)` in kVA (`milp_solver.py:627` `_add_connection_kva_rows`), and prices the LC14 chargeable kVArh at the stack's published CDCM excess reactive rate (`_add_reactive_excess_epigraph` :662, `_reactive_charge_term` :692). The unity warning is retired on exactly those runs (:729) and stands, unchanged, on every run without the channels (:759). Guarded by `test_reactive_power.py` (11 tests, incl. `test_reactive_has_a_variable_and_a_charge_and_the_unity_warning_is_retired`, `test_the_bill_line_is_the_exact_lc14_rule_hand_recomputed`); red if the charge term returns 0. Still true, and stated in D106: no shipped site's half-hourly file carries the reactive channels yet, so every shipped run keeps the unity warning; the MPC schedule and the BEFORE-only bill carry no reactive line. |
| D-174 ≡ JSON-F-49 | T-P | WP-SIZE-05 | Signed net load is used in `MilpDispatchSolver.solve`, but the production sizing path (`two_stage.py:358` → `proxy_solver.py:88`) prices on the clipped series and warns that it does not. No campaign site has negative reads today. |
| B-09 | C | — | MIP gap set at 1e-6 and the achieved gap read back; tests pass. |
| B-14 ≡ JSON-F-47 | T-O | WP-UW-08 | P50 is the perfect-foresight LP with an uncited 20% haircut. |
| JSON-F-29 | T-O (mitigated) | WP-SIZE-06 | Flex utilisation is read off the arbitrage optimum and paid on top of it. |
| JSON-F-42 | T-O (latent) | WP-SIZE-02 | A missing warranty-cap key silently removes the cap. |
| JSON-F-46 | T-P | WP-SIZE-01 | The annual LP is fixed. The MPC side is fixed only in **uncommitted** code, and only on releases that declare a window. |
| JSON-F-44 | T-O (latent) | WP-DISP-04 | No cross-window warranty throughput in the MPC. |
| JSON-F-45 | T-O (latent, wider) | WP-DISP-02 | The MPC values on the national calendar while its inner solves use per-DNO windows (LIT-F-05). |
| JSON-F-40 ≡ F-43 | T-P | WP-SIZE-04 | The data tier caps confidence. The £ is still a flat 8760/covered scaling with no coverage gate. |
| JSON-F-53 | T-O | WP-DISP-07 | Binding detection ignores aux draw, provisioning, power-factor de-rate and signed load. |
| JSON-F-54 | T-O | WP-CORE-03 | The schedule hash includes sub-tolerance placement (first 48 half-hours at 4 dp). |
| LIT-F-05 | C | — | `dno_region` reaches the production MILP (`milp_solver.py:1466-1477`, `duos.band_windows_by_dno`). Fixed in `d779eb8`; reader-layer test passes. |
| JSON-F-38 ≡ F-41 | **C** (WP-UW-07, 2026-11-26) | — | Was: the mean-of-bands price entered the objective. Now (D105) every resolved half-hour is priced as its own band and every unresolved one carries a no-spread envelope (sell at the lowest quoted rate, buy at the highest): `tariff_calendar.py:837` `energy_price_envelope`, fed to the objective by `energy_objective_coefficients` (:957, `milp_solver.py:1943`). No mean is computed anywhere. Guarded by `test_energy_price_envelope.py::test_the_milp_objective_carries_no_mean_of_bands` and `::test_no_cycle_earns_spread_from_the_unpriced_band`; red if the envelope is replaced by the mean. |
| JSON-F-39 | **C** (WP-UW-07, 2026-11-26) | — | Was: peak/offpeak mapped onto the DUoS red window silently. Now peak/off-peak with no windows is unresolved (no-spread envelope, warned) until the stack states its own `import_rate_windows` (`tariff_calendar.py:837-900`). Guarded by `test_energy_price_envelope.py::test_peak_offpeak_with_no_windows_is_unresolved_not_mapped_onto_the_duos_red_band` and `::test_the_stacks_own_windows_price_each_band_as_itself`. |
| MD-F-18 | **C** (WP-UW-07, 2026-11-26) | — | `TariffStack.basis` (VAT included/excluded with its rate, levies and network itemised or in the unit rates, and how the VAT basis is known) and `TariffStack.provenance` are required (`schemas/tariff.py:368-370`; IC-12 chassis 3.0.0, D103/D104). A derived basis must carry its `DerivedAssumption`. A VAT-inclusive stack is priced net once, in the pipeline (`quotes/pipeline.py:420`). Guarded by `test_tariff_basis.py` (incl. `test_a_stack_without_a_basis_is_refused`, `test_the_pipeline_prices_the_net_stack_and_records_the_supplied_one`) and `tests/contracts/test_ic12_chassis.py`. |
| N27 | T-O (latent) | WP-SIZE-04 | `best` is not gated on viability. Check A: `sizing/search.py` has no production caller. |
| N29 | T-O | WP-SIZE-04 | Proxy mode (the production default) double-appends confirmed candidates. |
| B-10 | T-O | WP-UW-10 | No RTE sensitivity axis. |
| C-11 | C | — | **Closed in this session.** The `schedule.py` `priced_floor_terms_gbp` docstring no longer says "each clamped at £0" (the components have been signed since MD-F-02, `schedule.py:161-189`), and it now names the levy, windowed-levy and DFS terms it excludes. Documentation only, so there is no call site. |
| R8 (engine bill basis) | **C** (WP-UW-07, 2026-11-26) | — | Was: `engine.py:2783-2784` said "priced on its own tariff stack" on every site, Harwell included. Now one producer, `tariff_calendar.py:1096` `tariff_provenance_phrase`, says it only when every component of `TariffStack.provenance` is the site's own; the BEFORE-only bill (`engine.py:2814`), the solved bill (`milp_solver.py:1203`) and the consulting report (`consulting_report.py:1137`) all render through it. No shipped site is "own" today. Guarded by `test_tariff_provenance_sentences.py` (20 tests, incl. an AST scan proving no other string in `src/gridq` can say it). |
| MPC quarantine (A10-05) | T-P | WP-SIZE-01 | The silent failure is fixed in uncommitted code (handoff 2026-11-16 19:25), and `tests/test_mpc_infeasibility_regression.py` is untracked. The quarantine stays by owner decision. |
| ER-N1 | C | — | Two-anchor search box (`d779eb8`); the end-to-end test passes. |
| ER-N2 | C | — | The demo uses the engine's capacity basis (`959e7cd`); parity tests pass. |
| ER-N3 | C | — | The demo delegates export netting to the engine (`959e7cd`); tests pass. |
| ER-N4 | T-O (latent) | WP-SIZE-06 | The rejected alternative netting is still callable and test-pinned. Re-homed from the agent's WP-CORE-01: it is a netting-design item, not a baseline item. |
| ER-N5 | T-O (latent, labelled) | WP-SIZE-06 | The rebase is a no-op when no dispatch-derived floor stream is present. |
| ER-N6 | T-O (cosmetic) | WP-CORE-01 | Stale test name. |
| ER-N7 | T-O (cosmetic) | WP-CORE-01 | Message says "half-hours" at any interval. |

### 2.3 Value stack, valuation, ingestion, provenance, publication (evidence: `value_stack_ingestion.md`)

| id | Status | WP | Reason / evidence pointer |
|---|---|---|---|
| MD-F-04 + D-92 | T-P (by decision) | WP-UW-01 | The D-92 predicate is built and wired on both paths, but D57 satisfies it on every run with an assumed portfolio (`sizing/deliverability.py:260`). D57/D58 deliberately pay the power/energy budget in full (`e684e66`). No SoC-headroom reservation exists. The enforcement belongs on `underwriting_basis_v1`, not the study basis. |
| MD-F-05 limb (b) | C | — | `engine.py:2359` rebases the floor onto the overlay dispatch (present at HEAD), and the demo delegates to it. Fixed in `574487a`; guarded by `test_engine_connection_basis.py:637`. |
| MD-F-12 | T-O | WP-UW-05 | Load service: the gate opens on `any(r.reference)` and the pricers sum every requirement's £. |
| JSON-F-50 | T-O (latent) | WP-CORE-03 | The proxy prices MIC/ASC headroom at £12k/MW/yr FIRM against D57's "£0, carried by AGREED_CAPACITY_REDUCTION". **`tests/test_value_stack_value_functions.py:121` pins the double count.** |
| D-158 | T-P | WP-SIZE-09 | EFA/F/K structure is wired (`valuation.py:1136`), but F is null and K is unsourced, so both are held at 1.0. No product split. |
| D-116 | T-P (**labelled**, WP-UW-16, 2026-11-25) | WP-UW-11 | The availability leg is priced (`6e19c08`). No release carries a sourced `utilisation_performance_ratio`, and none is invented, so the leg stays gross, but it is now **labelled gross in `quote.warnings`** on every awarded site (`quotes/pipeline.py:487`, `LOCAL_FLEX_GROSS_LABEL`). One accessor, `gb_streams.sourced_utilisation_performance_ratio`, feeds both the price (`valuation.py:799`) and the label, and it accepts only a CONFIRMED entry with a non-empty source (D68). Guarded by `test_local_flex_gross_label.py` (T13 to T17). Open under WP-UW-11: the ratio itself (DATA ASK: the DNO's settlement statements or a published delivery ratio). |
| JSON-F-30 | T-O (retained under D57) | WP-SIZE-09 | The CM fallback prices `power_mw × derate` with no committed-kW cap. Labelled "D57 deliverability ASSUMED". |
| E-06 | C | — | `MarketStream.DFS` is priced from the dispatch (`valuation.py:1783`, `205ed92`) and zoned under D60. Guarded by `test_dfs_zonal_basis.py`. |
| MD-F-06 | T-O | WP-CORE-03 | `input_hash` pins the library by version string only. |
| MD-F-09 | T-O | WP-CORE-03 | `input_hash` pins the HH file path, not its content. |
| MD-F-13 | T-O (latent) | WP-GRC-12 | Reprice skips the optional sections. Check A: `assess_quote_reprice` has no production caller. |
| JSON-F-31 | T-O | WP-CORE-05 | `gridq render` binds any site JSON to any quote. |
| MD-F-08 | T-O | WP-SITE-03 | Inferred granularity is warned and then ignored. |
| MD-F-10 + H-1 (= C-6, Gate 3) | C | — | `quotes/pipeline.py:367` calls `reconcile_declared_against_measured`, and `:603`/`:641` pass `reconciliation=`. Fixed in `4e79f3a`; guarded by `test_gates_record_and_continue.py::test_gate_3_runs_on_the_real_path_and_reports_a_reconciled_basis`. |
| JSON-F-25 | T-O | WP-SITE-03 | `quality_flag` is read by nothing. |
| JSON-F-28 (Tier B) | T-O | WP-SITE-03 | No plausibility screen beyond the H-1 dropout screen. Thresholds need a human decision. |
| N9 (both routes) | C | — | Route 1: `hh_synthesis` stamps MODELLED (`642621b`). Route 2: committed at HEAD (`d779eb8`), called at `pipeline.py:355`. Both are tested. |
| JSON-F-35 | T-P | WP-CORE-03 | The engine detects peak > min(MIC, ASC) (`engine.py:1879`), but `validate_site_model` does not. |
| JSON-F-36 | T-O | WP-CORE-03 | No power × duration = energy validator. |
| JSON-F-48 | T-O (reproduced) | WP-CORE-04 | Mixed-UTC-offset forecasts raise under pandas 3.0.5, and `cli.py:451` swallows the error with a misleading note. |
| R1 (publish path) | T-P | WP-CORE-05 | `publish_run` is reached only from `scripts/run_d57_campaign.py:356`. CLI and app render from memory. `docs/dormant_code.toml` does not exist (the 08-16 doc said it did). |
| R2 (study-basis producer) | T-P | WP-UW-01 | Produced only by `scripts/run_d57_campaign.py:223,244`. There is no producer in `src/` and no CLI flag. `derive_flat_export_tariff` has zero callers. |
| dfs_performance_factor | T-O (dormant) | WP-UW-11 | Check A: unreferenced in `src/`. D60(e) declined fleet K, but the symbol is not declared dormant. |
| eac_response_value(toll_reference=) | T-O (latent, safe default) | WP-UW-06 | Check B: never supplied. The default applies F and K. |
| mypy over tests/ | T-O | WP-PLAT-01 | `pyproject.toml` `packages = ["gridq"]`. The new CI runs bare `mypy`. |

### 2.4 Register contradictions (§6 of 2026-08-12)

| id | Status | Evidence |
|---|---|---|
| C-1 (CLAUDE.md §9 pinned gb_v0_1) | C | Fixed 08-16. The residual CLAUDE.md pointers were corrected in this session (§4). |
| C-3 ("WP-2/WP-3 landed" false for N26/N27/N29) | C (as a contradiction) | N26 is fixed. N27 and N29 are triaged above. |
| C-4 (N6/N7 complete) | C | Unchanged from the register. |
| C-5 (N12 halves) | C (as a contradiction) | The remaining half is triaged under N12. |
| C-6 (Gate 3 unwired) | C | See MD-F-10 + H-1. **CLAUDE.md §3 still said "called from nowhere" and was corrected in this session.** |
| C-7 (N1/N13/N14 warnings) | C | Fixed 08-16 (`PRE_TAX_CFADS_BASIS` etc. reach `quote.warnings`). |
| C-8 (N9 route 2) | C | See N9. |
| C-9 (MD-F-04 recorded fully fixed) | C (as a contradiction) | The real status, PARTIAL by decision, is triaged under MD-F-04. |
| C-10 (MD-F-05(b)) | C | See MD-F-05. |
| C-13 (unverified FIXED rows) | T-O | WP-CORE-01 | §1 of the 08-12 register (27 items) has still never been mutation-verified. Two "fixed" halves (N3 netting, N12 flag) have no wiring test (finance evidence, surprise 4). |

## 3. What moved since 2026-08-16, and in which direction

- **Six findings the register lists as open or partial are closed:** N9 (both routes), N10, N11, N28,
  H-17 and JSON-F-51. Add MD-F-10/H-1 and E-06, which the register filed as partial or absent.
- **Three that read as latent are now live:** D-116 (every D54 site prices the availability leg
  gross), D-130 (award tenor never reaches finance) and the DEG-01 guard (red, now re-pinned).
  *Update 2026-11-25 (WP-UW-16):* D-130 is closed and D-116 is labelled gross in `quote.warnings`;
  see their rows.
- *Update 2026-11-26 (WP-UW-03):* blocker **B001 is closed**, with N25 (`case_id` now carries the debt
  terms, lender terms and price structure). H-05 and H-03 move to partial: covenant tiers and
  target-sized sculpting landed, sub-annual tests and covenant-aware sizing stay with WP-UW-09.
- **D57 re-opened two limbs by decision:** MD-F-04's power/energy budget and D-92 enforcement. They
  are triaged to `underwriting_basis_v1` (WP-UW-01) because they cannot be fixed on the academic
  basis without violating D57.

## 4. CLAUDE.md claims re-verified against the call path

Corrections were made **only where the claim is proven wrong**. Each correction cites the evidence.

| CLAUDE.md claim | Verdict | Evidence |
|---|---|---|
| §3 "Gate 3 is built, tested, and called from nowhere … `assess_confidence` invoked without `reconciles=`" | **WRONG, corrected** | `quotes/pipeline.py:367` calls `reconcile_declared_against_measured(site, summary)`, and `:603`/`:641` pass `reconciliation=` (the parameter was renamed; see the 08-16 §2.3 rename trap). Guarded by `test_gates_record_and_continue.py::test_gate_3_runs_on_the_real_path_and_reports_a_reconciled_basis`. The spec's A10-01 example agrees. |
| §3 "Claimed landed but actually open: N26, N27, N29. And MD-F-05 limb (b) … still not netted" | **WRONG for N26 and MD-F-05(b), corrected** | N26: `cashflows.py:856` plus `test_finance_blended_import_rate.py`. MD-F-05(b): `engine.py:2359` plus `test_engine_connection_basis.py:637`. N27 and N29 are still open (§2.2). |
| §3 "authoritative register … 27 fixed, 8 partial, 62 distinct open" | **STALE, re-pointed** | Statuses are superseded by this document. |
| §3 "Live validation campaign: Grundon (4 sites) + Harwell … only Harwell has metered HH data" | **WRONG, corrected** | `examples/real/sites/*.json` holds 9 site records. `load_data.half_hourly_basis` is `metered` on 6 (bishops_cleeve_ash_plant, bishops_cleeve_mrf, ewelme, harwell, knowl_hill, tanhouse) and `modelled` on 3 (colnbrook, ewelme_invoice_anchored, knowl_hill_invoice_anchored). |
| §3 DEG-01 "`optimisation/milp_solver.py:1741`" | **Line pointer wrong, corrected** | The `deg_term = …` signature is at `:1948` at HEAD (`:1987` in the working tree). The substance (the key read and £21.3/MWh) is verified: `degradation_cost_gbp_per_kwh(gb_v1_12)` = 0.0213 £/kWh via `battery_opex.degradation_marginal_cost_gbp_per_mwh`. |
| §2.0 "`PPA_OR_BILL_SAVINGS` (itemised across arbitrage + DUoS, `valuation.py:1185`)" | **Line pointer wrong, corrected** | The `elif sid == MarketStream.PPA_OR_BILL_SAVINGS` branch with the "£0 aggregate … itemised" note is at `valuation.py:1772`. The substance is verified. |
| §2 "the `mic ≈ peak` signature at `optimisation/engine.py:784`, which did exactly that" | **Pointer wrong, corrected** | `engine.py:784` is the two-stage search flag. The signature handling is `_PLACEHOLDER_MIC_TOLERANCE` (`engine.py:2037-2077`), and the comment at `:973` records that the old INSUFFICIENT_DATA return was removed. |
| §9 "`gb_v0_1.yaml` … `gb_v1_11.yaml` (newest, 2026-08-18)" | **WRONG, corrected** | `gb_v1_12.yaml` exists, `metadata.released: 2026-08-18`, added in `12033d0`. |
| §9 "CLI resolves `latest_version()` (`cli.py:206-221`)" and "`cli.py:7` / `:136` docstrings still say `gb_v0_1`" | **Pointer wrong / still true, corrected** | `latest_version` is at `cli.py:252-263`. Lines `:7` and `:136` still say gb_v0_1. |
| §9 "Gated upside streams … never `FIRM`, and they live in `gated_upside`, not `value_streams`" | **Half wrong, corrected** | "Never FIRM" holds: the validator is at `schemas/market.py:55`. But a consented and priced gated stream moves into `value_streams` as CONTRACTED (D43, `5fcf4f5`), which is the D57 campaign's normal case. Guarded by `test_valuation_consent_pricing.py::test_bm_consent_and_agreed_rate_prices_and_moves_into_the_priced_floor`. |
| §3 "Two collision hotspots … `finance/cashflows.py` (9 open)" | **STALE, re-pointed** | Of the nine, N26 is closed. See §2.1 for the rest. |
| §5 "`input_hash` pins neither HH nor assumption content (F-06/F-09)" | **Still true** | MD-F-06 and MD-F-09 are open (§2.3). |
| §3 "`MilpEngine` is the default for `gridq run`; Gate 1 refuses `BaselineEngine`" | **Still true** | `cli.py:148` imports `MilpEngine`. `quotes/lifecycle.py:106` refuses `ImplementationLevel.BASELINE`. |
| Session start: `python -m gridq.dev.team_sync add-handoff` | **Still true** | `team_sync.py:460` CLI. |
| §11 graphify | **Environment, not code** | The CLI is not on PATH (handoff 2026-08-23). Left as written; it is an instruction, not a claim about the code. |

## 5. Gaps the owner should close

1. **No work package covers finance completeness.** That means price levelisation, indexation,
   construction phase and terminal flows: N12, N13/H-15, N14, H-16, and the CPI half of D-33. Today
   they are triaged to the WS-UW workstream with no package. Proposal: a new WS-UW package, or widen
   WP-UW-14 ("Tax and allowances") to "Tax, indexation and life-cycle flows".
2. **LV tariff representation has no package.** MD-F-18, JSON-F-38/F-39, R8 and JSON-F-54 are
   parked on WP-CORE-03 (the SiteModel/TariffStack re-freeze). B-17 is parked on WP-UW-07, whose
   scope is HV/EHV only.
   *Update 2026-11-26:* WP-UW-07 was widened to LV/CDCM and closed MD-F-18, JSON-F-38/F-39, R8
   and B-17; JSON-F-54 is not in its scope and stays where it is.
3. **`docs/dormant_code.toml` does not exist.** R1, R2 and `dfs_performance_factor` would each be
   settled in one line if the owner declared them library-only.
