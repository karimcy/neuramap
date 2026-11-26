# WP-CORE-02 re-verification — dispatch group (MILP, MPC, engine, schedule, tariff calendar, sizing search)

Verified 2026-11-16 against the working tree of `uk` @ HEAD `55b168c` **plus** uncommitted edits in
`optimisation/{mpc_solver,milp_solver,battery_model,engine}.py` (today's WP-SIZE-01 MPC fix; the
`milp_solver.py` working-tree delta is only the `WindowOutcome` / `solve_window_outcome` addition, so
every MILP model-build verdict below is the same at HEAD). Read-only: no repo file written; pytest run
with `-p no:cacheprovider` and `PYTHONDONTWRITEBYTECODE=1`; `GRIDQ_ACCESS_LOG` unset (access log in-memory only).
Latest release resolved by `latest_version()` = **`gb_v1_12`**. Line numbers are working-tree snapshots.

Call-path audit run: `.venv/bin/python -m gridq.dev.call_path_audit --no-baseline --check A,B --json`
(250 findings, 1.63 s; full listing in `call_path_audit_A_B_2026-11-16.txt` beside this file). Lines relevant to this group:

```
B optimisation.engine:671 MilpEngine.__init__(use_mpc)            tests=True   <- no production caller passes use_mpc
B optimisation.engine:671 MilpEngine.__init__(mpc_settings)       tests=False
B optimisation.milp_solver:1053 MilpDispatchSolver.solve_window(weights) tests=False
B optimisation.mpc_solver:74 MpcDispatchSolver.__init__(battery_model) tests=True
A sizing.search:86  dense_grid_search()      tests=True   <- no production caller (N27)
A sizing.search:219 adaptive_refine_search() tests=True   <- no production caller (N27)
A sizing.search:375 pso_search()             tests=True   <- no production caller (N27)
A sizing.search:137 search_result_to_sizing_grid() tests=True
A optimisation.tariff_calendar:724 duos_rate_series() tests=True  (discarding wrapper; production uses _with_absences)
```

Test runs (this session):
```
$ pytest -p no:cacheprovider tests/test_milp_dispatch_physics.py tests/test_mpc_quarantine.py tests/test_mpc_infeasibility_regression.py
FAILED tests/test_milp_dispatch_physics.py::test_deg01_the_cycle_cost_stops_a_trade_whose_own_wear_erases_its_margin
FAILED tests/test_milp_dispatch_physics.py::test_deg01_applies_on_the_aging_weight_branch_too
2 failed, 39 passed, 2 warnings in 19.38s
$ pytest -p no:cacheprovider tests/test_duos_windows_by_dno.py tests/test_two_stage_power_domain.py tests/test_app_demo_engine_parity.py
123 passed, 2 warnings in 36.32s
```

## Summary

| id | verdict | WP | one-line reason |
|---|---|---|---|
| DEG-01 | CLOSED-FIXED (code) — **test guard RED** | WP-CORE-01 (test repair) | `deg_term` is in the base objective on both weight branches (fd6dfd6); measured: zero out-of-DFS discharge with c_deg vs 2,251 kWh without. But both behavioural guard tests have been red since the D57 DFS term (205ed92), so nothing currently fails if the fix is removed |
| B-01 | CLOSED-FIXED (annual MILP) | — (MPC simulator residual → WP-SIZE-01) | η_ch=η_dis=√RTE in SoE balance (fd6dfd6); tests green. The quarantined MPC's `ConstantEfficiencyBatteryModel` still puts all losses on charge |
| D-169 | CLOSED-FIXED | — | SoE bounded `[soc_min·E, soc_max·E]` from release; gb_v1_12 carries 0.15/0.95; tests green |
| B-17 | PARTIAL | WP-UW-07 | Unity PF now a labelled warning + optional release de-rating seam; no release supplies a PF, no reactive variable or charge |
| D-174 | PARTIAL | WP-SIZE-05 | Signed net position used in `MilpDispatchSolver.solve`, but the production sizing search (two_stage → PreloadedDispatchSolver) still dispatches on the clipped series |
| JSON-F-49 | PARTIAL (≡ D-174 limb) | WP-SIZE-05 | Same split; and on the sizing path the D-174 warning now claims the signed position reaches the physics when it does not |
| B-09 | CLOSED-FIXED | — | `mip_rel_gap=1e-6` pinned on binary solves, achieved gap read back, above-tolerance → INSUFFICIENT_DATA (fd6dfd6); tests green |
| B-14 ≡ JSON-F-47 | OPEN | WP-UW-08 | P50 is still the PF LP; haircut is a 20% uncited `needs_review` placeholder on gb_v1_12; no MPC calibration (MPC quarantined) |
| JSON-F-29 | OPEN (mitigated) | WP-SIZE-06 | Tender MWh still `min(solved discharge/charge, committed)` from the arbitrage optimum, priced on top of arbitrage+DUoS; F-19 call rate shrinks but does not remove it |
| JSON-F-42 | OPEN (latent) | WP-SIZE-02 | `if cap_table is not None:` with no else/warning in MILP; MPC `_throughput_cap` returns 0.0 silently; every shipped release carries the key |
| JSON-F-46 | PARTIAL | WP-SIZE-01 | Annual-LP limb fixed (D-169). MPC limb fixed only in the uncommitted tree and only on windowed releases; window-less release still plans to 0 against a 0.05 simulator floor |
| JSON-F-44 | OPEN (latent, quarantined) | WP-DISP-04 | MPC has no cross-window throughput tracking; cap prorated per 7-day window, 1 day applied |
| JSON-F-45 | OPEN (latent, quarantined; wider now) | WP-DISP-02 | MPC assembly still calls `energy_price_series`/`duos_rate_series_with_absences`/`red_band_mask` without `windows`; inner solves now use per-DNO windows, so the gap grew |
| JSON-F-40 ≡ F-43 | PARTIAL | WP-SIZE-04 | Declared tier now caps confidence (T1→MEDIUM, 642621b); £ still flat 8760/covered with no coverage gate, and a declared T2 is never checked against measured coverage |
| JSON-F-53 | OPEN | WP-DISP-07 | `binding_constraints` still tests `conn_limit − site_load`; ignores aux, provisioning, PF de-rate and signed net load |
| JSON-F-54 | OPEN | WP-CORE-03 | `ref()` still hashes first 48 HH of charge/discharge at 4 dp |
| LIT-F-05 | CLOSED-FIXED | — | `duos_windows_from_assumptions(assumptions, site.connection.dno_region)` on the production MILP; 14 areas in release; reader-layer test green (d779eb8) |
| JSON-F-38 ≡ F-41 | PARTIAL | WP-CORE-03 | Mean-of-quoted-bands price now announced (642621b) but still enters the objective; disclosure untested |
| JSON-F-39 | OPEN | WP-CORE-03 | peak/offpeak still mapped onto the DUoS red window, silently (note=None, 30 HH/week peak) |
| MD-F-18 | OPEN | WP-CORE-03 | `TariffStack` has no field saying whether `import_rates` include DUoS/levies |
| N27 | OPEN (latent) | WP-SIZE-04 | `best=max(scored,key=_rank_key)` ungated; no `priceable`/`candidate_viable`; no production caller (audit check A) |
| N29 | OPEN | WP-SIZE-04 | Proxy mode `cloud.extend(confirmed)`; `search_space_size=len(cloud)`; proxy mode is the production default |
| B-10 | OPEN | WP-UW-10 | Sensitivities apply only price/cost multipliers (+COD reported); no RTE axis |
| C-11 | OPEN (cosmetic) | WP-CORE-01 | `schedule.py:257` still says "each clamped at £0" |
| R8 (engine bill basis) | OPEN | WP-CORE-03 | `engine.py:2783-2784` no-spec basis says "priced on its own tariff stack", unconditioned; Harwell's tariff record says "Nothing in this stack is the site's own price" |
| MPC quarantine (A10-05) | PARTIAL — owner-held | WP-SIZE-01 | Silent-failure faults fixed in the uncommitted tree (regression file untracked); engine raise kept by owner decision; MPC residuals F-44/F-45/F-46/B-01-sim remain |
| ER-N1 | CLOSED-FIXED | — | Two-anchor power box (`load_relevant_capacity_kw`, d779eb8); end-to-end test green |
| ER-N2 | CLOSED-FIXED | — | `demo.py:256` uses `MilpEngine._sizing_basis` (959e7cd); parity tests green |
| ER-N3 | CLOSED-FIXED | — | `demo.py:673/680` delegates to `MilpEngine._rebase_dispatch_floor_streams` / `_restate_export_netting_site` (959e7cd); tests green |
| ER-N4 | OPEN (latent, documented) | WP-CORE-01 | Alternative `cannibalised_floor_gbp` netting still callable + test-pinned; engine still strips a "NOT NETTED" string |
| ER-N5 | OPEN (latent, labelled) | WP-SIZE-06 | `if not moved: return rebased, None` unchanged; label left true, residual uncovered |
| ER-N6 | OPEN (cosmetic) | WP-CORE-01 | Test name `test_n7_capacity_mic_equal_to_the_observed_peak_is_a_data_verdict` unchanged |
| ER-N7 | OPEN (cosmetic) | WP-CORE-01 | `_measured_charge_headroom` still says "half-hours" for `n` intervals at any `interval_min` |

Counts (33 ids): CLOSED-FIXED 8 (DEG-01, B-01, D-169, B-09, LIT-F-05, ER-N1, ER-N2, ER-N3) · PARTIAL 7 (B-17, D-174, JSON-F-49, JSON-F-46, JSON-F-40≡43, JSON-F-38≡41, MPC quarantine) · OPEN 18. No CLOSED-SUPERSEDED or CLOSED-INVALID: D57/D58 waive eligibility and stacking rules, not arithmetic integrity, so they supersede none of these.

---

### DEG-01

**Claim.** The MILP objective has no marginal degradation cost, so the break-even arbitrage spread is ~65% too low (08-16 doc says FIXED; confirm and find a test).

**Evidence.**
```
$ git log -S"deg_term = deg_cost" --format="%h %ad %s" -- src/gridq/optimisation/milp_solver.py
fd6dfd6 2026-08-13 Make absent reservations and null tariff lines visible instead of silent
  (commit message describes LIT-E-01/E-02; the 888-line milp_solver diff also carries DEG-01/B-01/D-169/B-17/D-174/B-09)

src/gridq/optimisation/milp_solver.py
1586  deg_cost, deg_note = degradation_cost_gbp_per_kwh(assumptions)
1986  revenue_term = (price_da * w_da * (discharge - charge) * dt).sum()
1987  deg_term = deg_cost * (export_flow * w_da * dt).sum()
2029-2030  objective = ( revenue_term - deg_term - _EPS*... )        # zero-weight (production LP) branch
2045  objective = revenue_term - deg_term                         # aging-weight (MPC) branch
release gb_v1_12: battery_opex.degradation_marginal_cost_gbp_per_mwh = 21.3 (needs_review)

Production path: cli.py:281 MilpEngine(...) -> quotes/pipeline.py:402 engine.solve -> engine._run_search ->
two_stage_sizing -> PreloadedDispatchSolver.solve (proxy_solver.py:88) -> MilpDispatchSolver._solve_milp

Tests (tests/test_milp_dispatch_physics.py, added 574487a):
  :165 test_deg01_the_cycle_cost_stops_a_trade_whose_own_wear_erases_its_margin   FAILED
       assert priced_wear.annual_discharge_kwh == pytest.approx(0.0)  -> 19565.734
  :218 test_deg01_applies_on_the_aging_weight_branch_too                           FAILED (19565.734)

Diagnosis probe (a throwaway session script, same fixture, gb_v0_1; reproduced by the re-pinned DEG-01 tests in tests/test_milp_dispatch_physics.py):
  dfs price max 0.159711 £/kWh (DFS ZONE 6 from postcode LE17; 8 called half-hours)
  discharge kWh in DFS periods 375.2   outside 0.0          <- with c_deg (fallback £21.3/MWh)
  zero-deg discharge outside DFS 2251.4                      <- c_deg published as 0
$ git log -S"dfs_price = np.zeros" -- src/gridq/optimisation/milp_solver.py
205ed92 2026-08-18 D57 item 4: build the DFS engine branch, which did not exist
```

**Verdict.** CLOSED-FIXED in code (production call site `milp_solver.py:1987/2030`, fixing commit
`fd6dfd6`). The fix is behaviourally intact: with the cycle cost the thin-spread trade is refused
outside DFS periods; without it, 2,251 kWh/week is cycled. **But the second clause of the FIXED bar is
not met today.** The two tests that would catch removal have been red since `205ed92` added the DFS
objective term: the fixture site resolves to DFS zone 6, so it now discharges into 8 called half-hours
at £0.16/kWh. A red test cannot detect a regression. No other test pins `deg_term` behaviourally (the
remaining DEG-01 tests check the coefficient helper or a wide spread that trades either way). The
docs don't record the red state anywhere.

**Workstream.** WP-CORE-01: the guard tests need re-basing (assert no discharge *outside* the DFS mask,
or pin a DFS-free fixture) so the regression test works again. That is baseline hygiene, not new physics.

### B-01

**Claim.** RTE is applied once, all losses on the charge side, so held-SoE floors certify deliverability the battery cannot meet.

**Evidence.**
```
src/gridq/optimisation/milp_solver.py
1394  eta = math.sqrt(rte)
1785-1790 balance_full = soe - soe[pred] - charge*(eta*dt) + export_flow*(dt/eta)
1930  soe >= soe_floor + _level / eta          # provisioning pre-position rescaled
$ git log -S"eta = math.sqrt(rte)" -> fd6dfd6 2026-08-13
tests/test_milp_dispatch_physics.py :242 test_b01_the_split_efficiency_preserves_the_finance_gross_up
  :254 test_b01_the_soe_recurrence_divides_the_discharge_by_eta   (asserts the recurrence; fails on the old form)
  :280 test_b01_a_held_energy_commitment_holds_the_stored_energy_it_costs
  -> all PASSED this session

Residual (quarantined MPC simulator):
src/gridq/optimisation/battery_model.py:86-88
  dc_charge_kwh = charge_kw * self.rte * dt_hours
  dc_discharge_kwh = discharge_kw * dt_hours
```

**Verdict.** CLOSED-FIXED for the annual MILP, which is the only production dispatch. Call site
`milp_solver.py:1394/1789`, commit `fd6dfd6`, guard `test_b01_the_soe_recurrence_divides_the_discharge_by_eta`.
Residual: the MPC's simulator (`ConstantEfficiencyBatteryModel.step`) still uses the pre-B-01
convention, so the MPC planner (√RTE split) and its plant (all losses on charge) disagree by ~√RTE.
That disagreement is a plan/plant mismatch.

**Workstream.** Residual → WP-SIZE-01, because it has to be fixed before the MPC quarantine can be lifted.

### D-169

**Claim.** SoE is bounded [0, E] with no usable window or DoD limit.

**Evidence.**
```
milp_solver.py:422  def _soc_window_fractions(assumptions)  -> reads warranty_cycle_limits.soc_{min,max}_fraction
milp_solver.py:1404 soc_min_frac, soc_max_frac, soc_note = _soc_window_fractions(assumptions)
milp_solver.py:1695 soe = model.add_variables(lower=soe_floor, upper=soe_ceiling, coords=[coords], name="soe")
release gb_v1_12: warranty_cycle_limits.soc_min_fraction=0.15, soc_max_fraction=0.95 (needs_review)
$ grep -l soc_min_fraction assumptions/*.yaml -> gb_v1_7 .. gb_v1_12 (key added 574487a)
code commit: fd6dfd6
tests: test_d169_a_released_soc_window_bounds_the_state_of_energy (:345), test_d169_the_usable_window_reduces_deliverable_energy (:370) -> PASSED
```

**Verdict.** CLOSED-FIXED. Production call site `milp_solver.py:1695`; the latest release supplies the window. Commits `fd6dfd6` (code) and `574487a` (release key). The guard is `test_d169_a_released_soc_window_bounds_the_state_of_energy`. Usable energy is reported separately from nameplate in a warning (`:1412`).

### B-17

**Claim.** Unity power factor is assumed everywhere, with no reactive variable, constraint or charge.

**Evidence.**
```
milp_solver.py:475  def _charge_power_factor(assumptions) -> (pf, warning)   # 1.0 unless mic_asc_headroom.assumed_power_factor
milp_solver.py:1458-1459 power_factor, pf_note = _charge_power_factor(assumptions); charge_conn_limit = conn_limit * power_factor
release gb_v1_12: mic_asc_headroom.assumed_power_factor => None   -> pf = 1.0 on every production run
$ grep -rn "reactive\|kvarh" src/gridq/optimisation/milp_solver.py  -> only warning prose; no variable/constraint/charge
tests: test_b17_unity_power_factor_is_a_labelled_warning_and_a_data_ask, test_b17_a_released_power_factor_derates_the_charge_bound -> PASSED
```

**Verdict.** PARTIAL. Closed: unity PF is now stated loudly with the exceeded-capacity exposure and a
data ask, and a sourced release factor would de-rate the charge bound (`fd6dfd6`). Open: no release
carries a PF, so every run is still kVA≈kW. There is no reactive variable, no P–Q polygon, and no
excess-reactive or exceeded-capacity charge on either side of the bill.

**Workstream.** WP-UW-07, whose scope names reactive power charges and excess-capacity charges.

### D-174

**Claim.** There is no PV representation, and negative net import is clipped to zero.

**Evidence.**
```
milp_solver.py:1197 def _load_series_signed(...) -> (clipped_kw, signed_net_kw, ...)  # nets export_kw/generation_kw
milp_solver.py:836  load, net_load, ... = self._load_series_signed(...)               # MilpDispatchSolver.solve
milp_solver.py:864  self._solve_milp(..., net_load_kw=net_load)
milp_solver.py:1624 charge_ub = np.minimum(power, np.maximum(0.0, charge_conn_limit - net_load - aux_kw))

Production SIZING path (what prices the reported candidate):
optimisation/two_stage.py:358  load, timestamps, interval_min, load_warn = base_solver._load_series(...)   # CLIPPED
optimisation/two_stage.py:395  confirm_solver = PreloadedDispatchSolver(base_solver, load=load, ...)
optimisation/proxy_solver.py:88-95 self._base._solve_milp(problem, self._load, ..., interval_weights=...)  # no net_load_kw
milp_solver.py:1376-1380 net_load = net_load_kw if not None else load        # -> clipped series
$ grep -rn "net_load_kw\|_load_series_signed" src/gridq --include=*.py | grep -v milp_solver.py -> (no caller)
engine.py:1350 _dispatch_energy_for -> self._dispatch_solver.solve(problem)   # volumes from the SIGNED path
Campaign sites: 9/9 real site series have 0 negative readings and no generation column.
```

**Verdict.** PARTIAL. Closed: signed net load and a generation channel exist and drive charge headroom
in `MilpDispatchSolver.solve` (`fd6dfd6`, guard `test_d174_surplus_is_real_charge_headroom_against_the_connection`).
Open: the production sizing search, including the full-series confirm stage that prices the reported
value streams, still dispatches on the clipped series. On a PV site the reported £ (clipped model) and
finance's charge/discharge volumes (`engine.py:1350`, signed model) would come from two different
physical models. The gap is latent on all nine campaign sites, which have no negative readings.

**Workstream.** WP-SIZE-05 (gensets and on-site generation in sizing). The fix is to carry the signed series through `two_stage` and `PreloadedDispatchSolver`.

### JSON-F-49

**Claim.** The solver silently rewrites negative imports to zero, against the ingestion rule "flagged, never corrected".

**Evidence.** Same code as D-174, plus the warning text the sizing path emits:
```
milp_solver.py:1279-1288 (inside _load_series_signed, reached via _load_series from two_stage.py:358)
  "negative net import reading(s) ...: the signed net position is carried into the dispatch physics, and
   clipped to 0 only for the behind-the-meter discharge bound and the reported billing basis (never
   clipped away from the charge headroom, D-174)"
two_stage.py:361 warnings.extend(load_warn)   # sentence reaches the run record
```

**Verdict.** PARTIAL (≡ the D-174 limb). The signed series exists, but the sizing path still clips. That
path now carries a sentence saying the signed position reaches the physics, which is **false there**.
The peak/dispatch split the finding describes (summary peak unclipped, dispatch clipped) persists on
the sizing path.

**Workstream.** WP-SIZE-05, same change as D-174. The false sentence should be fixed in the same edit.

### B-09

**Claim.** `mip_rel_gap` is never set and the achieved gap is never recorded.

**Evidence.**
```
milp_solver.py:209  _MIP_REL_GAP = 1e-6
milp_solver.py:2079-2081 binary_solve = use_binary or export_wash_binary; if binary_solve: solve_kwargs["mip_rel_gap"] = _MIP_REL_GAP
milp_solver.py:2094 achieved, gap_note = _achieved_mip_gap(model)   # above tolerance -> _UnpriceableDispatch -> INSUFFICIENT_DATA
commit fd6dfd6
tests: test_b09_a_binary_solve_pins_and_records_the_achieved_gap, test_b09_a_gap_above_tolerance_degrades_and_never_prices,
       test_b09_the_lp_path_claims_no_mip_gap -> PASSED
Reachability: binary solves occur on the export overlay (engine.py:1598 solve_with_export, export_wash_binary) and the MPC (quarantined).
```

**Verdict.** CLOSED-FIXED. Call site `milp_solver.py:2081/2094`, commit `fd6dfd6`. Guard is
`test_b09_a_gap_above_tolerance_degrades_and_never_prices`. The annual LP path correctly claims no MIP gap.

### B-14 ≡ JSON-F-47

**Claim.** P50 is a perfect-foresight full-year LP, and the haircut defaults to zero.

**Evidence.**
```
finance/cashflows.py:1037-1047 missing/unusable key -> warning "P50 dispatch revenue is the RAW perfect-foresight LP optimum" and return None
assumptions/gb_v1_12.yaml:1206-1214 perfect_foresight_haircut_pct: value 20.0, source "No citable source identified ... Must be calibrated", status needs_review
engine.py:686-723  use_mpc=True raises (quarantine) -> the MPC/PF calibration route the finding proposed is unavailable
```

**Verdict.** OPEN. The "defaults to zero" limb doesn't bite on any shipped release, because a missing
key degrades loudly. The substantive defect stands: P50 is the anticipative LP with an uncited 20%
deduction, and no calibration exists.

**Workstream.** WP-UW-08 (stochastic P50/P90), whose release text says it replaces "the placeholder haircut".

### JSON-F-29

**Claim.** Local-flex reserved kW is held out of dispatch, yet ordinary arbitrage discharge is counted as delivered tender MWh and paid as utilisation on top of arbitrage+DUoS.

**Evidence.**
```
milp_solver.py:2532 net_injection <= envelope - committed  (name="availability_reserve_aggregate")   # reservation unchanged (site-load cap removed by D57)
milp_solver.py:2156 capped = np.minimum(discharge_sol, flex_committed)  -> tender_window_discharge_mwh
milp_solver.py:2165 capped_charge = np.minimum(charge_sol, flex_committed) -> tender_window_charge_mwh  (D57 5b)
optimisation/valuation.py:688-700 mwh = schedule.tender_window_{charge|discharge}_mwh by awarded direction
optimisation/valuation.py:~713  called_mwh = mwh * call_rate; gross = rate_val * payable_mwh      # F-19 call rate
No objective term for local-flex utilisation; the same solved MWh also earn TARIFF_ARBITRAGE / DUOS_RED_BAND_AVOIDANCE.
docs/decisions.md D57: "Double-booking is a defect, not a constraint."  D58: "the optimiser therefore sells each half-hour ONCE".
```

**Verdict.** OPEN, mitigated. The F-19 call rate scales the paid volume down, and D57 5b fixed the
direction. But the utilisation MWh are still read off the business-as-usual arbitrage optimum, which is
already monetised as arbitrage+DUoS. They are not a modelled deviation from a baseline. D57/D58 don't
supersede this: D57 keeps double-booking a defect, and D58's "each half-hour sold once" holds only for
streams that are objective terms, which local-flex utilisation is not.

**Workstream.** WP-SIZE-06. It makes the DNO flex window a decision variable with reserved headroom, which is the structural fix.

### JSON-F-42

**Claim.** A missing `warranty_cycle_limits` key silently removes the "HARD" throughput cap.

**Evidence.**
```
milp_solver.py:1835-1837 throughput_cap_kwh = 0.0; cap_table = maybe(...); if cap_table is not None:
$ awk 'NR>=1836 && NR<=1895 && /^        (else|elif|if)/' milp_solver.py -> only "if cap_table is not None:" (no else, no warning)
milp_solver.py:2599 _WARRANTY_KEY gated into _bankable_status only (status downgrade, not a constraint)
mpc_solver.py:311-322 _throughput_cap: "if cap_table is None: return 0.0"
release gb_v0_1 and gb_v1_12 both carry the key -> latent
```

**Verdict.** OPEN (latent). Both solvers are unchanged, and the key is present on every shipped release.

**Workstream.** WP-SIZE-02 (shared constraint library), the single place the warranty constraint should refuse or warn on absence for both sizer and MPC.

### JSON-F-46

**Claim.** The MPC planner allows SoE 0 while its simulator forbids <5%, and the annual LP monetises 100% DoD.

**Evidence.**
```
Annual LP limb: milp_solver.py:1695 soe bounds [soc_min*E, soc_max*E]; gb_v1_12 = 0.15/0.95  (fd6dfd6, see D-169)
MPC limb (UNCOMMITTED working tree, git diff):
  mpc_solver.py:154  soc_min_frac, soc_max_frac, _ = _soc_window_fractions(assumptions)
  mpc_solver.py:160-161 soc_min=max(self._settings.soc_min, soc_min_frac), soc_max=soc_max_frac
  mpc_solver.py:205  current_soe = soc_min_frac * float(problem.energy_kwh)
  battery_model.py:73-76 soc_max field added; :91 clip to [soc_min*E, soc_max*E]
Window-less release (e.g. gb_v0_1, which the tests pin): soc_min_frac = 0.0 -> planner floor 0,
  simulator floor max(0.05, 0.0) = 0.05 (mpc_settings.py:51) -> mismatch persists; start SoE 0 is clipped UP
  to 0.05E by battery_model.step and booked as a charge nobody planned.
tests/test_mpc_infeasibility_regression.py:159 test_feasible_problem_on_a_windowed_release_solves_with_real_volumes -> PASSED (file is UNTRACKED)
```

**Verdict.** PARTIAL. The annual-LP limb is closed at HEAD. The MPC limb is closed only in the
uncommitted working tree, and only on releases that declare a window (gb_v1_7 onwards). On a window-less
release the planner and simulator still disagree.

**Workstream.** WP-SIZE-01. It owns the MPC start-state and floor fix, and the regression file must be committed.

### JSON-F-44

**Claim.** The MPC can breach the annual warranty cap ~7x: each window gets a 7-day quota but only one day is applied.

**Evidence.**
```
$ grep -n -i "throughput|consumed|budget|cap" src/gridq/optimisation/mpc_solver.py
301: throughput_cap_kwh = self._throughput_cap(problem, annual_factor)   # full-year, REPORT only (binding_constraints)
311-322: _throughput_cap ...
(no consumed-throughput state carried between windows; inner window cap = cap_cycles*E/annual_factor(window), milp_solver.py:1840)
```

**Verdict.** OPEN. It is latent because the engine refuses `use_mpc=True` and audit check B finds no
production caller supplying `use_mpc`.

**Workstream.** WP-DISP-04 (warranty-aware dispatch, throughput budget as a hard limit). It must close before WP-SIZE-01 lifts the quarantine.

### JSON-F-45

**Claim.** The MPC values its dispatch against a different DUoS calendar from the one it optimised.

**Evidence.**
```
mpc_solver.py:265 energy_price, price_warn = energy_price_series(site.tariff_stack.import_rates, timestamps)   # no windows
mpc_solver.py:274 duos, duos_absent = duos_rate_series_with_absences(site.tariff_stack, timestamps)          # no windows
mpc_solver.py:278 red_mask = red_band_mask(timestamps)                                                         # no windows
inner windows: milp_solver.py:1476-1477 duos_windows_from_assumptions(assumptions, site.connection.dno_region)
```

**Verdict.** OPEN, latent behind the quarantine, and **wider than filed**. Since LIT-F-05 the inner solve
uses each site's own licence-area windows, while the assembly still uses the generic national default.
Every site whose area differs from the default (e.g. SEPD 16:30–19:30, weekend amber) is now valued on
the wrong calendar, not only sites with a release override.

**Workstream.** WP-DISP-02 (rolling MPC on the shared constraint library), so one resolver feeds both plan and valuation.

### JSON-F-40 ≡ JSON-F-43

**Claim.** Partial-year metered samples are annualised by a flat 8760/covered-hours factor, with no seasonality correction or coverage gate.

**Evidence.**
```
milp_solver.py:1375 annual_factor = _HOURS_PER_YEAR / (weighted_intervals * dt)     # unchanged
$ grep -rn -iE "partial.year|part.year|coverage_fraction|minimum.coverage" optimisation/ sizing/ quotes/ finance/ -> no gate on the dispatch £
quotes/confidence.py:148-154 T1 -> ConfidenceBand.MEDIUM "annual figures are extrapolated ... inherit the seasonal bias" (642621b)
ingestion/validators.py:257-279 _check_tier_vs_data: checks T2-without-file-ref only; a DECLARED T2 is never compared with measured coverage
Campaign: 9/9 real series cover 8,759-8,760 h -> latent today
```

**Verdict.** PARTIAL. The data tier now caps the confidence band and names the seasonal bias. The £
annualisation is unchanged, though: there is no minimum-coverage gate, and a site declared T2 on a
3-month file would still get the HIGH band.

**Workstream.** WP-SIZE-04 (sized on sampled typical periods with full-year confirmation). The coverage
precondition and season-weighted annualisation belong to its representative-period machinery.

### JSON-F-53

**Claim.** Connection-binding detection ignores the auxiliary and provisioning draws the bound actually used.

**Evidence.**
```
schedule.py:306 headroom = connection_limit_kw - self.site_load_kw
schedule.py:309 charge_at_headroom = (self.charge_kw + tol >= headroom) & (headroom + tol < self.power_kw)
milp_solver.py:2202-2203 binding = schedule.binding_constraints(connection_limit_kw=conn_limit, ...)   # un-de-rated limit, clipped load
actual bound: milp_solver.py:1624/1651 min(power, charge_conn_limit - net_load - aux_kw [- prov_addition]), charge_conn_limit = conn_limit*pf
also schedule.py:312-316 throughput binding uses discharge only while the cap constrains export_flow (discharge+export, milp_solver.py:1877)
```

**Verdict.** OPEN. The reconstruction has drifted further from the real bound since filing: PF de-rate,
signed net load, and export in the throughput row are all missing from it.

**Workstream.** WP-DISP-07 (every decision logs its binding constraint and shadow price). Reading the
binding set from the solver's duals replaces this reconstruction.

### JSON-F-54

**Claim.** The schedule ref and quote hashes bake in sub-tolerance solver placement that the solver admits is non-unique.

**Evidence.**
```
schedule.py:433-434 "charge_kwh_profile_sha": [round(float(x), 4) for x in self.charge_kw[:48]], "discharge_..." likewise
milp_solver.py:117-126 _EPS_AGE comment: placement "deterministic per machine, not unique by construction"
milp_solver.py:2613 / mpc_solver.py:351 dispatch_schedule_ref=schedule.ref()  -> sizing/sizer.py:356 onto the evaluation
```

**Verdict.** OPEN, unchanged.

**Workstream.** WP-CORE-03. OptimisationResult is re-frozen there, which is where `dispatch_schedule_ref`
identity semantics should be redefined (for example, hash aggregates, or canonicalise placement).

### LIT-F-05

**Claim.** One national DUoS red band stands in for 14 DNOs' published CDCM bands, with no bank-holiday treatment.

**Evidence.**
```
milp_solver.py:1476-1477 windows, windows_note = duos_windows_from_assumptions(assumptions, site.connection.dno_region)
tariff_calendar.py:233 duos_windows_for_dno(...) reads duos.band_windows_by_dno; loud fallback on unknown/absent region
tariff_calendar.py:38-40,126-129 bank holidays banded as the weekday (statements say "Monday to Friday (Including Bank Holidays)")
release gb_v1_12: duos.band_windows_by_dno present (14 areas, needs_review)
examples/real/sites/*.json carry dno_region (e.g. ewelme SSEN_SEPD, bishops_cleeve NGED_WM)
$ git log -S"assumptions, site.connection.dno_region" -- milp_solver.py -> d779eb8 2026-08-16
tests/test_duos_windows_by_dno.py:680 test_the_areas_own_calendar_reaches_the_warnings_a_reader_reads (runs MilpDispatchSolver.solve, asserts SEPD 16:30-19:30 note) -> PASSED
```

**Verdict.** CLOSED-FIXED. Production call site `milp_solver.py:1476-1477`, which is reached by the
sizing path as well. Commit `d779eb8`. Guard is the reader-layer test at `:680`, which explicitly
targets deleting the region argument.

### JSON-F-38 ≡ JSON-F-41

**Claim.** A missing tariff band rate is invented as the mean of the other bands.

**Evidence.**
```
tariff_calendar.py:807-818 fallback = sum(normalised.values())/len(normalised); prices = np.full(..., fallback); note when a missing band is present
probe: energy_price_series({'red':0.35,'green':0.10}, 1 week)
  note: "tariff band(s) ['amber'] were not quoted by the site; those half-hours are priced at £0.2250/kWh, the mean ..."  amber-priced HH: 130
production: milp_solver.py:1479 energy_price_series(site.tariff_stack.import_rates, timestamps, windows) -> objective price
$ git log -S"the mean of the bands that WERE quoted" -> 642621b 2026-08-11
$ grep -rn "not quoted|mean of" tests/... -> no test pins the disclosure
```

**Verdict.** PARTIAL. The invented price is now announced. It still enters the objective and the
arbitrage £, and the in-source comment defers the real fix ("WP-4 replaces it ... once TariffStack can
express its own basis"). The disclosure has no test guarding it.

**Workstream.** WP-CORE-03 (SiteModel/TariffStack re-freeze), so missing bands become a declared
DerivedAssumption, D45-style, rather than a mean. Sequence it after MD-F-18.

### JSON-F-39

**Claim.** peak/offpeak labels are guessed onto the DUoS red window (weekday 16:00–19:00 only).

**Evidence.**
```
tariff_calendar.py:755-758 if scheme == _PEAK_OFFPEAK_LABELS: return np.where(red_band_mask(timestamps, windows), "peak", "offpeak")
probe: energy_price_series({'peak':0.3,'offpeak':0.1}, 1 week) -> note None; peak half-hours/week: 30 (15 h)
```

**Verdict.** OPEN. The mapping is still a guess and now also inherits per-DNO windows. It is still
silent: no note is returned.

**Workstream.** WP-CORE-03. The tariff record must carry its own peak window (or refuse with a declared assumption). That is a TariffStack schema change.

### MD-F-18

**Claim.** TariffStack can't express whether `import_rates` already include DUoS/levies, and the code stacks DUoS unconditionally.

**Evidence.**
```
src/gridq/schemas/tariff.py:128-155 TariffStack fields: supplier, tariff_name, standing_charge_per_day_gbp, import_rates, export_rates,
  duos_{red,amber,green}_rate, levies, windowed_levies, capacity_charge_gbp_per_kva_day, source, confidence   -> no basis/inclusive flag
$ grep -rn -iE "duos_inclusive|includes_duos|rates_include|import_rates_basis" src/gridq -> none
milp_solver.py:1585 price = energy_price + duos + levy_rate + windowed_levy + dfs_price   # unconditional stacking
```

**Verdict.** OPEN, unchanged.

**Workstream.** WP-CORE-03, which re-freezes SiteModel. Adding the basis field is a contract change.

### N27

**Claim.** In the continuous search's rank key, an unpriceable candidate is indistinguishable from a zero-value one, and `best` is not gated on viability.

**Evidence.**
```
sizing/search.py:43-50 _rank_key = (_METRIC_RANK tier, objective_value, -power, -duration)
sizing/search.py:127, 364, 523 best=max(scored, key=_rank_key)
$ grep -c "candidate_viable\|priceable" src/gridq/sizing/search.py -> 0
sizing/evaluator.py:174-186 _score: unpriceable -> (METRIC_PRICED_FLOOR, 0.0)
best_feasible (covenant-gated) exists since b13ec56 (2026-07-02); search.py last touched c70cad2 2026-07-03
call_path_audit A: dense_grid_search / adaptive_refine_search / pso_search -> no production caller
```

**Verdict.** OPEN, latent. No shipped path calls these searches; production sizing is `two_stage` with its own `_select`.

**Workstream.** WP-SIZE-04 (co-optimising sizer). Either gate `best` on viability with a `priceable` tier, or retire the unwired searches.

### N29

**Claim.** In the two-stage path, `search_space_size` counts the same candidate twice.

**Evidence.**
```
optimisation/two_stage.py:747-749 cloud = list(evaluations.values()); if proxy_mode: cloud.extend(confirmed)
optimisation/two_stage.py:750-755 non-proxy branch de-duplicates by (power_kw, duration_hours)
optimisation/two_stage.py:760 search_space_size=len(cloud)
git blame :747-760 -> c2092db0 2026-07-02 (unchanged); proxy_mode = typical is not None (production default on a full-year series)
```

**Verdict.** OPEN. It is live on every proxy-mode run, but it inflates a reported count, not a £.

**Workstream.** WP-SIZE-04, which owns the sizing search's reported surface.

### B-10

**Claim.** The sensitivity pack has no RTE axis.

**Evidence.**
```
quotes/sensitivities.py:42 _APPLIED_SCENARIO_FIELDS = ("price_multiplier", "cost_multiplier")
quotes/sensitivities.py:140-192 cod_delay_months reported, applied to nothing (N18)
$ grep -n -iE "rte|round_trip|efficiency" src/gridq/quotes/sensitivities.py -> none
```

**Verdict.** OPEN, unchanged.

**Workstream.** WP-UW-10 (downside cases), because an RTE case needs a dispatch re-solve, the same machinery as its availability-shock case.

### C-11

**Claim.** A stale docstring in schedule.py says the components are "each clamped at £0".

**Evidence.**
```
schedule.py:257 per-component ``arbitrage_value_gbp``/``duos_avoidance_value_gbp`` (each clamped at £0),
schedule.py:161-189 both components are SIGNED, not clamped (F-02)
also stale at :260 "zeros when the tariff had no time-mappable spread" (a flat tariff returns a constant array since F-01)
```

**Verdict.** OPEN (cosmetic), with a second stale clause found on `:260`.

**Workstream.** WP-CORE-01 (clean baseline: stale assertions fixed).

### R8 — engine.py bill basis asserts "its own tariff stack" on a borrowed tariff

**Claim.** The bill-basis text in engine.py says "its own tariff stack" even when the tariff was borrowed (2026-08-16 item R8).

**Evidence.**
```
src/gridq/optimisation/engine.py:2783-2784 (no-spec _before_only_customer_bill basis=):
  "dispatch-invariant: the site's own metered half-hourly load priced on its own "
  "tariff stack with no battery in it. ..."      # not conditioned on tariff provenance
engine.py:1319 docstring repeats "the site's own load on its own tariff stack"
schedule.py:398-402 with-spec basis now says "the site's import_rates" (no "own tariff" claim)
examples/real/sites/harwell.json tariff_stack.source:
  "ASSUMED TARIFF — THERE IS NO HARWELL TARIFF DATA OF ANY KIND ... Nothing in this stack is the site's own price."
```

**Verdict.** OPEN on the no-spec path. The with-spec occurrence no longer makes the claim.

**Workstream.** WP-CORE-03. The sentence can only be conditioned honestly once TariffStack carries a
typed provenance or IC-4 evidence reference (own / by analogy / assumed). Reading the free-text
`source` would be fragile. A one-line interim fix is possible in engine.py.

### MPC quarantine (spec A10-05)

**Claim.** `MilpEngine(use_mpc=True)` raises because the MPC fails closed but silent (`status="optimal"` with 0.0 volumes).

**Evidence.**
```
engine.py:686-723 if use_mpc: ... raise InvalidRequestError("MilpEngine refuses use_mpc=True: ... QUARANTINED at the engine entry point ...")
   (working-tree text; comment: "faults 1-4 are FIXED in MpcDispatchSolver ... This block is deliberately KEPT ... owner's decision")
mpc_solver.py:205 current_soe = soc_min_frac*E       (fault 1: start at the usable floor, uncommitted)
mpc_solver.py:224-243 refused window -> return self._unsolved(problem, refusal, reason, extra_warnings=...)  (faults 2-4, uncommitted)
milp_solver.py:1076 solve_window_outcome -> WindowOutcome(schedule | refusal_status, reasons)   (uncommitted)
tests/test_mpc_quarantine.py (7bfa665, committed)            -> 4 PASSED
tests/test_mpc_infeasibility_regression.py (UNTRACKED, ?? in git status) -> PASSED
call_path_audit B: MilpEngine.__init__(use_mpc) supplied by no production caller
```

**Verdict.** PARTIAL, held by owner decision. The silent-failure mode is fixed, but only in uncommitted
code whose regression suite is an untracked file, so neither is in HEAD. The quarantine stays by owner
decision. Open MPC residuals found in this pass:
- JSON-F-44 (no cross-window warranty budget).
- JSON-F-45 (valuation calendar ≠ optimisation calendar, now including per-DNO windows).
- JSON-F-46 on window-less releases.
- The simulator still uses the pre-B-01 efficiency split (`battery_model.py:86-88`).

**Workstream.** WP-SIZE-01. Its acceptance requires the permanent regression test, so commit
`tests/test_mpc_infeasibility_regression.py` and the four source files. Lifting the quarantine also
needs F-44/F-45/F-46 and the simulator split addressed.

### ER-N1

**Claim.** The search box loses its bottom: anchoring on the connection raises the smallest searchable system.

**Evidence.**
```
optimisation/two_stage.py:137 load_relevant_capacity_kw(...) = min(binding, min(MIC,ASC), max_import_kw, series peak)
optimisation/two_stage.py:181 power_search_floor_kw = 0.05 * relevant_capacity_kw ; :442 box_power_lo = power_search_floor_kw(relevant_capacity_kw)
$ git log -S"def power_search_floor_kw" -> d779eb8 2026-08-16
tests/test_two_stage_power_domain.py:326 test_the_search_reaches_the_small_systems_and_says_so (end-to-end two_stage) -> PASSED
```

**Verdict.** CLOSED-FIXED. Call site `two_stage.py:439-442`, commit `d779eb8`, guard at `:326`.

### ER-N2

**Claim.** `app/demo.py` still runs the deleted capacity rule (`max_import_kw or mic_kw`).

**Evidence.**
```
src/gridq/app/demo.py:256 basis = MilpEngine._sizing_basis(site)
$ git log -S"MilpEngine._sizing_basis(site)" -- src/gridq/app/demo.py -> 959e7cd 2026-08-13
tests/test_app_demo_engine_parity.py:284 test_the_demo_and_the_engine_derive_the_identical_capacity_basis; :355 test_no_second_capacity_basis_survives_in_the_demo_module -> PASSED
```

**Verdict.** CLOSED-FIXED. This confirms the 08-16 report. DemoEngine is the MILP behind `POST /api/run` (`app/app.py:1055/1203`).

### ER-N3

**Claim.** `app/demo.py` double-counts the export floor (MD-F-05(b) verbatim).

**Evidence.**
```
src/gridq/app/demo.py:673 rebased, rebase_note = MilpEngine._rebase_dispatch_floor_streams(...)
src/gridq/app/demo.py:680 MilpEngine._restate_export_netting_site(...)
commit 959e7cd; tests/test_app_demo_engine_parity.py:394/:419/:447 -> PASSED
```

**Verdict.** CLOSED-FIXED. This confirms the 08-16 report.

### ER-N4

**Claim.** There are two live, contradictory netting implementations, both test-pinned.

**Evidence.**
```
optimisation/valuation.py:1916 wholesale_contracted_stream_result(..., cannibalised_floor_gbp=None) still accepts the alternative deduction
optimisation/valuation.py:1954-1956 None -> appends "GROSS — NOT NETTED (MD-F-05 limb b) ..."
optimisation/engine.py:177 _STALE_GROSS_CLAIM_MARKER = "NOT NETTED"; _restate_export_netting_site strips it when rebased
engine.py:2346-2353 / demo.py:660-667 call it WITHOUT cannibalised_floor_gbp (deliberate, documented in-source)
```

**Verdict.** OPEN, latent and documented. No runtime double count, because no caller passes the
parameter. But the rejected alternative is still callable and test-pinned, and the engine still depends
on stripping a string. Both points match the finding. The design rationale lives only in docstrings,
not in a decision record or `docs/dormant_code.toml`.

**Workstream.** WP-CORE-01. Retire the rejected alternative and its pins, or declare it in `dormant_code.toml`.

### ER-N5

**Claim.** The rebase is a no-op when no dispatch-derived priced-floor stream is present.

**Evidence.**
```
optimisation/engine.py:2479-2480 if not moved: return rebased, None
optimisation/engine.py _restate_export_netting_site: "``rebased=False`` leaves it untouched — nothing was re-based, so it is then true."
```

**Verdict.** OPEN, latent and honestly labelled. Under D57, arbitrage and DUoS are always priced, so this branch is unlikely to be reached on current runs. I did not measure whether it is reachable.

**Workstream.** WP-SIZE-06. It makes wholesale export a co-optimised decision variable, which removes the overlay-delta construction.

### ER-N6

**Claim.** A stale test name asserts the refuted framing.

**Evidence.**
```
tests/test_selection_metric_and_verdicts.py:155 def test_n7_capacity_mic_equal_to_the_observed_peak_is_a_data_verdict(make_site)
  body: assert note.startswith("connection capacity unknown")
```

**Verdict.** OPEN (cosmetic), unchanged. Note that `tests/` is being edited concurrently by other agents.

**Workstream.** WP-CORE-01.

### ER-N7

**Claim.** `_measured_charge_headroom` says "half-hours" for any interval.

**Evidence.**
```
optimisation/engine.py:2096 def _measured_charge_headroom ...
  :2118 n = int(headroom.size)   (interval_min-agnostic)
  :2121-2126 f"... over the {n:,} half-hours priced ... positive in {positive:,} of {n:,} half-hours"
```

**Verdict.** OPEN (cosmetic), unchanged. The arithmetic is interval-correct (`dt = interval_min/60`); only the wording is wrong.

**Workstream.** WP-CORE-01.
