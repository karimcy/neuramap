# WP-CORE-02 re-verification: value stack, valuation, ingestion, provenance, schemas, publication

Verified 2026-11-16 against `uk` @ HEAD `55b168c` plus the working tree. The working tree has uncommitted
edits in `optimisation/engine.py`, `milp_solver.py`, `mpc_solver.py` and `battery_model.py`. Every
verdict below that rests on `engine.py` was re-checked with `git show HEAD:` (see MD-F-05 and JSON-F-35).
`value_stack/`, `optimisation/valuation.py`, `ingestion/`, `quotes/`, `schemas/` and `cli.py` have no
uncommitted diff. Line numbers are working-tree snapshots, so match on the signature.

Call-path audit run (read-only): `.venv/bin/python -m gridq.dev.call_path_audit --no-baseline --check A,B --json`
gave 245 findings. `coverage.allowlist_state = "no entries (no docs/dormant_code.toml)"`: the file does not
exist and never has (`git log --all -- docs/dormant_code.toml` is empty). The 08-16 doc said it existed
but was empty. The relevant lines are quoted in each section below.

## Summary

| id | verdict | WP | one-line reason |
|---|---|---|---|
| MD-F-04 (+ D-92) | PARTIAL (by decision) | WP-UW-01 | D-92 eligibility predicate is now built and wired on both paths, but on every run it is satisfied by a D57 assumed portfolio aggregate. The power+energy budget limb was deliberately reopened by D57/D58. No SoC-headroom reservation exists, and export rights are assumed. |
| MD-F-05 (limb b) | CLOSED-FIXED | — | `engine.py:2359` re-bases the floor onto the overlay dispatch (present at HEAD). The demo path delegates to it. `test_engine_connection_basis.py:637` drives `_final_result`. |
| MD-F-12 | OPEN | WP-UW-05 | The gate still opens on `any(r.reference)` and both pricers still sum every requirement's declared £. Code is unchanged. |
| JSON-F-50 | OPEN (latent) | WP-CORE-03 | The proxy `MicAscHeadroomStream._price` still prices £12,000/MW/yr FIRM, which contradicts D57's "£0, carried by AGREED_CAPACITY_REDUCTION". `value_function` has no src caller. |
| D-158 | PARTIAL | WP-SIZE-09 | EFA/F/K structure is wired (`valuation.py:1136`). F (`expected_availability_pct: null`) and K are both unsourced and held at 1.0, and there is no product split or per-block award. |
| D-116 | PARTIAL (MUPF half now LIVE) | WP-UW-11 | The availability leg is now priced (D54/D57, `valuation.py:771`). `utilisation_performance_ratio` is still never supplied, so the leg is booked gross, an upper bound, on every awarded site. |
| JSON-F-30 | OPEN (retained under D57) | WP-SIZE-09 | The CM fallback still prices `power_mw × derate` when no commitment compiled. It is now labelled "D57 deliverability ASSUMED". There is no `min(committed_power_kw)` clamp. |
| E-06 | CLOSED-FIXED | — | `MarketStream.DFS` exists, is priced from the dispatch (`valuation.py:1783 → _dfs_value`) and is zone-scaled (D60). The K-factor residue is carried by the dfs_performance_factor item. |
| MD-F-06 | OPEN | WP-CORE-03 | `_canonical_run_input` still pins `assumptions.metadata.version` (a string) only. CLAUDE.md §5 and D60(b) both concede this. |
| MD-F-09 | OPEN | WP-CORE-03 | The site dump carries `half_hourly_file_ref` (a path) plus summary stats only. No series content hash exists anywhere. |
| MD-F-13 | OPEN (latent) | WP-GRC-12 | `assess_reprice` still iterates `SECTION_NAMES` only, so OPTIONAL_SECTION_NAMES (availability, penalties, tnuos, bill_products) are skipped. `assess_quote_reprice` has no production caller (check A). |
| JSON-F-31 | OPEN | WP-CORE-05 | `cli.render` still passes any `--site` JSON straight to `generate_artifacts` with no site_id or input_hash check. |
| MD-F-08 | OPEN | WP-SITE-03 | The inferred grid is still only a WARNING ("coverage maths assume a 30-min grid"). It is neither honoured nor refused. |
| MD-F-10 + H-1 | CLOSED-FIXED | — | `pipeline.py:367` calls `reconcile_declared_against_measured`, and `:603/:641` pass `reconciliation=`. `test_gates_record_and_continue.py:68` exercises the real path. |
| JSON-F-25 | OPEN | WP-SITE-03 | The only `quality_flag` hits in src are the docstring and the alias table. |
| JSON-F-28 | OPEN (Tier B) | WP-SITE-03 | There is no variance, day-tiling or lag-48 screen. Only the H-1 dropout screen exists. Thresholds need a human decision. |
| N9 (both routes) | CLOSED-FIXED | — | Route 2 is now committed at HEAD (`d779eb8`). Route 1 stamps MODELLED (`642621b`). Both have tests. |
| JSON-F-35 | PARTIAL | WP-CORE-03 | The engine now detects peak > min(MIC,ASC) and states it on the run (`engine.py:1879`, at HEAD). `validate_site_model` still never compares them, and the headroom fields are unreconciled. |
| JSON-F-36 | OPEN | WP-CORE-03 | The `OptimisationResult._checks` validator still has no power×duration=energy check. The SizingGridPoint and DispatchProfile copies have none either. |
| JSON-F-48 | OPEN (reproduced) | WP-CORE-04 | `_parse_timestamps` still uses `utc=False` with no retry. Under pandas 3.0.5 a mixed-offset series raises `ValueError`, which `cli.py:451` swallows with a misleading note. |
| R1 | PARTIAL | WP-CORE-05 | `publish_run` is now reached from `scripts/run_d57_campaign.py:356`, the operator campaign driver. CLI and app never reach it. `render_publishable_pack`, `is_persisted` and `generate_artifacts(require_persisted=)` remain unreached. dormant_code.toml is absent. |
| R2 | PARTIAL | WP-UW-01 | `StudyBasis` and `AllStreamsEnabledStudyBasis` are now produced by `scripts/run_d57_campaign.py:223,244`. src has no producer and CLI/app have no basis flag. `derive_flat_export_tariff` has zero callers. `option_set()` is not consumed by dispatch. |
| dfs_performance_factor | OPEN (dormant) | WP-UW-11 | Check A: `dfs_performance_factor` and `dfs_settlement_value_gbp` are both unreferenced in src. D60(e) declined to apply fleet K to dispatch-measured delivery, so this is dormant but undeclared. |
| eac_response_value(toll_reference=) | OPEN (latent, safe default) | WP-UW-06 | Check B: never supplied at its single production call (`valuation.py:1136`). The default `None` applies F and K, the non-exempt direction. No SiteModel field carries a toll contract reference. |
| mypy over tests/ | OPEN | WP-PLAT-01 | `pyproject.toml:79` still has `packages = ["gridq"]`. The new (untracked) `.github/workflows/ci.yml:73` runs bare `mypy`, so tests stay unchecked. |

---

### MD-F-04 (+ D-92)

**Claim.** Consent alone opens BM and ancillary revenue into the debt-sized floor. Three limbs were open: no SoC-headroom reservation, no export-rights check, and no eligibility predicate (D-92: no 1 MW or aggregation gate). The power+energy budget limb was recorded as fixed.

**Evidence.**
```
# D-92 eligibility predicate now exists and is applied BEFORE consent on the engine path
src/gridq/value_stack/gb_streams.py:203  def market_entry_verdict(stream, *, unit_mw, portfolio_mw) -> gates.EligibilityVerdict:
    """Can ``stream`` be entered at ``unit_mw`` (or via a ``portfolio_mw`` aggregate)? (D-92) ..."""
src/gridq/value_stack/gb_streams.py:179  _MIN_UNIT_MW_BY_STREAM = {BALANCING_MECHANISM: _MIN_BM_UNIT_MW, ANCILLARY_DC_DM_DR: ..., CAPACITY_MARKET: 1.0}
src/gridq/optimisation/valuation.py:988  # D-92 FIRST: eligibility is a precondition on the money, checked before consent ...
src/gridq/optimisation/valuation.py:990  verdict = _market_entry_verdict(stream, schedule, context)
src/gridq/value_stack/gb_streams.py:5476/5564  ineligible = self._ineligibility_gate(...)   # proxy BM / DC-DM-DR
$ git log -S"def market_entry_verdict" --oneline -- src/gridq/value_stack/gb_streams.py | tail -1
769cdc4 Null guard at the live gate layer, gb_v1_9, and the call-path audit   (2026-08-15)
tests: tests/test_market_entry_eligibility.py

# ...but D57 satisfies it on every run by an ASSUMED portfolio aggregate
src/gridq/sizing/deliverability.py:254  # D57: the portfolio aggregate is ASSUMED APPROVED on every run, so it is populated whether or
src/gridq/sizing/deliverability.py:260  portfolio_mw = d57_portfolio_aggregate_mw()
src/gridq/sizing/deliverability.py:73   def d57_portfolio_aggregate_mw() -> float:  """The ASSUMED-APPROVED aggregated portfolio MW (D57) -- the largest sourced minimum."""

# The "closed" power+energy budget limb has been deliberately REOPENED (D57 limb 3 / D58 limb 1)
src/gridq/value_stack/capacity_budget.py:167  def allocate_capacity(...):
    **D57 (Olly, 2026-08-18) — STACKING EXCLUSIVITY IS ASSUMED SATISFIED, so nothing is cut.**
    Every claim is now awarded in full and the oversell is STATED rather than enforced.
$ git log -S"STACKING EXCLUSIVITY IS ASSUMED SATISFIED" --oneline -- src/gridq/value_stack/capacity_budget.py
e684e66 D57 items 5-6 part 1: retire the deliverability clamps, pay the stacking claim in full, state the assumption
caller: src/gridq/optimisation/valuation.py:1857  budget = allocate_capacity(claims, rated_power_mw=rated_mw, usable_energy_mwh=usable_mwh)

# SoC-headroom reservation / export-rights limbs
$ grep -rn -i -e soc_headroom -e soe_headroom -e headroom_reservation -e export_rights -e eligibility_predicate -e reserved_soe src/gridq/value_stack src/gridq/optimisation
(no output)
docs/decisions.md D57 limb 2: export consent, export MPAN, G99/G100 ... ASSUMED APPROVED
```

**Verdict.** PARTIAL (by decision).
- Eligibility predicate (D-92): BUILT AND WIRED on both paths. The engine path checks it at `valuation.py:990`, the proxy path at `gb_streams.py:5476/5564`, and `tests/test_market_entry_eligibility.py` covers it. On the academic basis it is neutralised by design, because `sizing/deliverability.py:260` gives every run an assumed aggregate. That makes the predicate a real-world gate D57 waives, not a fix that bites.
- Power+energy budget limb: REOPENED deliberately by D57/D58 limb 1. `allocate_capacity` pays every claim in full and discloses the shortfall.
- SoC-headroom reservation: OPEN.
- Export-rights check: waived by D57 limb 2.

**Workstream.** WP-UW-01. `underwriting_basis_v1` is the basis that "enforces gates, consents, size minimums, stacking rules and route-to-market from evidence objects", which is every open limb here. Under D57 none of them may reduce an academic figure. The SoC-headroom reservation physics also needs WP-SIZE-06 (reserved headroom), and the evidenced eligibility inputs need WP-UW-02.

### MD-F-05

**Claim.** Limb (b): contracted export is appended gross onto the unchanged no-export floor, so cannibalised arbitrage is never netted. The 08-16 doc says this is FIXED on both paths.

**Evidence.**
```
$ git show HEAD:src/gridq/optimisation/engine.py | grep -n "rebased, rebase_note = self._rebase_dispatch_floor_streams\|def _rebase_dispatch_floor_streams"
2359:                rebased, rebase_note = self._rebase_dispatch_floor_streams(
2403:    def _rebase_dispatch_floor_streams(
src/gridq/optimisation/engine.py:2354  # MD-F-05 limb (b): the base streams are the NO-EXPORT solve's. Appending gross
src/gridq/app/demo.py:673  rebased, rebase_note = MilpEngine._rebase_dispatch_floor_streams(   # demo delegates to the engine
$ git log -S"_rebase_dispatch_floor_streams" --oneline | tail -1
574487a Protect the wave's new artifacts: 470 tests, 2 releases, 3 modules, 8 working papers  (2026-08-13)
tests/test_engine_connection_basis.py:637 test_the_composed_floor_is_overlay_terms_plus_gross_export_not_no_export_terms
    result = engine._final_result(request, _chosen_with_gated_wholesale(base_streams))
    assert _net(result.value_streams) == pytest.approx(_net(base_streams) + 8_000.0 - 5_000.0)
tests/test_app_demo_engine_parity.py:394 test_the_demo_rebases_the_dispatch_floor_components_onto_the_overlay
call_path_audit: check B flags valuation.wholesale_contracted_stream_result(cannibalised_floor_gbp) L1908. This is the documented, deliberately unpassed alternative (08-16 §6 FP list), not a defect.
```

**Verdict.** CLOSED-FIXED. The production call site is `optimisation/engine.py:2359`, inside `_final_result` and present at HEAD. The fixing commit is `574487a`. `tests/test_engine_connection_basis.py:637` calls `engine._final_result` and asserts the floor is lower by exactly the cannibalised £. It would fail if the call at `:2359` were removed.

**Workstream.** —

### MD-F-12

**Claim.** One referenced load-service requirement opens the gate, after which unreferenced requirements' declared £ also enter the CONTRACTED floor.

**Evidence.**
```
src/gridq/value_stack/gb_streams.py:5190  if not any(r.reference for r in requirements):
                                              return self._gate(GateStatus.CONDITIONAL, ...)
src/gridq/value_stack/gb_streams.py:5204  return self._gate(GateStatus.OPEN, consent_status=consent_status)
src/gridq/value_stack/gb_streams.py:5218  for req in requirements: ... revenue += req.declared_counterfactual_gbp_per_year   # no reference filter
src/gridq/optimisation/valuation.py:1245 def _load_service_value(...):
src/gridq/optimisation/valuation.py:1267     revenue += req.declared_counterfactual_gbp_per_year
src/gridq/optimisation/valuation.py:1274     if not req.reference: notes.append("... has NO contract reference — a modelling input ...")   # note only
production dispatch: src/gridq/optimisation/valuation.py:1785  elif sid == MarketStream.LOAD_SERVICE: revenue, s_used, notes = _load_service_value(schedule, site)
```

**Verdict.** OPEN. The code is unchanged from the finding. A mixed set of referenced and unreferenced requirements still prices every declared £ once any single reference exists. On the D57 academic basis contracts are assumed approved, so the harm lands only on an evidence-gated basis.

**Workstream.** WP-UW-05. The fix is to tag each pound with its own evidence id, so a referenced requirement is contracted and an unreferenced one conditional. That is exactly the three-tier per-pound classification.

### JSON-F-50

**Claim.** The proxy value path prices MIC/ASC headroom as FIRM £/MW/yr, which the engine path itself rules a double count.

**Evidence.**
```
src/gridq/value_stack/gb_streams.py:1698 class MicAscHeadroomStream(FloorStream):
src/gridq/value_stack/gb_streams.py:1768     return _priced_per_mw_yr("mic_asc_headroom", "value_gbp_per_mw_yr", dispatch, assumptions, scenario, ...)
assumptions/gb_v1_12.yaml:1947 mic_asc_headroom: value_gbp_per_mw_yr: value: 12000  status: placeholder
docs/decisions.md D57: "MIC_ASC_HEADROOM (its GBP is carried by AGREED_CAPACITY_REDUCTION). Forcing those to a non-zero figure books the same pound TWICE"
$ grep -rn "value_function" src | grep -v "def value_function" | cut -d: -f1 | sort | uniq -c
   2 src/gridq/value_stack/gb_streams.py   (docstrings: ":635 ... value_function, which has no caller in src/ at all")
   1 src/gridq/value_stack/interface.py
   2 src/gridq/value_stack/streams.py
call_path_audit check B: value_stack.streams.BaseValueStream.value_function(assumptions|context|dispatch_schedule|scenario|site_model) L228
tests/test_value_stack_value_functions.py:121 test_mic_asc_priced_traces_to_gb_v0_1   # pins the proxy £ rather than the £0 identity
```

**Verdict.** OPEN (latent). The proxy still returns a FIRM £/MW/yr for a stream D57 defines as £0 by accounting identity. No production caller reaches `value_function` today. An existing test pins the double count.

**Workstream.** WP-CORE-03. The stream registry is re-frozen there. The proxy MicAsc stream should either return £0 "carried by AGREED_CAPACITY_REDUCTION", matching the engine path, or the unwired proxy `value_function` surface should be retired.

### D-158

**Claim.** DC/DM/DR enters the floor as one lumped £/MW/yr, with no availability factor F, no performance factor K, no EFA-block shape and no product split.

**Evidence.**
```
src/gridq/value_stack/gb_streams.py:2218 def eac_response_value(*, rate_gbp_per_mw_yr, sellable_mw, assumptions, toll_reference=None):
    """Value DC/DM/DR at EFA-block granularity, netted by availability F and performance K (D-158)."""
    revenue = rate_gbp_per_mw_yr * sellable_mw * f_factor * k_factor
src/gridq/value_stack/gb_streams.py:2215 (_settlement_factor) return 1.0, [], [f"{label} NOT applied (held at 1.0 — an UPPER BOUND). {ask}"]
assumptions/gb_v1_12.yaml:3900 expected_availability_pct: value: null  status: placeholder
K: "ancillary_services.performance_factor_k does not exist in any assumption release" (gb_streams.py ask text)
production call: src/gridq/optimisation/valuation.py:1136  revenue, eac_used, eac_notes = eac_response_value(rate_gbp_per_mw_yr=priced_rate, sellable_mw=sellable, assumptions=assumptions)
$ git log -S"def eac_response_value" --oneline | tail -1  ->  769cdc4 (2026-08-15)
tests: tests/test_response_market_volume_basis.py, tests/test_market_entry_eligibility.py
"DATA ASK: no award record was supplied, so the whole year is attributed to a single unspecified DC/DM/DR product at the library's blended rate."
```

**Verdict.** PARTIAL. The structure is fixed and wired: EFA-block arithmetic, F × K slots, and one product per block with no six-way sum. The numbers are unchanged, because F is null and K is unsourced, so both are held at 1.0. There is also no product split or per-EFA-block award, so the £ is still rate × MW.

**Workstream.** WP-SIZE-09. Its deliverable is DC/DM/DR "as decision variables with per-EFA-block reservations", which is the missing product and block split. Sourcing F and K values is a WP-UW-06 data input.

### D-116

**Claim.** Local flex is priced utilisation-only. There is no availability payment and no MUPF, Performance Multiplier or POD cap. The 08-15 register filed `utilisation_performance_ratio` as never supplied (latent).

**Evidence.**
```
# availability leg NOW priced on the engine path (D54 awards on every site + D57 limb 5b awarded clear)
src/gridq/optimisation/valuation.py:771  leg, leg_used, leg_notes = local_flex_availability_value(
             assumptions=assumptions, contracted_mw=contracted_mw or None, window_hours=window_h,
             product_type=None, awarded_rate_gbp_per_mw_h=..., awarded_rate_key=...)      # no utilisation_performance_ratio
$ git log -S"leg, leg_used, leg_notes = local_flex_availability_value(" --oneline -- src/gridq/optimisation/valuation.py | tail -1
6e19c08 D57 item 5b: the local-flex availability leg is priced, and the utilisation leg follows the awarded direction (2026-08-18)
src/gridq/value_stack/gb_streams.py:2609 if utilisation_performance_ratio is None:
    "Monthly Utilisation Performance Factor NOT applied (held at 1.0 — an UPPER BOUND)."  return gross, ...
src/gridq/value_stack/gb_streams.py:3474 (proxy) local_flex_availability_value(assumptions=assumptions, contracted_mw=None, window_hours=None)
call_path_audit check B:
  value_stack.gb_streams.local_flex_availability_value(utilisation_performance_ratio) L2523 tests=True
  value_stack.gb_streams.payable_delivery_fraction(grace|multiplier|payable_over_delivery) L2492 tests=False
```

**Verdict.** PARTIAL. The availability-payment half is FIXED on the engine path at `valuation.py:771` (`6e19c08`). The MUPF clawback half is still OPEN and has moved from latent to LIVE. Every campaign site now carries an award (D54), so control reaches the ratio-None branch and the availability leg is booked gross as an upper bound. `product_type=None` and the ENA defaults on `payable_delivery_fraction` are also unchanged.

**Workstream.** WP-UW-11. It owns "DNO flex non-delivery" and performance deductions, and the MUPF clawback is exactly such a deduction.

### JSON-F-30

**Claim.** The Capacity Market can reach CONTRACTED priced at nameplate × derate when its commitment never compiled into the dispatch.

**Evidence.**
```
src/gridq/optimisation/valuation.py:395 def _capacity_market_value(schedule, assumptions, site, context=None):
    if schedule.deliverable_capacity_mw is not None: capacity_mw = schedule.deliverable_capacity_mw
    else:
        derate, warn = value_for_duration(derate_a.value, schedule.duration_h)
        capacity_mw = schedule.power_mw * derate
        "... no CM commitment compiled into the dispatch. Under D57 that deliverability is ASSUMED and the figure is priced into the floor ..."
$ grep -n "committed_power_kw" src/gridq/optimisation/valuation.py
767/769 (local flex only — no CM min(committed_power_kw) clamp)
src/gridq/optimisation/milp_solver.py:2296/2439 "intersect the metered horizon — vacuous over this dataset (no constraint ..." (skip still present, working tree)
src/gridq/optimisation/milp_solver.py:2132 deliverable_mw only from kind == "availability_window" and stream == CAPACITY_MARKET
```

**Verdict.** OPEN, and retained deliberately under D57. The fallback still prices sized power × derate, now explicitly labelled as an assumption. There is still no clamp to the awarded `committed_power_kw`, and no SoE headroom is reserved for a held-SoE-only or out-of-horizon commitment.

**Workstream.** WP-SIZE-09. CM "as a decision variable with per-EFA-block reservations" is what compiles the commitment into dispatch and makes the priced volume the reserved one.

### E-06

**Claim.** There is no Demand Flexibility Service stream.

**Evidence.**
```
src/gridq/schemas/enums.py:80  DFS = "DFS"
src/gridq/value_stack/gb_streams.py:4695 class DemandFlexibilityServiceStream(ContractedStream):
src/gridq/optimisation/valuation.py:1783  elif sid == MarketStream.DFS:
src/gridq/optimisation/valuation.py:1784      revenue, s_used, notes = _dfs_value(schedule, assumptions, site)
src/gridq/optimisation/valuation.py:1146 def _dfs_value(...): """DFS utilisation value, MEASURED by the dispatch over the called settlement periods."""
$ git log -S"def _dfs_value" --oneline | tail -1  ->  205ed92 D57 item 4: build the DFS engine branch, which did not exist (2026-08-18)
D60 (2026-08-19): zone by point-in-polygon, observed period count, battery's own deliverable volume
tests/test_dfs_zonal_basis.py:220 test_two_zones_at_one_size_earn_different_dfs_pounds  (build_value_streams -> DFS entry)
tests/test_class_c_stream_zeros.py:238 test_dfs_shows_a_sourced_value_range_rather_than_a_bare_zero
```

**Verdict.** CLOSED-FIXED. The production call site is `valuation.py:1783-1784`, reached via `build_value_streams`, and the fixing commit is `205ed92`. `tests/test_dfs_zonal_basis.py:220` asserts DFS £ through `build_value_streams` and would fail without the branch. Residue: the finding's K-factor settlement is not applied, and that is tracked under dfs_performance_factor below. The 0.1–100 MW RUC eligibility it asked for is waived by D57 limb 3.

**Workstream.** —

### MD-F-06

**Claim.** `input_hash` pins the assumption library by version string only.

**Evidence.**
```
src/gridq/quotes/assembly.py:219  "assumption_version": assumptions.metadata.version,
$ grep -rn "content_hash\|sha256" src/gridq/assumptions/*.py   -> (no output)
CLAUDE.md §5: "open gap: input_hash pins neither HH nor assumption content — F-06/F-09, WP-5"
docs/decisions.md D60 alt (b): "input_hash pins no data content (F-06)"
```

**Verdict.** OPEN.

**Workstream.** WP-CORE-03. Adding content hashes to the canonical run input re-mints every `input_hash`/`run_id`, which is a deliberate QuoteRecord contract change. WP-GRC-12's change control should consume it.

### MD-F-09

**Claim.** `input_hash` never pins the half-hourly series content.

**Evidence.**
```
src/gridq/quotes/assembly.py:66 _canonical_site_dump: dump = site.model_dump(mode="json", exclude={"created_at"})
LoadDataSummary fields: ['annual_kwh','end_date','half_hourly_basis','half_hourly_file_ref','half_hourly_reference_profile',
  'half_hourly_rows','half_hourly_scaled_to','max_export_kw','max_import_kw','missing_intervals','schema_version','start_date','validation_warnings']
   -> a path string + summary statistics; no content hash
$ grep -rn "content_hash\|sha256" src/gridq/schemas/site.py src/gridq/ingestion/assembly.py src/gridq/ingestion/hh_data.py  -> (no output)
(content hashing exists only for market forecasts: ingestion/market_forecasts.py:95 content_hash, and persisted artifacts: quotes/persistence.py:120)
```

**Verdict.** OPEN. Content changes reach the hash only indirectly, through the hashed result numbers.

**Workstream.** WP-CORE-03. This is the same canonical-input contract change as MD-F-06, so it should land in one re-mint.

### MD-F-13

**Claim.** Reprice silently skips optional sections such as penalties.

**Evidence.**
```
src/gridq/assumptions/reprice.py:154  for section in SECTION_NAMES:
src/gridq/assumptions/models.py:89    OPTIONAL_SECTION_NAMES = ("availability", "penalties", "tnuos", "bill_products")
call_path_audit check A: assumptions.reprice.assess_quote_reprice() L194 tests=True   (no production caller)
$ grep -rn "reprice" src/gridq/cli.py src/gridq/app/app.py src/gridq/quotes/pipeline.py  -> (no output)
```

**Verdict.** OPEN (latent). The section loop is unchanged. The whole reprice check has no production caller, so the skip affects nothing shipped, but the check itself is unwired.

**Workstream.** WP-GRC-12. Its "change control for pricing-path code and assumption releases" is the consumer reprice exists for. Wiring it and covering every section belong together.

### JSON-F-31

**Claim.** `gridq render` binds any site JSON to any saved quote with no identity check.

**Evidence.**
```
src/gridq/cli.py:352-362
    site = None
    if site_json is not None:
        try: site = SiteModel.model_validate_json(site_json.read_text(encoding="utf-8"))
        ...
    artifacts = generate_artifacts(quote, site)
$ grep -rn "site.site_id != quote.site_id\|site_id != \|site_id mismatch" src/gridq/outputs src/gridq/cli.py  -> (no output)
```

**Verdict.** OPEN.

**Workstream.** WP-CORE-05. The persisted-run path (`publish_run` → `persist_run` writes `site.json` with its sha256 beside `quote.json`, `provenance/claims.py:139`) already binds site to quote. Moving the shipped render onto persisted pairs closes this together with R1.

### MD-F-08

**Claim.** Inferred non-30-minute granularity is warned about and then ignored.

**Evidence.**
```
src/gridq/ingestion/hh_data.py:534  if coverage.granularity_minutes is not None and coverage.granularity_minutes != interval:
        report.add("hh.irregular_grid", Severity.WARNING,
            f"inferred grid is ~{coverage.granularity_minutes} min, not the expected {interval} min; coverage maths assume a {interval}-min grid.")
src/gridq/ingestion/hh_data.py:259  ``expected_interval_minutes`` is recorded for downstream coverage maths only
$ grep -rn "granularity_minutes" src/gridq | grep -v ingestion/hh_data.py   -> (no output)
```

**Verdict.** OPEN. The warning is unchanged. Under D45, refusing the file is no longer an allowed remedy, so the fix must honour the inferred interval by resampling, or derive and declare one.

**Workstream.** WP-SITE-03. It owns metered half-hourly load ingest for operating sites.

### MD-F-10 + H-1

**Claim.** The declared `load_data` is never reconciled with the priced file (Gate 3). The register said the module was built and tested but not wired.

**Evidence.**
```
src/gridq/quotes/pipeline.py:36   reconcile_declared_against_measured,
src/gridq/quotes/pipeline.py:367  reconciliation = None if summary is None else reconcile_declared_against_measured(site, summary)
src/gridq/quotes/pipeline.py:603  reconciliation=reconciliation,      # into assemble/assess
src/gridq/quotes/pipeline.py:641  reconciliation=reconciliation,
src/gridq/quotes/confidence.py:674-675  reconciles: bool | None = None, / reconciliation: ReconciliationResult | None = None,
call_path_audit check B: quotes.confidence.assess_confidence(reconciles) L669  -> the legacy name the 08-16 "rename trap" warns about; never supplied, harmless
$ git log -S"reconcile_declared_against_measured" --oneline -- src/gridq/quotes/pipeline.py | tail -1
4e79f3a Wire Gate 3b: the bill reconciliation was the SEVENTH never-called subsystem (2026-08-15)
tests/test_gates_record_and_continue.py:68 test_gate_3_runs_on_the_real_path_and_reports_a_reconciled_basis
tests/test_gates_record_and_continue.py:82 test_a_gate_3_breach_records_caps_the_band_and_lets_the_run_finish
H-1 (dropout screen): src/gridq/ingestion/hh_data.py:68,461 (inside summarise_half_hourly_data on the --hh path)
```

**Verdict.** CLOSED-FIXED. The production call site is `quotes/pipeline.py:367`, and the fixing commit is `4e79f3a`. `tests/test_gates_record_and_continue.py:68` exercises it on the real path. The original fix asked for "refuse on material divergence", which was replaced by record-and-continue (`validation_requirements.md` §1 and D45). That is the authoritative rule now, not a gap.
**Stale doc:** CLAUDE.md §3 still says "Gate 3 is built, tested, and called from nowhere ... Highest-value unclaimed item". This is false and should be corrected under WP-CORE-02.

**Workstream.** —

### JSON-F-25

**Claim.** `quality_flag` is recognised at ingestion and then read by nothing, so estimated meter data is priced as actual.

**Evidence.**
```
$ grep -rn "quality_flag\|estimated_reading" src/gridq
src/gridq/ingestion/hh_data.py:10:- **Optional:** ``export_kw`` / ``export_kwh``, ``site_load_kw``, ``meter_id``, ``quality_flag``.
src/gridq/ingestion/hh_data.py:61:    "quality_flag": ("quality_flag", "quality", "flag", "status"),
```

**Verdict.** OPEN.

**Workstream.** WP-SITE-03. Counting A/E rows and capping the band is part of metered half-hourly admission.

### JSON-F-28

**Claim.** There is no plausibility screen on series content, so constant or tiled data earns T2.

**Evidence.**
```
$ grep -rn -i "flatline\|zero.variance\|autocorr\|duplicate.day\|tiled\|\.std()" src/gridq/ingestion   -> (no output)
only content screen present: H-1 exact-zero dropout share (src/gridq/ingestion/hh_data.py:68-70, :461)
src/gridq/ingestion/validators.py:239 _check_connection_plausibility  (connection fields only)
```

**Verdict.** OPEN. This is Tier B as filed: the thresholds and the synthetic-data policy need a human decision.

**Workstream.** WP-SITE-03. Its metered-load ingest is where admission screens live. The flatline primitive overlaps WP-TEL-03, which covers live telemetry.

### N9

**Claim.** `half_hourly_basis` stamping. Route 1: `hh_synthesis` never stamps MODELLED. Route 2: `hh_mandate` defaults an absent basis to METERED with a HIGH ceiling. The 08-16 doc said route 2 was fixed only in the working tree.

**Evidence.**
```
$ git diff --stat HEAD -- src/gridq/ingestion/   -> (empty: nothing uncommitted)
$ git show HEAD:src/gridq/ingestion/hh_mandate.py | grep -n ...
21:**An absent basis is never read as METERED** (N9, route 2).
232:        # Undeclared: admitted and priced, but never promoted to METERED (N9, route 2).
235:            basis=None,
$ git log -S"Undeclared: admitted and priced" --oneline -- src/gridq/ingestion/hh_mandate.py | tail -1
d779eb8 Close the register: CM de-rating, D45 classes, scenario_id, levy research, Tier A status (2026-08-16)
$ git show HEAD:src/gridq/ingestion/hh_synthesis.py | grep -n "HalfHourlyBasis.MODELLED"
347:            basis=HalfHourlyBasis.MODELLED,
596:            "half_hourly_basis": HalfHourlyBasis.MODELLED.value,
$ git log -S"basis=HalfHourlyBasis.MODELLED" --oneline -- src/gridq/ingestion/hh_synthesis.py | tail -1  ->  642621b (2026-08-11)
production call: src/gridq/quotes/pipeline.py:355  admission = admit_half_hourly(site, release=assumptions.metadata.version)
tests/test_hh_mandate.py:49 test_an_undeclared_basis_is_never_read_as_metered   (asserts basis None, ceiling "medium")
tests/test_gates_record_and_continue.py:189 test_derivation_stamps_modelled_onto_the_site
```

**Verdict.** CLOSED-FIXED, both routes at HEAD. Route 2 was fixed in `d779eb8`, with call site `pipeline.py:355` and test `tests/test_hh_mandate.py:49`. Route 1 was fixed in `642621b`, with test `tests/test_gates_record_and_continue.py:189`. One caveat: the route-2 test exercises `admit_half_hourly` directly, not via `generate_quote`. Removing the call at `pipeline.py:355` would break the pipeline outright, so wiring is implied rather than separately pinned.

**Workstream.** —

### JSON-F-35

**Claim.** The observed peak can exceed MIC with no schema or validator coherence check, and the headroom fields float free.

**Evidence.**
```
src/gridq/ingestion/validators.py:239 _check_connection_plausibility: only `conn.mic_kw == 0` and `import_headroom_kw < 0` (unchanged)
$ git show HEAD:src/gridq/optimisation/engine.py | grep -n 'connection_limit_below_observed_peak'
1882:                basis="connection_limit_below_observed_peak",
src/gridq/optimisation/engine.py:1879  if peak is not None and peak > limit:  -> SizingBasis(... note="... declared import capacity min(MIC, ASC) = ... is BELOW the site's own metered peak ... FLAGGED, not trusted ... the dispatch's own charge bound still uses the declared limit, so this contradiction suppresses charging.")
src/gridq/optimisation/engine.py:932  run = replace(run, warnings=[*run.warnings, basis.note])   # reaches the record
$ git log -S"a limit is declared BELOW the observed peak" --oneline | tail -1  ->  7bfa665 (2026-08-15)
tests/test_engine_connection_basis.py (references connection_limit_below_observed_peak)
call_path_audit check B: schemas.connection.ConnectionDetails(import_headroom_kw) L26, (export_headroom_kw) L29 — no production producer supplies them
```

**Verdict.** PARTIAL. The engine now detects peak > min(MIC,ASC), sizes on the larger value and states the contradiction on the run (`engine.py:1879`, `:932`). Still open: there is no check in `validate_site_model` or the schema, the declared headroom fields are never reconciled against MIC minus peak, and the MILP still clips charge headroom to zero.

**Workstream.** WP-CORE-03. SiteModel is re-frozen there with a typed IC-1 connection reference, which is where the coherence validator and the derived headroom belong.

### JSON-F-36

**Claim.** The battery spec triple (power, energy, duration) is unconstrained.

**Evidence.**
```
src/gridq/schemas/optimisation.py:57-59 selected_power_kw / selected_energy_kwh / duration_hours: independent ge=0 optionals
src/gridq/schemas/optimisation.py:74 @model_validator _checks: only BASELINE-vs-bankable and gated_upside membership
src/gridq/schemas/sizing.py:126-127, src/gridq/schemas/dispatch_profile.py:57-58: duration_hours / energy_kwh, no model_validator relating them
```

**Verdict.** OPEN.

**Workstream.** WP-CORE-03. OptimisationResult, SizingGridPoint and DispatchProfile are frozen contracts, and a cross-field validator is a contract change.

### JSON-F-48

**Claim.** Forecast files with mixed UTC offsets raise an unwrapped ValueError and are silently dropped.

**Evidence.**
```
src/gridq/ingestion/market_forecasts.py:146  parsed = pd.to_datetime(series, errors="coerce", utc=False)   # no except ValueError / utc=True retry
src/gridq/cli.py:451  except (MarketForecastError, ValueError) as exc:
                        notes.append(f"forecast file {forecast} not pinned ... ({exc}); supply --forecast-source/--forecast-version ...")
$ .venv/bin/python -c "pd.to_datetime(pd.Series(['2025-03-30T00:30:00+00:00','2025-03-30T02:00:00+01:00']), errors='coerce', utc=False)"
RAISES ValueError Mixed timezones detected. Pass utc=True in to_datetime or tz='UTC' ... pandas 3.0.5
```

**Verdict.** OPEN, reproduced on the repo's pandas 3.0.5.

**Workstream.** WP-CORE-04. GridQ reads market data only through the IC-10 adaptor, and the manual forecast-file loader is what it retires or hardens.

### R1

**Claim.** `publish_run`, `render_publishable_pack`, `is_persisted`, `PublishedRun` and `generate_artifacts(require_persisted=)` are unreachable from any entry point. `docs/dormant_code.toml` was said to be empty.

**Evidence.**
```
call_path_audit check A:
  A outputs.consulting_report.render_publishable_pack() L2731 tests=True
  A quotes.persistence.is_persisted() L235 tests=True
  A quotes.persistence.publish_run() L279 tests=True
call_path_audit check B:
  B outputs.generate.generate_artifacts(require_persisted) L50 tests=True
coverage.allowlist_state: "no entries (no docs/dormant_code.toml)";  git log --all -- docs/dormant_code.toml -> (empty)
$ grep -rn "publish_run\|render_publishable_pack" scripts src/gridq/cli.py src/gridq/app
scripts/run_d57_campaign.py:40:from gridq.quotes.persistence import publish_run
scripts/run_d57_campaign.py:356:            published = publish_run(run)
$ git log -S"publish_run(run)" --oneline -- scripts/run_d57_campaign.py | tail -1  ->  d355076 (2026-08-19)
src/gridq/quotes/persistence.py:294 persisted = persist_run(run.quote, run.site, run.telemetry, root=root)
src/gridq/quotes/persistence.py:315 artifacts = generate_artifacts(persisted.quote, persisted.site, ..., provenance_claim=persisted.provenance_claim, run_store_root=root)
src/gridq/cli.py:362 artifacts = generate_artifacts(quote, site)        # still in-memory, no require_persisted
PublishedRun: referenced outside persistence.py only by the re-export in src/gridq/quotes/__init__.py:43,65
```

**Verdict.** PARTIAL. `publish_run` now has a real caller: the D57 campaign driver renders packs from the persisted record. The operator-driven reading suggested on 08-16 is therefore true in practice, but it was never declared. Still open:
- `gridq render` and the app render from memory without `require_persisted`.
- `render_publishable_pack` and `is_persisted` have no caller anywhere.
- `docs/dormant_code.toml` does not exist.

**Workstream.** WP-CORE-05. Persisted QuoteRecords and receipts, plus run migration, is where the shipped CLI and app render path moves onto persisted runs. The dormant symbols should be declared or deleted in the meantime.

### R2

**Claim.** `StudyBasis` is never instantiated in src and `derive_flat_export_tariff` has no callers. The D57 basis `all_streams_enabled_study_v1` was to be checked for a producer.

**Evidence.**
```
$ grep -rn "AllStreamsEnabledStudyBasis(\|StudyBasis(" src scripts docs/grundon_harwell/*.py
scripts/run_d57_campaign.py:223:    return AllStreamsEnabledStudyBasis(
scripts/run_d57_campaign.py:244:    return StudyBasis(
(no src/ hit outside schemas/study.py)
$ grep -n "study" src/gridq/cli.py  -> (no output)        # no CLI basis flag
src/gridq/quotes/pipeline.py:557 study_basis_hook = getattr(engine, "last_study_basis", None)   # consumer reads it off the engine
call_path_audit check A: optimisation.valuation.derive_flat_export_tariff() L2010 tests=True   # callers: tests/test_study_basis_schema.py only
call_path_audit check B: optimisation.engine.MilpEngine.__init__(study_basis) L671; schemas.study.StudyBasis(<all fields>) L74-112 tests=False
$ grep -rn -e "option_set()" -e "enabled_streams" -e "exclusive_families" src/gridq | grep -v schemas/study.py  -> only docstring/re-export hits
schemas/study.py:317 "Until that hook exists no figure from those five streams reaches a reported £" (option_set not read by dispatch)
```

**Verdict.** PARTIAL. A producer now exists, but only in `scripts/run_d57_campaign.py` (committed `d355076`). No shipped entry point (`gridq run`, `POST /api/run`) can select a basis. `derive_flat_export_tariff` is still uncalled; the campaign builds its tariff another way. `AllStreamsEnabledStudyBasis.option_set()` and `exclusive_families` are carried and labelled but not consumed by dispatch. Its own docstring says so; under D57/D58 the streams are instead priced through the per-stream engine branches.

**Workstream.** WP-UW-01. That WP registers a second basis "beside the academic basis" with "both bases label every artefact". Making basis selection an explicit shipped-entry-point input, and retiring or using `derive_flat_export_tariff`, belongs there.

### dfs_performance_factor

**Claim.** `dfs_performance_factor` has zero references in src.

**Evidence.**
```
call_path_audit check A:
  A value_stack.gb_streams.dfs_performance_factor() L3535 tests=True
  A value_stack.gb_streams.dfs_settlement_value_gbp() L3613 tests=True
$ grep -rn "dfs_performance_factor\|dfs_settlement_value_gbp" src | grep -v "def dfs_"
src/gridq/value_stack/gb_streams.py:3562 _DFS_ANCHOR_NAME = "dfs_performance_factor_k_piecewise"   (library key name, not a call)
src/gridq/optimisation/valuation.py:1172 docstring mention; src/gridq/optimisation/schedule.py:224 comment mention
docs/decisions.md D60 alt (e): fleet K = 0.3896 "refused ... a dispatch-measured delivery has no such mismatch in it"
```

**Verdict.** OPEN (dormant). It is now two unwired symbols. Not applying K to dispatch-measured delivery is a documented D60 choice, but the functions are neither wired nor declared dormant.

**Workstream.** WP-UW-11. "Response performance deductions" is where a DFS K-factor settlement haircut would be applied on an underwriting basis. Until then, declare both functions in `docs/dormant_code.toml`.

### eac_response_value(toll_reference=)

**Claim.** Both production callers leave `toll_reference` unsupplied, and the 08-16 doc did not determine whether that is deliberate.

**Evidence.**
```
call_path_audit check B: value_stack.gb_streams.eac_response_value(toll_reference) L2218 tests=True
src/gridq/optimisation/valuation.py:1136  eac_response_value(rate_gbp_per_mw_yr=priced_rate, sellable_mw=sellable, assumptions=assumptions)
src/gridq/value_stack/gb_streams.py:5639  eac_response_value(...)   (proxy; same omission)
gb_streams.py:2247 "``toll_reference`` is the ONLY exemption: ... the £/MW/yr shortcut is kept as-is (F and K are the counterparty's problem)"
LoadDataSummary/SiteModel: no toll / contract-reference field for DC/DM/DR exists to supply it from
```

**Verdict.** OPEN (latent, safe default). `None` selects the non-exempt path, which applies F and K. Supplying a reference could only raise revenue, so the omission is conservative. It is unsuppliable because no input carries a toll contract reference. While F and K are both held at 1.0 (see D-158) the two branches currently give the same £.

**Workstream.** WP-UW-06. Contracted rates, including a toll whose contract states where availability risk sits, are the evidence that would supply the reference as an IC-4 evidence id.

### mypy over tests/

**Claim.** `mypy` does not cover `tests/`. Enabling it flags the `GateContext`-in-`scenario` bug, at a cost of about 160 errors.

**Evidence.**
```
pyproject.toml:74-80
[tool.mypy]
python_version = "3.12"
mypy_path = "src"
packages = ["gridq"]
ignore_missing_imports = true
$ git diff --stat HEAD -- pyproject.toml -> (empty)
.github/ (UNTRACKED, another agent's WP-PLAT-01 work): .github/workflows/ci.yml:71-73  - name: mypy / run: mypy   # inherits packages=["gridq"]
```

**Verdict.** OPEN.

**Workstream.** WP-PLAT-01. Its `ci_pipeline` runs mypy on every PR, so coverage of tests/ is a CI-config decision there.
