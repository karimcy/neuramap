# WP-CORE-02 re-verification: finance group (finance, debt, pricing, returns, scenarios, assumption library)

Verified 2026-11-16 against the working tree of `uk` at HEAD `55b168c`, plus uncommitted edits. Nothing in the repo was modified.
Default release that production resolves: `gb_v1_12` (`gridq.assumptions.latest_version()`; `cli.py:248-255`).
Production finance call path: `cli.py run` -> `quotes/pipeline.py:484 run_financial_model(site, result, assumptions, dispatch_energy=, debt_terms=)` -> `finance/financial_model.py:162 build_cashflow_model` / `:190 assess_debt` / `:226 equity_irr` / `:243 recommend_price`. Scenario rows go through `quotes/pipeline.py:505 compute_scenario_sensitivities` -> `quotes/sensitivities.py:161 run_financial_model`.
Call-path audit: `.venv/bin/python -m gridq.dev.call_path_audit --no-baseline --check A,B --json` (147 core modules, 229 test files). Full listing: `call_path_audit_A_B_2026-11-16.txt` beside this file. The relevant lines are quoted in each section.

**Audit-tool caveat (matters for D-33, D-130, N25):** check B does NOT flag `run_financial_model(commitments=, contract_tenor_years=, price_structure=, horizon_years=)`, even though no production caller passes them. The reason is that `build_financial_case` (`financial_model.py:326`) passes all four through, and check A flags `build_financial_case` itself as having no production caller. So a dead pass-through wrapper satisfies check B for its target. The suppression was confirmed by grep (see D-130).

## Summary

| id | verdict | WP | one-line reason |
|---|---|---|---|
| N1 | OPEN (Tier B; disclosure landed) | WP-UW-14 | No tax line anywhere. The PRE-TAX basis label is now on `DscrSummary.basis` and in the warnings. |
| N3 | PARTIAL | WP-UW-09 | Netting is wired. (a) dispatch cost is still excluded, (b) the rate is still the equity hurdle on total capex, (c) it still nets the raw stream sum before the foresight and availability adjustments. |
| N10 | CLOSED-FIXED | n/a | `debt.py:347-371` returns `available=False` on a partial window. Commit `642621b`; test `test_n10_*`. |
| N11 | CLOSED-FIXED | n/a | `returns.py:363` runs to max(horizon, tenor). Commit `642621b`; test `test_n11_post_horizon_*`. |
| N12 | PARTIAL | no WP fits (UW) | `hits_target_return` is now measured. The price is still levelised over flat year-1 volume (`pricing.py:210`). |
| N13 | OPEN | no WP fits (UW; nearest WP-UW-13) | Finance is still flat-nominal. `financing.cpi_pct_per_year=2.0` has been in the library since gb_v1_8 with **no src consumer**. |
| H-15 | OPEN (≡ N13) | same as N13 | Same defect as N13. |
| N14 | OPEN (Tier B; disclosure landed) | no WP fits (UW; partial overlap WP-SIZE-10) | The FCF is still `[-capex, *ebitda]`, with no terminal, decommissioning or replacement flow. |
| N25 | OPEN | WP-UW-03 | Reproduced: gearing 60% vs 40%, and fixed vs capacity_fee, give the **same `case_id`**. |
| MD-F-14 | OPEN (≡ N25) | WP-UW-03 | Same defect as N25. |
| N28 | CLOSED-FIXED | n/a | gb_v1_10+ carries the full 0.5 to 12 h NESO curve, so the 8 h search bound is priced at its own bin. Commit `d779eb8`. |
| N31 | OPEN (latent) | WP-UW-10 | `scenarios/engine.py:130` still scales net cashflow. It is reachable from `outputs/generate.py:104`, but only with identity multipliers today. |
| H-03 | OPEN (Tier B policy) | WP-UW-09 | `debt.py:328` sets principal = capex x gearing. The DSCR-sized version exists only in the off-pipeline `portfolio_requirements/kernel.py`. |
| H-04 | OPEN | WP-UW-09 | `financial_model.py:189` passes P90 EBITDA (`p90_fcf_pre_debt[1:]`) as CFADS. |
| H-05 | OPEN (Tier B) | WP-UW-03 | `dscr_covenants` has one key (`min_p90_dscr`), with no lock-up, EoD or semi-annual test. |
| H-08 | OPEN | WP-UW-08 | P90 is still a flat 15% placeholder haircut (`gb_v1_12:1197`, status placeholder). |
| H-16 | OPEN | no WP fits (UW) | There is no construction phase or IDC in finance. `Scenario.cod_delay_months` is supplied by no production caller (audit B). |
| H-17 | CLOSED-FIXED | n/a | `cashflows.py:1841` applies the 95% fleet availability from gb_v1_12, and it is live in persisted runs. Commit `cfe7120`. |
| D-33 | PARTIAL | WP-UW-09 | The flat in-term mechanism is built and tested, but no production caller supplies a CM tenor, so CM is still faded. CPI indexation is absent. |
| D-130 | PARTIAL (production half OPEN) | WP-UW-09 | The tenor fix exists, but `site.local_flex_awards[].tenor_years` never reaches finance. Tanhouse's 1-year award earns £451.68/yr for 15 years. |
| D-25 | OPEN | WP-UW-10 | `de_rating_by_auction_and_delivery_year` is in the library, but no src code reads it. Pricing uses the duration-only 2029-30 T-4 slice. |
| JSON-F-32 | OPEN (latent) | WP-UW-01 | The MILP ceiling is `REVIEW_REQUIRED` (`milp_solver.py:2681`), while finance requires `bankable_candidate` (`cashflows.py:2153`). That is still a deadlock. |
| JSON-F-33 | PARTIAL | WP-UW-01 | The 12.0 low-end pick is now policy under D56. The note still mislabels it as "chosen conservatively". |
| JSON-F-34 | PARTIAL | WP-DATA-02 | Limb (a), the "derived not fetched" suspicion, is CLOSED-INVALID by the full curve and history. Limb (b), no code verifies the fetch against its sidecar, is OPEN. |
| JSON-F-51 | CLOSED-FIXED | n/a | gb_v1_12 reconciles both keys to 5 kW/MW (0.005). Commit `1964949`; test `test_the_two_parasitic_keys_now_carry_one_reconciled_value`. |
| B001 | OPEN | WP-UW-03 | Twelve of twelve recent persisted gb_v1_12 runs have `min_dscr=None`, because all nine real sites have `credit_status: unknown` and the CLI injects no debt terms. |

---

### N1
**Claim:** No corporation tax anywhere. DSCR, project IRR and equity IRR are all pre-tax. Tier B.

**Evidence**
```
$ grep -rniE "corporation_tax|tax_rate|capital_allowance|taxable" src/gridq assumptions/gb_v1_12.yaml
src/gridq/finance/cashflows.py:1446:  "taxable-profit computation exists anywhere in the model: ..."   <- disclosure text only
src/gridq/finance/debt.py:62:#: Why it matters: there is no corporation tax line, tax rate, ...  <- comment only
$ .venv/bin/python -c "import yaml;d=yaml.safe_load(open('assumptions/gb_v1_12.yaml'));print(list(d['financing']))"
['target_equity_irr_pct', 'debt_gearing_pct', 'credit_rate_tenor_policy', 'cpi_pct_per_year']   <- no tax key

# FCF series is still pre-tax EBITDA with capex prepended
src/gridq/finance/cashflows.py:2086:    p50_fcf = [round(-capex.total_gbp, 2), *p50_ebitda]
src/gridq/finance/cashflows.py:2087:    p90_fcf = [round(-capex.total_gbp, 2), *p90_ebitda]
src/gridq/finance/financial_model.py:189:    p90_cfads = cashflows.p90_fcf_pre_debt_gbp[1:]

# Tier A disclosure subset (register C-7) has landed and reaches the record:
src/gridq/finance/cashflows.py:50:  PRE_TAX_CFADS_BASIS = ("PRE-TAX — no corporation tax is modelled; ...")
src/gridq/finance/debt.py:75:     DSCR_BASIS_P90_PRE_TAX = "P90 PRE-TAX"
runs/quote_run_aadecf1e9fb2df51/quote.json (tanhouse, gb_v1_12): dscr_summary.basis = "P90 PRE-TAX";
  warning "BASIS — PRE-TAX — no corporation tax is modelled; ... (N1)"
```
**Verdict:** OPEN, Tier B. The substantive tax layer is absent. The disclosure (C-7) is closed: the label is on `DscrSummary.basis` and in `FinancialCase.warnings`, and it reaches persisted quotes.

**Workstream:** WP-UW-14. Its deliverable `tax_model` (tax, capital allowances, depreciation) is the post-tax CFADS line N1 needs. The SPV/capital-allowance basis is the human input.

---

### N3
**Claim:** The price-to-target recovers 100% of capex while the project keeps the stack. Remaining halves: (a) `annual_dispatch_cost_gbp` is not in the cost base, (b) it discounts at the equity hurdle on total capex, (c) it nets before the perfect-foresight adjustment.

**Evidence**
```
# Netting half — present and wired:
src/gridq/finance/pricing.py:86:   return round(max(0.0, capex_gbp * crf + annual_opex_gbp - other_annual_revenue_gbp), 2)
src/gridq/finance/financial_model.py:254:  other_annual_revenue_gbp=cashflows.total_annual_revenue_gbp,
persisted tanhouse run: "Price-to-target nets off £4,108/yr of priced-floor revenue ..."

# (a) dispatch cost still excluded
src/gridq/finance/cashflows.py:77-86  COST_LINE_KEYS = ("opex","insurance","metering_idno","dno","business_rates",
                                                       "levies_on_charge","windowed_levies_avoided","parasitic_load")
src/gridq/finance/cashflows.py:310:   annual_dispatch_cost_gbp: float          <- separate field
src/gridq/finance/financial_model.py:242:  annual_opex = cashflows.total_annual_operating_cost_gbp
$ grep -n "annual_dispatch_cost_gbp" src/gridq/finance/financial_model.py src/gridq/finance/pricing.py
(no output)

# (b) equity hurdle on total capex
src/gridq/finance/pricing.py:20:   TARGET_IRR_KEY = ("financing", "target_equity_irr_pct")
src/gridq/finance/pricing.py:82:   crf = capital_recovery_factor(target_irr_pct, horizon_years)   # applied to capex_gbp (total)

# (c) nets the RAW stream sum — before PF adjustment AND (new since the register) before H-17 availability
src/gridq/finance/cashflows.py:359-370  total_annual_revenue_gbp: "Year-1 total priced revenue (raw stream sum ...)
        — BEFORE the named perfect-foresight adjustment AND before the H-17 fleet-availability de-rate"

$ git log --oneline -S "other_annual_revenue_gbp" -- src/gridq/finance/pricing.py
642621b WIP: snapshot WP-0 remediation work + validation/requirements docs
```
**Verdict:** PARTIAL. The netting half is closed by commit `642621b` at production call site `financial_model.py:254`. Halves (a), (b) and (c) are OPEN. Limb (c) has **widened**: the netted figure now also sits before the 95% availability de-rate, so it over-nets by about 5% of revenue plus the PF deduction.
*Wiring-test gap:* `tests/test_price_to_target_nets_stack.py` tests only `annual_revenue_requirement` at module level. No test calls `run_financial_model`, so deleting `other_annual_revenue_gbp=` at `financial_model.py:254` would fail nothing. Under the 2026-08-15 bar the closed half is therefore FIXED (call site) but unpinned (test).

**Workstream:** WP-UW-09. Limb (b) needs the blended rate from the debt structure that `debt_sizing_engine` produces. Limbs (a) and (c) are the same cost and revenue base, so they belong in that change.

---

### N10
**Claim:** Min DSCR is measured over a truncated window when horizon < tenor and is returned with `available=True`.

**Evidence**
```
src/gridq/finance/debt.py:347:    cfads_window = p90_cfads_by_year[: terms.tenor_years]
src/gridq/finance/debt.py:348:    if len(cfads_window) < terms.tenor_years:
   ... :364-371  return DscrResult(available=False, debt_service_by_year_gbp=service, principal_gbp=principal, ...)
$ git log --oneline -S "a partial window is NOT a measurable min DSCR" -- src/gridq/finance/debt.py
642621b WIP: snapshot WP-0 remediation work + validation/requirements docs
tests/test_finance_debt.py:102  test_n10_horizon_shorter_than_tenor_returns_unavailable_not_a_partial_minimum
tests/test_finance_debt.py:119  test_n10_the_dropped_years_are_the_binding_ones   (asserts truncated.available is False)
# call path: pipeline.py:484 -> financial_model.py:190 assess_debt -> debt.py:470 size_and_measure_dscr
# still latent on shipped paths (audit B): sizing.sizer enumerate_sizing horizon_years / sizing.evaluator horizon_years
#   "B sizing.sizer enumerate_sizing horizon_years L502 tests=False"
```
**Verdict:** CLOSED-FIXED. Fixed in `642621b`. Production site is `debt.py:348` via `financial_model.py:190`. Removing the guard would fail `test_n10_horizon_shorter_than_tenor_returns_unavailable_not_a_partial_minimum`.

---

### N11
**Claim:** `equity_irr` drops debt service after the horizon, which can turn a series the IRR gate would refuse into a published positive return.

**Evidence**
```
src/gridq/finance/returns.py:363:    periods = max(len(operating_fcf_pre_debt_by_year_gbp), len(debt_service_by_year_gbp))
src/gridq/finance/financial_model.py:226-231  eq = equity_irr(capex, dscr.principal_gbp, dscr.debt_service_by_year_gbp, p50_fcf[1:])
  (debt.py:364-371 still returns principal + service on the N10 unavailable branch, so the tail reaches equity_irr)
$ git log --oneline -S "periods = max(len(operating_fcf_pre_debt_by_year_gbp)" -- src/gridq/finance/returns.py
642621b WIP: snapshot WP-0 remediation work + validation/requirements docs
tests/test_finance_returns.py:151  test_n11_post_horizon_debt_service_is_never_dropped_from_the_equity_series
```
**Verdict:** CLOSED-FIXED. Fixed in `642621b`. Production site is `financial_model.py:226`. The guard is pinned by `test_n11_post_horizon_debt_service_is_never_dropped_from_the_equity_series`, which asserts 13 periods and `available is False`.

---

### N12
**Claim:** `hits_target_return` was set from the assumption's status, and the price is levelised as if volume were flat.

**Evidence**
```
# measured half — fixed
src/gridq/finance/pricing.py:223-250   hits from achieved_equity_irr_pct vs target (None on placeholder / no IRR)
src/gridq/finance/financial_model.py:258  achieved_equity_irr_pct=eq.irr_pct if eq.available else None,
$ git log --oneline -S "achieved_equity_irr_pct" -- src/gridq/finance/pricing.py
642621b WIP: ...
tests/test_finance_cashflows.py:531 test_n12_hits_target_is_measured_against_the_achieved_equity_irr (module-level, calls recommend_price)

# levelisation half — open
src/gridq/finance/pricing.py:210:   price = fixed_price_per_kwh(requirement, annual_delivered_energy_kwh)   # year-1 volume, level annuity
src/gridq/finance/pricing.py:216:   "... it levelises as if volume were flat when the delivered volume fades ..."  (concession unchanged)
src/gridq/finance/financial_model.py:378-382 _delivered_energy_kwh -> dispatch_energy.annual_discharge_kwh (year-1)
```
**Verdict:** PARTIAL. The measured-flag half is closed in `642621b` at production call site `financial_model.py:258`. *Wiring-test gap:* no test drives `run_financial_model` and asserts a measured True or False, so deleting the kwarg at `:258` would leave the flag permanently `None` and every test would still pass. The levelisation-over-faded-volume half is OPEN, unchanged.

**Workstream:** no WP fits. Price-to-target (the IPP PPA £/kWh) has no WP in the roadmap. I propose the UW workstream add a small "price-to-target correctness" item that bundles N12 levelisation with N3(a)/(c). The nearest existing WP is WP-UW-09.

---

### N13
**Claim:** No inflation index; nominal debt rate and nominal hurdle applied to flat-nominal cashflows. Tier B.

**Evidence**
```
src/gridq/finance/cashflows.py:1993:  decay = (1.0 - fade_pct / 100.0) ** (year - 1)     <- only year-on-year adjustment
src/gridq/finance/cashflows.py:55:    FLAT_NOMINAL_UNESCALATED_BASIS = ("FLAT-NOMINAL, UN-ESCALATED — no CPI/RPI index ...")
$ grep -rniE "cpi_pct|inflation_pct|escalation_pct|def .*escalat" src/gridq/finance
(no output)
# BUT the library now carries the answer the review said needed a human:
assumptions/gb_v1_12.yaml:1154  financing.cpi_pct_per_year: value 2.0, status needs_review, source OBR EFO Mar-2026;
   notes: "INDEX = CPI ... RATE = 2.0 ... LINES INDEXED (H-15) = opex, business_rates, insurance, metering_idno,
   dno_costs ... CPI-linked CONTRACTED revenue (T-4 CM ...)" ; "no src consumer read this key at cut time"
$ for v in 7..12: grep -c cpi_pct_per_year  -> present gb_v1_7 (empty home) .. gb_v1_12 (2.0 from gb_v1_8)
$ grep -rn "cpi_pct_per_year" src/gridq
(no output)
# a SECOND, off-pipeline finance kernel escalates at a different hard-coded rate:
src/gridq/portfolio_requirements/assumptions.py:233:    cpi_escalation: float = 0.025   ("Model_v5 G21")
src/gridq/portfolio_requirements/kernel.py:16-17  rev_y/opex_y escalate by (1 + cpi)^(y-1)
```
**Verdict:** OPEN. The disclosure subset (C-7) has landed. The register's Tier B label is stale: the index, the rate and the indexed lines are now proposed in the library (`financing.cpi_pct_per_year`, needs_review, owner Sim). What remains is Tier A wiring plus Sim's confirmation. The off-pipeline `portfolio_requirements` kernel escalates at a hard-coded 2.5%, which disagrees with the library's 2.0.

**Workstream:** no WP fits. The roadmap has no indexation, construction or terminal-flow item. I propose the UW workstream own a "cashflow bases" WP covering N13/H-15, N14, H-16 and the CPI half of D-33. The nearest is WP-UW-13, whose reconciliation to Model_v5 conventions (which escalate at CPI) would expose this gap.

### H-15
**Claim:** No indexation of opex, rates or insurance, nor of CPI-linked contracted revenue; the basis is undeclared. Same defect as N13.
**Evidence:** see N13. The basis is now declared (`cashflow_basis = FLAT_NOMINAL_UNESCALATED_BASIS`, `cashflows.py:337`), and nothing is indexed.
**Verdict:** OPEN (≡ N13). **Workstream:** as N13.

---

### N14
**Claim:** No terminal value, decommissioning or replacement capex. Tier B.

**Evidence**
```
src/gridq/finance/cashflows.py:2086-2087  p50_fcf/p90_fcf = [round(-capex.total_gbp, 2), *ebitda]   (only capital flow is index 0)
$ grep -rniE "terminal_value|residual_value|decommission|replacement_capex|augmentation_capex" src/gridq/finance
src/gridq/finance/cashflows.py:63:    "decommissioning / site restoration",          <- OMITTED_CAPITAL_FLOWS label only
src/gridq/finance/cashflows.py:1477:  "... decommissioning/restoration obligation ... are human inputs"  <- warning text only
```
**Verdict:** OPEN, Tier B (residual-value basis and decommissioning obligation). The disclosure has landed: `OMITTED_CAPITAL_FLOWS` is on `CashflowModel` and the warning is in the case.

**Workstream:** no WP fits. Propose the UW "cashflow bases" WP (see N13). WP-SIZE-10's augmentation schedule covers only the augmentation limb, which the panel already called the weak limb.

---

### N25
**Claim:** `_hash_input` omits debt terms and price structure, so materially different financial cases share a `case_id`.

**Evidence**
```
src/gridq/finance/financial_model.py:424-464  _hash_input(site, result, assumptions, horizon_years, scenario,
      dispatch_energy, contracted_tenor_years=())  -> no debt_terms, no price_structure
# reproduced (read-only, run_financial_model on the tests/test_finance_tenor_threading.py fixture, gb_v1_6):
A gearing60 fixed        fc_run_efb5d253f23994cd min_dscr 3.2464 eq_irr 87.4709 price fixed_gbp_per_kwh
B gearing40 fixed        fc_run_efb5d253f23994cd min_dscr 4.8696 eq_irr 61.244  price fixed_gbp_per_kwh
C gearing60 capacity_fee fc_run_efb5d253f23994cd min_dscr 3.2464 eq_irr 87.4709 price capacity_fee
A==B case_id: True   A==C case_id: True
# Only the contract-tenor limb was added (d779eb8; tests/test_finance_tenor_threading.py:359
#   test_two_tenor_treatments_cannot_share_a_case_id). Containment one layer up still holds:
src/gridq/quotes/assembly.py:158  financial_case.model_dump(mode="json", exclude={"case_id"}) -> quote canonical hash
```
**Verdict:** OPEN. The named inputs (debt terms, price structure) are still outside the case identity. The quote-level hash still contains them through the dumped case, which is why the panel rated it LOW.

**Workstream:** WP-UW-03. Its `lender_terms_object` makes debt terms a first-class input, and that object must enter `_hash_input` along with `price_structure`.

**Closure (WP-UW-03, 2026-11-26): CLOSED.** `run_financial_model` passes the injected `debt_terms`, the `lender_terms` and the `price_structure` to `_hash_input` (`financial_model.py:473`), which adds each key only when present or non-default (`:959`, `:961`, `:963`), so runs without them keep their historical hash input (D78). Guarded by `tests/test_lender_terms.py::test_debt_terms_lender_terms_and_price_structure_split_the_case_id` (each payload line deleted in turn turns it red) and `::test_a_run_without_terms_keeps_its_historical_case_hash_input`.

### MD-F-14
**Claim:** `case_id` omits `debt_terms` and `price_structure`. Same defect as N25.
**Evidence:** see N25. Reproduced: three cases, one `case_id`.
**Verdict:** OPEN (≡ N25). **Workstream:** WP-UW-03.

---

### N28
**Claim:** CM de-rating is silently floored at the 4 h key across the 4 to 8 h half of the search box.

**Evidence**
```
assumptions/gb_v1_12.yaml:1977  capacity_market.de_rating_factor_by_duration_h: 24 bins "0.5" .. "12"
   ("6": 0.6608, "8": 0.8777, "8.5".."12": 0.9216 plateau)
tests/test_cm_derating_curve_and_penalty_cap.py:19-21 "DURATION_BOUNDS_HOURS = (0.5, 8.0)"  -> search max 8 h < table max 12 h
src/gridq/optimisation/valuation.py:345  derate, warn = value_for_duration(derate_a.value, schedule.duration_h)
src/gridq/optimisation/_assumptions.py:59-64 value_for_duration: still no above-range warning (but unreachable from the search box now)
$ git log --oneline --reverse -- tests/test_cm_derating_curve_and_penalty_cap.py | head -1
d779eb8 Close the register: CM de-rating, D45 classes, scenario_id, levy research, Tier A status
tests/test_cm_derating_curve_and_penalty_cap.py:270 test_every_duration_the_search_can_produce_is_priced_at_its_published_bin
tests/test_cm_derating_curve_and_penalty_cap.py:255 test_gb_v1_10_is_what_a_production_run_resolves  (latest == full curve)
```
**Verdict:** CLOSED-FIXED, by the data route. `d779eb8` (gb_v1_10, carried into gb_v1_12) adds the full published curve. Production site is `valuation.py:345` via the MILP's `build_value_streams`. Reverting the table fails `test_every_duration_the_search_can_produce_is_priced_at_its_published_bin` and `test_gb_v1_10_is_what_a_production_run_resolves`. The review's Tier A warning above 12 h was not added; it is moot while the search caps at 8 h.

---

### N31
**Claim:** `price_multiplier` scales NET operating cashflow, so opex shrinks along with revenue.

**Evidence**
```
src/gridq/scenarios/engine.py:130:  out = [cf * (cost_multiplier if i == 0 else price_multiplier) for i, cf in enumerate(cashflows)]
src/gridq/scenarios/engine.py:54-58 CashflowProvider still exposes only p50_cashflows / p90_cashflows (not widened)
# reachable production path (fallback only):
src/gridq/outputs/generate.py:96-104  if no persisted quote.scenario_sensitivities -> scenario_sensitivity_rows(...)
src/gridq/outputs/sensitivities.py:29  scenario_set = build_standard_scenario_set(assumptions)   # no multipliers -> 1.0
src/gridq/outputs/sensitivities.py:32  run = run_scenario_set(case, scenario_set)
src/gridq/scenarios/engine.py:369-370  low/high price multiplier default 1.0
$ grep -rn "low_price_multiplier=\|high_price_multiplier=" src/gridq | grep -v scenarios/engine.py
(no output)    <- no caller supplies a non-identity band
$ git log --oneline --reverse -S "honest coarseness" -- src/gridq/scenarios/engine.py | head -1
2b5ce47 Wave 1 ...   <- the docstring disclosure predates the review; no per-scenario warning added
```
**Verdict:** OPEN, latent. The arithmetic is unchanged and the review's interim per-scenario warning was not added. It produces no wrong figure today because every reachable call uses identity multipliers.

**Workstream:** WP-UW-10. The `downside_case_set` will be the first producer of real price shocks through scenario sets, and a net-cashflow multiplier would overstate each Low case.

---

### H-03
**Claim:** Debt quantum is asserted as gearing x capex instead of being sized as min(DSCR capacity, gearing cap).

**Evidence**
```
src/gridq/finance/debt.py:328:    principal = round(capex_gbp * terms.gearing_pct / 100.0, 2)
$ grep -rn "PV(\|dscr_capacity\|min(.*gearing" src/gridq/finance
(no output)
# a DSCR-sized implementation exists, but OFF the quote pipeline:
src/gridq/portfolio_requirements/kernel.py:4  "debt = min(gearing_cap x capex, PV(CFADS_P90 / DSCR_target, tenor, rate))"
$ grep -rln "portfolio_requirements" src scripts | grep -v portfolio_requirements/   -> only egg-info SOURCES.txt
audit B: "B finance.credit build_debt_terms_from_credit gearing_override L190 tests=True"
```
**Verdict:** OPEN. It is Tier B as a policy choice (whether DSCR sizing becomes the default). A working algorithm already exists in `portfolio_requirements/kernel.py` and could be ported.

**Workstream:** WP-UW-09. `debt_sizing_engine` ("debt sized on contracted CFADS") is exactly this deliverable.

**Update (WP-UW-03, 2026-11-26): PARTIAL.** The kernel's rule is now on the quote path for supplied terms: a sculpted `LenderTerms` with a `sculpting_target_dscr` sizes the debt as min(capex x max gearing, PV(case CFADS / target, tenor, rate)) (`finance/debt.py:405` `target_sculpted_schedule`, D77), and `DebtAssumptions.sized_by` says which bound bit. Guarded by `tests/test_lender_terms.py::test_a_sculpting_target_sizes_the_debt_when_the_cfads_supports_less_than_the_cap` and `::test_a_sculpting_target_never_lends_past_the_gearing_cap`. Still open (WP-UW-09): every other run is capex x gearing, and the sizer never sees the covenant.

---

### H-04
**Claim:** DSCR is measured on EBITDA, not on a built CFADS series (tax, reserves and working capital are missing).

**Evidence**
```
src/gridq/finance/financial_model.py:189:  p90_cfads = cashflows.p90_fcf_pre_debt_gbp[1:]     # == p90_ebitda (cashflows.py:2087)
src/gridq/finance/financial_model.py:190-200 assess_debt(capex, p90_cfads, ...)
$ grep -rniE "working_capital|dsra|maintenance_reserve|cash_tax" src/gridq/finance
(no output)
```
**Verdict:** OPEN. The pre-tax label exists (N1 disclosure), but no CFADS build-up exists.

**Workstream:** WP-UW-09. It releases DSCR "on contracted CFADS", which needs a named CFADS series between EBITDA and debt service.

---

### H-05
**Claim:** One covenant value and one annual min-DSCR test, with no lock-up or event-of-default tiers and no semi-annual period. Tier B.

**Evidence**
```
$ .venv/bin/python -c "...; print(list(d['dscr_covenants']))"
dscr_covenants keys: ['min_p90_dscr']
$ grep -rniE "lock.?up|event_of_default|semi.?annual|calculation_period" src/gridq/finance assumptions/gb_v1_12.yaml
(no output)
src/gridq/finance/debt.py:426-435  check_covenant(min_dscr, covenant) -> single pass/fail
```
**Verdict:** OPEN, Tier B (lender thresholds and period convention).

**Workstream:** WP-UW-03. The `lender_terms_object` is where lock-up and EoD thresholds and the calculation-period convention would be supplied.

**Update (WP-UW-03, 2026-11-26): PARTIAL.** `LenderTerms.covenant` carries the default (EoD) and lock-up thresholds and `calculation_period` the test period. `financial_model.py:279` takes the tiers from the terms with no library fallback (D76); `CovenantStatus` gains `lock_up_dscr`, `lock_up_passes`, `source` and `not_evaluated_reason`. Guarded by `tests/test_lender_terms.py::test_covenant_tiers_come_from_the_terms_with_a_lock_up` and `::test_terms_without_a_covenant_are_never_tested_against_the_library_placeholder` (both red with that line replaced). Open: only an annual test is computed. A semi-annual or quarterly period is refused with its reason rather than annualised (`::test_a_semi_annual_test_period_is_refused_not_annualised`); sub-annual CFADS is WP-UW-09 work.

---

### H-08
**Claim:** "P90" is a flat, unsourced 15% haircut on one deterministic run, not a percentile.

**Evidence**
```
assumptions/gb_v1_12.yaml:1197  p90_revenue_haircut_pct: value 15.0, status placeholder,
     source "No citable source identified (W4-3 review 2026-06-24); carried forward from gb_v0_1"
assumptions/gb_v1_12.yaml:1215  p90_revenue_haircut_contracted_pct: value null
src/gridq/finance/cashflows.py:1818-1822  haircut_pct = _use(... "p90_revenue_haircut_pct") ...
$ grep -rn "percentile\|realisations\|monte" src/gridq/finance/*.py
(no output)
persisted tanhouse run: "Contracted revenue shares the merchant P90 haircut: ... absent/unusable"
```
**Verdict:** OPEN. The per-stream in-term zero (`_NO_P90_IN_TERM`) is a structural exclusion, not a distribution, and it fires only on tenored streams (see D-130).

**Workstream:** WP-UW-08. `stochastic_finance_engine` is specified as "replacing the placeholder haircut".

---

### H-16
**Claim:** No construction phase. Cashflow runs from the cheque straight to full revenue with no IDC, contingency, commissioning ramp or COD-start tenor.

**Evidence**
```
src/gridq/finance/cashflows.py:2086  p50_fcf = [round(-capex.total_gbp, 2), *p50_ebitda]   # year 1 = full revenue
$ grep -rniE "construction|interest_during|\bIDC\b|commercial_operation|cod_delay" src/gridq/finance
(only prose hits: "by construction")
src/gridq/quotes/sensitivities.py:140-158  COD-delay scenarios are WITHHELD ("the delay is reported on the row and applied to nothing")
audit B: "B scenarios.models Scenario cod_delay_months L55 tests=True"   (no production caller supplies it)
```
**Verdict:** OPEN.

**Workstream:** no WP fits. Propose the UW "cashflow bases" WP (see N13), which needs the COD and connection timeline from WP-CONN.

---

### H-17
**Claim:** Every revenue line is earned at 100% availability for the whole horizon.

**Evidence**
```
src/gridq/finance/cashflows.py:107   AVAILABILITY_FACTOR_KEY = ("availability", "fleet_availability_pct")
src/gridq/finance/cashflows.py:1841  availability_factor, availability_pct = _availability_factor(assumptions, ...)
src/gridq/finance/cashflows.py:2017-2018  p50_rev/p90_rev = (...) * availability_factor
src/gridq/finance/cashflows.py:1857-1859 shortfall_fraction includes (1 - availability_factor)  (penalty side, as asked)
assumptions/gb_v1_12.yaml:3914  fleet_availability_pct: value 95.0 (Modo calibration + PNNL), status needs_review
persisted tanhouse run (gb_v1_12): "Revenue is de-rated for fleet availability: availability.fleet_availability_pct = 95% ..."
$ git log --oneline --reverse -S "def _availability_factor" -- src/gridq/finance/cashflows.py | head -1
cfe7120 In-term local flex and DFS take their P90 from the published lower tail, not 15%
tests/test_finance_tenor_and_availability.py:433 test_the_release_that_carries_the_sourced_figure_activates_the_derate
```
**Verdict:** CLOSED-FIXED. Production site is `cashflows.py:1841`, reached via `pipeline.py:484` -> `financial_model.py:162`. The first commit carrying the code is `cfe7120`. `test_the_release_that_carries_the_sourced_figure_activates_the_derate` fails if the call or the key is removed. The value is still needs_review.

---

### D-33
**Claim:** CM revenue is shrunk every year by capacity fade and is never CPI-indexed.

**Evidence**
```
# mechanism built (flat in-term when a tenor is known):
src/gridq/finance/cashflows.py:1997  flat_in_term = sum(tenored_base[k] * f ...)   # "NOT faded — D-33"
tests/test_finance_tenor_and_availability.py:241 test_a_tenored_cm_agreement_is_held_flat_on_the_awarded_mw_for_its_term
# ...but the production path supplies no CM tenor:
src/gridq/quotes/pipeline.py:484-486  run_financial_model(site, result, assumptions, dispatch_energy=..., debt_terms=...)   # no commitments=, no contract_tenor_years=
$ grep -rn -E "contract_tenor_years=|commitments=" src/gridq | grep -v finance/
(no output outside finance/ and the dev audit docstring)
src/gridq/finance/cashflows.py:1278-1318  tenors_from_provisioning_requirements -> LOAD_SERVICE only
persisted tanhouse run: "The battery's capacity fade is applied to the un-tenored contracted revenue
   (AGREED_CAPACITY_REDUCTION, ANCILLARY_DC_DM_DR, BALANCING_MECHANISM, CAPACITY_MARKET, DFS, LOCAL_FLEX_PICLO, WHOLESALE_P415) ... (D-33)"
# CPI half: no consumer of financing.cpi_pct_per_year (see N13)
```
**Verdict:** PARTIAL. The in-term flat mechanism is built and tested (module-level, through `run_financial_model(contract_tenor_years=)`). On the production path CAPACITY_MARKET is still faded because no CM term is ever supplied (see the audit caveat at the top). CPI indexation is OPEN (N13).

**Workstream:** WP-UW-09. Contracted CFADS needs each contracted stream held flat for its awarded term, so the CM agreement term must be threaded to finance there.

---

### D-130
**Claim:** A local-flex award (typically 1 to 2 years, terminable) earns for the full horizon in P50 and P90.

**Evidence**
```
# module fix exists:
src/gridq/finance/cashflows.py:132-134 _NO_P90_AFTER_EXPIRY = {LOCAL_FLEX_PICLO, DFS};  :171-173 _NO_P90_IN_TERM
tests/test_finance_tenor_threading.py:429 test_d130_is_p50_neutral_after_expiry_and_bites_only_the_cautious_cases
# the award's own tenor never reaches finance on the production path:
$ grep -rn "local_flex_awards" src/gridq | grep -v schemas   -> only value_stack/gb_streams.py and quotes/assembly.py:92 (no finance/)
$ .venv/bin/python (read-only) on examples/real/sites/tanhouse.json:
local_flex_awards tenor: [(LOCAL_FLEX_PICLO, 1.0)]
resolve_contract_tenor_evidence(site) -> ({'LOAD_SERVICE': 10.0}, [])        <- the 1-year award is dropped
persisted run runs/quote_run_aadecf1e9fb2df51 (grundon_tanhouse, gb_v1_12):
  LOCAL_FLEX_PICLO contracted £451.68/yr; DFS contracted £1,464.57/yr
  warning: "CONTRACTED revenue is projected to the FULL 15-year horizon with no contract tenor evidence for
            ... DFS, LOCAL_FLEX_PICLO ... (D-130)"
  (no _warn_in_term_p90_zeroed text on the record -> the in-term P90 zero never fired)
audit A: "A finance.financial_model build_financial_case None L326 tests=True"  (the dead wrapper that masks check B
          for run_financial_model(commitments=, contract_tenor_years=))
```
**Verdict:** PARTIAL. The module half is FIXED with tests. The production half is OPEN: `SiteModel.local_flex_awards[].tenor_years` (D54) is never passed to `run_financial_model(commitments=)`, and `resolve_contract_tenor_evidence` reads only `provisioning_requirements`. So on the real Tanhouse run a 1-year award earns for 15 years in P50. It enters P90 and the lender-basis series at the class-wide 15% haircut rather than zero.

**Workstream:** WP-UW-09. Its contracted-versus-merchant CFADS split needs contracted revenue to end at the award tenor. The fix is one argument at `pipeline.py:484` and `quotes/sensitivities.py:161` (`commitments=site.local_flex_awards`), plus a CM term source.

---

### D-25
**Claim:** There is one de-rating table keyed only on duration. T-4 and T-1 factors and delivery years cannot be represented.

**Evidence**
```
assumptions/gb_v1_12.yaml:2039  capacity_market.de_rating_by_auction_and_delivery_year: full_curves {T-4: 2029-30, T-1: 2026-27},
                                 history_1h_2h_4h (17 auction x year combos)
$ grep -rn "de_rating_by_auction_and_delivery_year\|auction_type\|delivery_year" src/gridq --include='*.py'
src/gridq/dev/fetch_neso_ckan.py:192/222   <- fetcher only; no pricing consumer
src/gridq/optimisation/valuation.py:342-345  reads de_rating_factor_by_duration_h only (= the T-4 2029-30 slice)
```
**Verdict:** OPEN. The data now exists in the library, but valuation never reads auction type or delivery year.

**Workstream:** WP-UW-10. Its "CM de-rating change" downside needs pricing keyed on auction and delivery year from the commitment, which is the D-25 restructure.

---

### JSON-F-32
**Claim:** The solver never emits BANKABLE_CANDIDATE, so finance's bankability gate is unreachable.

**Evidence**
```
src/gridq/optimisation/milp_solver.py:2671-2681  _bankable_status: "Never bankable_candidate here" ... return BankableStatus.REVIEW_REQUIRED
src/gridq/finance/cashflows.py:2153:  bankable = getattr(status, "value", status) == "bankable_candidate"
src/gridq/finance/financial_model.py:268-273  bankable = cashflows.bankable and dscr.available and covenant.passes ...
src/gridq/optimisation/engine.py:2891 / app/demo.py:716  bankable_status=BankableStatus.NOT_BANKABLE
$ grep -rn "BANKABLE_CANDIDATE" src/gridq  -> comparisons/validators only; no producer
src/gridq/schemas/quote.py:162  "... other than `BANKABLE_CANDIDATE` (which no engine emits) ..."
```
**Verdict:** OPEN, latent (zero confirmed assumptions today). D57 does not supersede it: study-basis outputs are never bankable by design, but the underwriting basis needs a reachable verdict.

**Workstream:** WP-UW-01. `underwriting_basis_v1` "enforces gates from evidence objects", and the bankability verdict is its terminal gate, so it must be reachable without the solver/finance deadlock.

---

### JSON-F-33
**Claim:** The 12.0% target-equity-IRR default is the low end and flatters price and viability, while its note claims conservatism.

**Evidence**
```
assumptions/gb_v1_12.yaml:1084-1097  target_equity_irr_pct: value 12.0, status needs_review;
   notes: "12.0 is the LOW end, chosen conservatively (a higher hurdle is harder to clear)"   <- unchanged since gb_v1_6
docs/decisions.md D56 (2026-08-18): "Where a SOURCED RANGE exists the FAVOURABLE END is taken" ... each pick
   labelled with its "direction of effect" on a dedicated assumptions page
```
**Verdict:** PARTIAL. Choosing the low end is now deliberate policy under D56, so the "flatters" limb is CLOSED-SUPERSEDED (D56). The mislabelling limb is OPEN: D56 requires an optimistic pick to be labelled as such with its direction, and the note still says "chosen conservatively", which is false.

**Workstream:** WP-UW-01. The underwriting basis must take the conservative end (13%+) and label each basis's pick, which corrects the note.

---

### JSON-F-34
**Claim:** The CM de-rating factors are exactly duration-proportional (1:2:4), which suggests a value derived from the 1 h figure, and no code verifies a provenance sidecar.

**Evidence**
```
# limb (a) — proportionality as evidence of derivation
assumptions/gb_v1_12.yaml:1977-2001  full 24-bin curve: "1.5": 0.1641 (linear would be 0.16485), "2.5": 0.2755 (0.27475),
     "5": 0.5509 (0.5495), plateau 0.9216 from 8.5 h  -> NOT linear off the 1 h point
$ .venv/bin/python (yaml) history_1h_2h_4h: combos 17, exactly proportional 2  (T-4 2029-30 and T-1 2026-27)
assumptions/gb_v1_12.yaml:2033-2037  "NOTE ON PROPORTIONALITY ... a property of the 2029-30 T-4 data ... Never argue from proportionality"
# limb (b) — machine verification
assumptions/provenance/gb_v1_6/cm_derating.provenance.json  (now present; full 64-hex sha256, record_count 801)
tests/test_assumptions_gb_v1_6.py:123 test_gb_v1_6_provenance_sidecars_exist_and_pin_the_fetch  -> checks shape only
$ find . -size 149106c (the sidecar's size_bytes)  -> no raw payload in the repo; nothing re-hashes a fetch
```
**Verdict:** PARTIAL. Limb (a) is CLOSED-INVALID: the full curve and 15 of 17 historical combos are non-proportional, so integer-hour proportionality is a property of that auction's data, not a sign of derivation. Limb (b) is OPEN: the sidecars now exist, but no code or test re-verifies a fetched payload against the pinned hash, and the payload is not stored.

**Workstream:** WP-DATA-02. The stamped-dataset contract (row stamp with dataset hash, conformance tests, recorded real API responses as fixtures) is the machine check limb (b) lacks.

---

### JSON-F-51
**Claim:** The library carries two contradictory parasitic-load values (1% vs 0.5% of rated power), and one of them is dead.

**Evidence**
```
assumptions/gb_v1_12.yaml:805  battery_opex.parasitic_load_fraction_of_power: 0.005  ("MOVED 0.01 -> 0.005 IN gb_v1_12")
assumptions/gb_v1_12.yaml:980  parasitic_load.standby_kw_per_mw: 5.0  (PNNL-28379: 10 kW / 2,000 kW)
consumers: finance/cashflows.py:99, optimisation/milp_solver.py:1606, engine.py:1410 (fraction key);
           value_stack/gb_streams.py:3011 (standby key — no longer dead)
$ git log --oneline --reverse -S "value: 0.005" -- assumptions/gb_v1_12.yaml | head -1
1964949 D57 items 6-8 part 1: agreed capacity into the floor, the CM commitment gate retired, the parasitic keys reconciled
tests/test_all_streams_study_basis.py:673 test_the_two_parasitic_keys_now_carry_one_reconciled_value
   (asserts standby/1000 == fraction)
```
**Verdict:** CLOSED-FIXED by `1964949` (gb_v1_12, the default release). Both keys are consumed and agree, and `test_the_two_parasitic_keys_now_carry_one_reconciled_value` fails if they diverge. Two notes. First, there are still two keys for one quantity, reconciled by value rather than merged; on gb_v1_11 and older they still disagree. Second, the reconciliation took D56's favourable end (cost halves), which is the returns-improving direction.

---

### B001
**Claim:** DSCR and debt terms are unavailable because there is no per-site debt structure (credit mapping and gearing are blocked externally). Referenced at `docs/operating_guide.md:137,219-220`.

**Evidence**
```
assumptions/gb_v1_12.yaml:1098  financing.debt_gearing_pct: 50.0 (CEPA/DESNZ 2025, needs_review)  <- gearing no longer absent
src/gridq/finance/financial_model.py:178-184  debt_terms from build_debt_terms_from_credit(site.counterparty.credit_status, ...)
src/gridq/finance/credit.py:203-205  crt is None (credit 'unknown') -> return None, None
$ .venv/bin/python (read-only) examples/real/sites/*.json counterparty.credit_status:
   all nine real sites (bishops_cleeve x2, colnbrook, ewelme x2, harwell, knowl_hill x2, tanhouse) -> "unknown"
$ grep -rn "debt_terms" src/gridq/cli.py   -> (no output: the CLI never injects debt terms; only app/demo.py:109 DEMO_DEBT_TERMS)
12 most recent persisted runs (runs/*/quote.json, all gb_v1_12, 9 real sites): min_dscr=None, gearing=None on 12/12
persisted tanhouse warning: "Credit status 'unknown' is not assessed — rate/tenor not set ..." /
   "DSCR unavailable: ... (unresolved, BLOCKERS B001)"
audit B: "B finance.credit build_debt_terms_from_credit gearing_override L190 tests=True"
```
**Verdict:** OPEN. The gearing half is now sourced. DSCR is still unpopulated on every real-site production run because no credit band and no lender-terms input exists on the CLI path. D57 says credit assessment is "ASSUMED APPROVED", but no study-basis code supplies a band, so the academic basis also runs without DSCR.

**Workstream:** WP-UW-03. "DSCR populated on every run that has debt terms" plus a lender-terms input object is spec A10-03, which explicitly names B001.

**Closure (WP-UW-03, 2026-11-26): CLOSED.** A typed `LenderTerms` input (D75) reaches finance from every entry point: `cli.py:411` (`gridq run --lender-terms FILE`), `app/app.py:1216` (`/api/run`) and `app/app.py:1325` (`/run/real`) into `generate_quote`, then `quotes/pipeline.py:578` into `run_financial_model`, where `finance/financial_model.py:282` sizes the debt on the terms. A run without terms records `DscrSummary.status = no_debt_terms` with the reason (`financial_model.py:493`), so no reader sees a bare `None`. Guards (each red with its line deleted, mutation-checked): `tests/test_dscr_on_every_run.py::test_a_run_with_lender_terms_shows_a_measured_dscr`, `::test_cli_run_with_lender_terms_records_and_prints_the_dscr`, `::test_app_wizard_hands_the_lender_terms_to_the_pipeline`, `::test_app_real_form_hands_the_lender_terms_to_the_pipeline` and `::test_a_run_without_debt_terms_shows_dscr_unavailable_with_the_reason`. The D57 credit-band point stands: no band is invented. A real site's DSCR now waits only on its lender's terms (a data ask).
