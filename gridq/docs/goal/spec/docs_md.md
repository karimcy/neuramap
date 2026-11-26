# Verification spec — extracted from `docs/*.md` (documentation-derived invariants)

**Generated:** 2026-07-02 · **Sources:** the 18 docs named in the extraction brief + `docs/decisions.md` (D1–D26), `docs/issues/`, `docs/shared_work/OPEN_COMMENTS.md` + `BLOCKERS.md` · **Test mapping:** `grep -n "def test_" tests/test_*.py` (2026-07-02 tree).

Every row is a **normative claim** ("must", "never", "always", "exactly", "identical", "frozen") found in the docs, mapped to the module the docs assign it to, with the likely existing pytest coverage. "—(gap?)" means no obviously-matching test name was found; it may still be covered inside a broader test. This file **records** — it changes nothing.

---

## ARCH — cross-cutting architecture rules (boundary §5 · 02_architecture §1/§6 · CLAUDE.md §4)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-ARCH-01 | **No LLM / no stochastic AI on the pricing path**; LLM prose never creates or changes a number. | 00_boundary §5.1, 02_arch §1/§6.1, CLAUDE.md §2/§4 | `test_outputs_numbers_match_record.py::test_no_record_figure_leaks_into_prose` | v1 prose is deterministic/LLM-free (D14); W5 acceptance re-verified "no LLM/network on the pricing path". |
| INV-DOC-ARCH-02 | Pricing is deterministic: identical input + assumption version + code version + model version + seed ⇒ identical outputs. | 00_boundary §5.2, 02_arch §6, provenance.md | `test_determinism_golden.py` (3 tests); per-module `*_is_deterministic` tests throughout | Byte-identity same-platform; tolerance across platforms (02_arch §6.7). |
| INV-DOC-ARCH-03 | The core **never branches on `market == "GB"`** — GB is content in the assumption library, never architecture. | 00_boundary §5.5, CLAUDE.md §4, value_stack.md, 02_arch §1 | —(gap?) no automated grep/AST test found; asserted manually by wave harmony audits | Candidate mechanical check: grep core `src/gridq/` for `== "GB"` branches. |
| INV-DOC-ARCH-04 | **No private constants** — every underwriting number reads from the versioned `AssumptionSet` (labelled structural constants excepted: horizon D9, tier bounds D10, `BASELINE_POWER_FRACTION` D16, `POWER_FRACTIONS`, IRR search bounds). | 02_arch §6.4, assumption_library.md, X.1 acceptance (01_spec) | `test_finance_cashflows.py::test_capex_is_not_a_hidden_constant` / `test_opex_...` / `test_p90_haircut_...`; `test_finance_credit.py::test_library_driven_no_private_constants` | The structural-constant carve-outs are themselves documented decisions. |
| INV-DOC-ARCH-05 | **No wall-clock in the maths** — timestamps are metadata only, never feed a calculation or a hash pin. | 02_arch §6.3/§7, provenance.md, financial_model.md §7 | `test_quotes_assembly.py::test_hash_and_id_deterministic_and_created_at_invariant`; `test_sizing_sizer.py::test_sizing_eval_equals_underwrite_reprice` (timestamp-free fingerprint) | |
| INV-DOC-ARCH-06 | Re-running an identical `(input_hash, assumption_version, code_version, model_version, seed)` on the same platform yields a **byte-identical** output hash; `reproduction_env` is recorded. | 02_arch §6 (Verification), §6.8 | `test_determinism_golden.py::test_demo_quote_matches_pinned_golden_hash`, `::test_demo_serialized_outputs_are_byte_identical` | |
| INV-DOC-ARCH-07 | **Never invent missing data** anywhere: a missing input becomes a typed validation warning or a ranked data ask — never a fabricated number. | ingestion.md (the one rule), parallel plan §5, SKILLS.md, value_stack.md §5 | Pervasive: `test_app_real_mode_gating.py::test_degrades_without_inventing_numbers`, `test_dispatch_contract.py::test_charge_discharge_volume_is_unavailable_never_invented`, many others below | The single most-asserted invariant in the repo. |
| INV-DOC-ARCH-08 | The two platform↔engine coupling contracts (`SiteModel`, `OptimisationResult` + stream registry) are **frozen**; changing either is a deliberate versioned event that must trip conformance tests. | 03_contracts (Change control), 02_arch §5, operating_guide §6, D8 | `test_optimisation_contract_conformance.py` (5 tests); `test_schemas.py::test_example_files_validate` | D23/D25 additions deliberately went on `QuoteRecord` (hash-excluded), not the frozen result. |

---

## M0 — Ingestion (ingestion.md · input_data_formats.md)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-M0-01 | Ingestion **never fabricates, guesses or interpolates** a missing value; the only responses are a `ValidationIssue` or a ranked `DataAsk`. | ingestion.md (rule §) | `test_ingestion_hh_data.py::test_loader_does_not_fill_gaps`; `test_ingestion_site_json.py::test_loader_does_not_invent_hh_for_a_t0_site` | |
| INV-DOC-M0-02 | Missing HH intervals are **counted and reported, never back-filled**; a loaded frame has **exactly the input row count** (no rows added/dropped). | ingestion.md, input_data_formats §2 | `test_ingestion_hh_data.py::test_valid_hh_csv_loads_with_row_count_preserved`, `::test_missing_intervals_detected` | |
| INV-DOC-M0-03 | Duplicate timestamps are counted and reported; rows are **not** dropped. | input_data_formats §2 | `test_ingestion_hh_data.py::test_duplicate_timestamps_flagged` | |
| INV-DOC-M0-04 | Unparseable timestamps **raise** `HalfHourlyDataError` naming the offenders — never coerced/guessed. | ingestion.md §2, input_data_formats §2 | `test_ingestion_hh_data.py::test_unparseable_timestamps_fail` | |
| INV-DOC-M0-05 | Negative import/export readings are flagged (counted) unless `allow_negative=True`. | ingestion.md §2 | `test_ingestion_hh_data.py::test_negative_readings_flagged`, `::test_negative_allowed_when_explicit` | |
| INV-DOC-M0-06 | kWh→kW is a deterministic labelled unit transform: `kWh × (60/interval_minutes)` (≡ ×2 for 30-min). | input_data_formats §2 | `test_ingestion_hh_data.py::test_import_kwh_is_converted_to_kw` | |
| INV-DOC-M0-07 | Tier grading is deterministic: **T2 = ≥17,520 present intervals (±2-day tolerance) AND completeness ≥90%**; T1 = genuine HH below the T2 bar; T0 = no genuine HH. Constants are labelled structural (`data_tier.py`, D10). | ingestion.md (tiers), input_data_formats §2, 03_contracts §1 note | `test_ingestion_hh_data.py::test_tier_t0_*`, `::test_tier_t1_*` (4), `::test_tier_t2_for_full_year`, `::test_tier_classification_is_deterministic` | |
| INV-DOC-M0-08 | Forecast loader: **no generated prices** — only from a supplied file; missing prices warned, filled **only** with `allow_interpolation=True`, interior-only, labelled (`interpolated=True`), **never extrapolated**. | ingestion.md §3, input_data_formats §3 | `test_market_forecast_loader.py::test_no_interpolation_by_default`, `::test_interpolation_only_when_requested_and_labelled`, `::test_interpolation_does_not_extrapolate` | |
| INV-DOC-M0-09 | A missing `price_gbp_per_mwh` column/field raises `MarketForecastError`; **negative prices are INFO, never an error**. | input_data_formats §3/§4 | `test_market_forecast_loader.py::test_missing_price_column_fails_csv` / `_json`; negative-price INFO —(gap? likely inside load tests) | |
| INV-DOC-M0-10 | Forecast provenance is recorded: `source`, `forecast_version`, `content_hash` (canonical, **order-independent**), `file_sha256` — so a quote cites the exact forecast. | ingestion.md §3 | `test_market_forecast_loader.py::test_hash_is_deterministic_and_content_sensitive`, `::test_hash_is_order_independent`, `::test_summary_records_source_version_and_hash`, `::test_validate_flags_absent_provenance` | |
| INV-DOC-M0-11 | `load_site_model_json` fails cleanly with `SiteModelLoadError` (missing file / bad JSON with line-col / non-object / schema failure naming field paths); an **incoherent-but-schema-valid** site is rejected **distinctly**. | ingestion.md §1, input_data_formats §1 | `test_ingestion_site_json.py` (12 tests incl. `::test_schema_valid_but_incoherent_site_is_rejected`, `::test_incoherent_site_can_be_inspected_without_raising`) | |
| INV-DOC-M0-12 | Coherence: a site that exports (`mec_kw > 0`) but declares g99/g100 `not_required` is an **error**. | input_data_formats §1 (coherence) | `test_ingestion_site_json.py::test_schema_valid_but_incoherent_site_is_rejected` | |
| INV-DOC-M0-13 | Data-ask ranking is deterministic (priority, then stable id tie-break); the same site always yields the same ask order; the wholesale-forecast ask appears **only when wholesale is requested**. | ingestion.md §4 | `test_ingestion_ranked_asks.py` (4 tests); `test_ingestion_counterparty.py` (6, credit-ask precision) | |
| INV-DOC-M0-14 | `--hh` fold-in re-grades the tier; an inconsistent fold raises; observed `max_import_kw` preserved. | operating_guide §2, ingestion assembly | `test_ingestion_assembly.py` (5 tests) | |

---

## M1 — SiteModel / schema & contracts (03_data_contracts.md)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-M1-01 | Every contract inherits `GridQModel`: `extra="forbid"` — an unknown/typo field is a **validation error**, never a silent pass-through. | 03_contracts (conventions), CLAUDE.md §7 | `test_schemas.py::test_strict_extra_fields_rejected`; `test_ingestion_site_json.py::test_extra_field_is_rejected`; `test_scenario_models.py::test_scenario_forbids_unknown_fields` | |
| INV-DOC-M1-02 | **No `archetype` field** anywhere on `SiteModel` — the engine reasons from the site's own data. | 00_boundary §3.7, 03_contracts (conventions) | Covered implicitly by `extra="forbid"` tests; no named archetype test —(gap?) | |
| INV-DOC-M1-03 | T2 tier ⇒ `load_data` **must** carry HH coverage (`half_hourly_rows` + `start_date` + `end_date`). | 03_contracts §1 validators | `test_schemas.py::test_t2_without_hh_coverage_is_rejected`; `test_ingestion_site_json.py::test_t2_without_coverage_metadata_is_rejected` | |
| INV-DOC-M1-04 | T0 tier ⇒ `annual_consumption_kwh` **or** `load_data.annual_kwh` must be set. | 03_contracts §1 validators | `test_schemas.py::test_valid_t0_site_model` (positive path) | Negative path not obviously named —(gap?). |
| INV-DOC-M1-05 | A `baseline` result **cannot** be `bankable_candidate` (schema validator). | 03_contracts §3 validators | `test_schemas.py::test_baseline_result_cannot_be_bankable_candidate`, `::test_baseline_result_can_be_review_required` | |
| INV-DOC-M1-06 | `gated_upside[]` may contain **only** the three `GATED_UPSIDE_STREAMS` (`WHOLESALE_P415`, `BALANCING_MECHANISM`, `ANCILLARY_DC_DM_DR`). | 03_contracts §3 validators, §7 | Conformance suite; `test_dispatch_contract.py::test_gated_stream_in_value_streams_is_rejected` (inverse) — direct schema test not obviously named —(gap?) | |
| INV-DOC-M1-07 | A gated-upside stream can **never** have `firmness == firm` (schema validator, one of three layers). | 03_contracts §3.1, value_stack.md §3 | `test_schemas.py::test_gated_stream_cannot_be_firm`, `::test_non_gated_stream_may_be_firm` | |
| INV-DOC-M1-08 | `FinancialCase`: `p50_cashflows` and `p90_cashflows` must both be **non-empty and the same length** — a point estimate or single series is invalid. | 03_contracts §4 validator | `test_schemas.py::test_financial_case_requires_both_cashflow_series` | |
| INV-DOC-M1-09 | An **underwritten** quote from a **T0** site must carry ≥1 warning — else rejected. | 03_contracts §5 validator | `test_schemas.py::test_underwritten_from_t0_without_warning_is_rejected` / `_with_warning_is_allowed`; `test_quotes_lifecycle.py::test_t0_underwritten_without_warnings_guard_fires_on_transition` | |
| INV-DOC-M1-10 | Enums are closed `StrEnum` vocabularies; an unknown value is a validation error. | 03_contracts §7 | `test_schemas.py::test_invalid_data_tier_is_rejected` | |
| INV-DOC-M1-11 | Numerics are `ge=0` by default with exactly two documented negative exceptions: connection headroom fields and P50/P90 cashflows. | 03_contracts (conventions) | `test_schemas.py::test_invalid_negative_power_is_rejected` (partial) | Headroom-may-be-negative not obviously named —(gap?). |
| INV-DOC-M1-12 | One shared `SCHEMA_VERSION` (`"0.1.0"`); schema-version recorded on every quote; committed example JSONs must validate. | 03_contracts (conventions), M1 acceptance | `test_schemas.py::test_example_files_validate`; `test_ingestion_site_json.py::test_committed_examples_load` | |
| INV-DOC-M1-13 | Tariff `import_rates`/`export_rates`/`levies` values must all be ≥0 (negative rate/levy rejected). | 03_contracts §1.2 validator | —(gap?) no named test found | |
| INV-DOC-M1-14 | Battery-only v1: no schema carries a solar sizing input; net-load seam held open. | 01_spec M1 "Later", boundary §1 | `test_netload_seam_battery_only.py` (4 tests) | Code-side seam is ahead of docs. |

---

## X.1 — Assumption library (assumption_library.md)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-X1-01 | Every assumption entry is fully self-documenting — **all 8 fields required** (`value`,`unit`,`source`,`status`,`owner_or_reviewer`,`confidence`,`last_reviewed`,`notes`); a missing field is a validation error. | assumption_library.md | `test_assumptions.py::test_missing_required_field_fails`, `::test_loader_rejects_entry_missing_provenance_field`, `::test_invalid_status_rejected` | |
| INV-DOC-X1-02 | A release carries `metadata` + exactly the **23 sections**; a missing/mistyped section fails validation at load. | assumption_library.md, CLAUDE.md §7 | `test_assumptions.py::test_gb_v0_1_loads_with_all_sections`, `::test_gb_v1_0_loads_with_all_sections` | |
| INV-DOC-X1-03 | Released files are **frozen** — a material change is a new versioned file, never an in-place edit. | assumption_library.md, operating_guide §6, D19 | `test_assumptions.py::test_gb_v0_1_round_trip_citation_left_frozen`, `::test_gb_v1_0_entry_count_locked` | |
| INV-DOC-X1-04 | A quote is **valid only against the assumption version that produced it**; a material change beyond tolerance triggers a **reprice**, never a silent edit. | assumption_library.md, provenance.md, 02_arch §7 | `test_assumptions_reprice.py` (9 tests: tolerance sourced from library, beyond-tolerance forces reprice, structural/unit change always material) | 03_contracts still calls reprice a "process convention" — see drift. |
| INV-DOC-X1-05 | `placeholder`/`needs_review` values are usable but **never presented as confirmed or bankable**; any placeholder-built quote surfaces warnings and stays `not_bankable`. | assumption_library.md, financial_model.md §2 | `test_finance_cashflows.py::test_not_bankable_on_placeholder_even_if_result_bankable_and_covenant_passes`; `test_assumptions.py::test_gb_v1_0_placeholders_remain_flagged` | |
| INV-DOC-X1-06 | Gated-upside stream assumptions stay **placeholder zero** until the RTM partner's real numbers replace them (a version bump, not a code edit). | assumption_library.md, value_stack.md §4 | `test_assumptions.py::test_gb_v1_0_gated_streams_stay_placeholder_zero`; `test_value_stack_value_functions.py::test_gated_streams_price_zero_and_never_firm` | |
| INV-DOC-X1-07 | `gb_v1_0` confirms **nothing**: exactly 0 confirmed / 10 needs_review / 15 placeholder; the two in-repo-sourced values are promoted only to `needs_review` with citations. | D19, assumption_library.md, CLAUDE.md header | `test_assumptions.py::test_gb_v1_0_status_counts_reported_and_locked`, `::test_gb_v1_0_in_repo_sourced_values_carry_citation_and_are_promoted`, `::test_gb_v1_0_round_trip_citation_corrected` | |
| INV-DOC-X1-08 | Modules read assumptions **only** via `load_assumptions(version)` (the single door); loader rejects unknown versions cleanly. | assumption_library.md | `test_assumptions.py::test_loader_missing_file_raises_assumption_error` | |

---

## X.2 — Provenance & quote lifecycle (provenance.md · 02_architecture §6–7)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-X2-01 | `run_id` is derived **deterministically** from `(input_hash, assumption_version, code_version, schema_version)` — never from the clock. | provenance.md, CLAUDE.md §7 | `test_provenance.py::test_build_run_context_populates_and_is_deterministic` | |
| INV-DOC-X2-02 | Canonical JSON: sorted keys, compact, deterministic — equal-by-value objects serialise identically regardless of key order; `hash_json` = `sha256:<hex>` of it. | provenance.md, 02_arch §6.6 | `test_provenance.py::test_stable_json_is_deterministic_and_key_order_independent`, `::test_hash_json_*`, `::test_hash_file_is_stable` | |
| INV-DOC-X2-03 | `created_at` is metadata only — excluded from the run hash; it never feeds a calculation. | provenance.md, financial_model.md §7 | `test_quotes_assembly.py::test_hash_and_id_deterministic_and_created_at_invariant` | |
| INV-DOC-X2-04 | `get_git_commit()` never raises; `get_code_version()` = `git:<sha>` else `pkg:<version>`. | provenance.md | `test_provenance.py::test_git_and_code_version_return_strings` | |
| INV-DOC-X2-05 | Every output (memo, proposal, quote record) **must show the assumption version + input hash** — else "reproducible" is unverifiable. | provenance.md | `test_outputs_sections_and_provenance.py::test_provenance_present_in_memo_and_html`; `test_quotes_assembly.py::test_stamp_fields_populated_from_run_context` | |
| INV-DOC-X2-06 | The quote lifecycle is **append-only and ordered** (`draft → indicative → underwritten → contracted → operating`, + `lapsed`): illegal forward jumps raise; prior snapshots are never mutated; the stamp survives transitions. | 01_spec (quote as first-class immutable object), 03_contracts §5 | `test_quotes_lifecycle.py` (7 tests); `test_quote_record_contract.py::test_state_transition_is_recorded_immutably_with_stamp_preserved` | 03_contracts calls ordering a "process convention" — now enforced; see drift. |
| INV-DOC-X2-07 | `quote_id` is deterministic (`quote_run_<16hex>` from `input_hash` + versions); store writes are **idempotent** — re-submitting the same site overwrites identically. | provenance.md (Wave-5 store) | `test_cli_run.py::test_run_deterministic_quote_id`; `test_app_smoke.py::test_run_is_deterministic`; `test_determinism_golden.py::test_demo_quote_id_and_input_hash_are_stable` | |
| INV-DOC-X2-08 | Privacy: runs persist only under gitignored `$GRIDQ_RUN_STORE`; `delete_run(quote_id)` is the deletion path; **real third-party meter/credit data is never preloaded, committed or shipped** — the public demo ships only synthetic `examples/demo_site/`. | provenance.md (Wave-5), operating_guide §5, D24/D26 | `test_confidentiality.py` (7 tests: NDA state, personal-data flag, HH access log); `test_app_examples.py` (prefill gitignored-absent path) | Confidentiality/NDA machinery is ahead of the docs — see drift. |
| INV-DOC-X2-09 | The additive `QuoteRecord.sizing_grid` and `QuoteRecord.dispatch_profile` fields are **excluded from the canonical hash input** — they cannot change `input_hash`/`quote_id`, and `SCHEMA_VERSION` is not bumped. | 03_contracts §3 (D23 note), D23/D25 | `test_sizing_grid.py::test_sizing_grid_is_excluded_from_the_quote_hash`; `test_dispatch_profile.py::test_dispatch_profile_is_excluded_from_the_quote_hash` | |
| INV-DOC-X2-10 | A forecast pinned to a run changes the run's identity: a different forecast content hash changes the pin. | quotes assembly / scenario seam | `test_quotes_assembly.py::test_forecast_content_hash_changes_the_pin` | |

---

## M2 — Value & constraint stack (value_stack.md)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-M2-01 | Every value stream implements **one interface** (`ValueStream` Protocol); the default GB registry holds exactly the **nine** streams; duplicate ids rejected. | value_stack.md §1 | `test_value_stack_interface.py::test_registry_returns_expected_streams`, `::test_all_streams_satisfy_protocol`, `::test_registry_rejects_duplicate_ids` | |
| INV-DOC-M2-02 | Nothing in `interface.py`/`gates.py`/`streams.py` names a market; GB specifics live only in `gb_streams.py` + the library. A second market is a new `<market>_streams.py`, **never** a `market == "GB"` branch. | value_stack.md §5 | —(gap?) manual audit only | Same as ARCH-03. |
| INV-DOC-M2-03 | Gated upside is barred from `firm` at **three layers** (stream class, `GateResult` validator, `ValueStreamResult` validator) and can never enter the priced floor. | value_stack.md §3 | `test_value_stack_gates.py::test_gated_upside_can_never_be_firm`; `test_value_stack_interface.py::test_gate_result_gated_cannot_be_firm`, `::test_gated_base_rejects_non_gated_stream`; `test_schemas.py::test_gated_stream_cannot_be_firm` | |
| INV-DOC-M2-04 | A stream failing its gate is **priced out and listed as conditional upside with its unlock condition** (+ `missing_requirements`); a `conditional` gate result requires an unlock condition. | 00_boundary §3, M2 acceptance (01_spec), value_stack.md §2 | `test_value_stack_interface.py::test_gate_result_conditional_requires_unlock`; `test_value_stack_gates.py::test_missing_data_produces_warnings_and_requirements` | |
| INV-DOC-M2-05 | `GateStatus` ↔ `Firmness` consistency is enforced by one builder — a stream can never report an inconsistent pair (`open`⇒priceable/firm-or-contracted; `conditional`/`closed`/`unknown`⇒not priceable). | value_stack.md §2 | `test_value_stack_interface.py::test_build_gate_result_*` (3), `::test_gate_result_rejects_inconsistent_priceability` | |
| INV-DOC-M2-06 | A value function **prices only over a supplied dispatch schedule**: no schedule, or a closed/excluded gate ⇒ `annual_revenue_gbp = 0.0` + a note — revenue is never invented. | value_stack.md §5 | `test_value_stack_value_functions.py::test_no_dispatch_schedule_prices_nothing`, `::test_closed_gate_prices_nothing_even_with_dispatch`, `::test_unreadable_dispatch_schedule_prices_nothing`; `test_value_stack_interface.py::test_value_function_invents_no_revenue` | |
| INV-DOC-M2-07 | Every priced figure = an assumption-library number × a dispatched quantity, carrying a **NOT-bankable warning naming the placeholder key**. | value_stack.md §4/§5 | `test_value_stack_value_functions.py::test_tariff_arbitrage_priced_traces_to_gb_v0_1`, `::test_duos_priced_traces_to_gb_v0_1`, `::test_mic_asc_priced_traces_to_gb_v0_1` | |
| INV-DOC-M2-08 | `route_to_market_available` is `True` **only** when the gated-upside assumption is `confirmed` AND positive — no partner flag is ever fabricated. | value_stack.md §3 | `test_value_stack_gates.py::test_route_to_market_available_requires_confirmed_positive`, `::test_p415_without_rtm_partner_is_conditional` | |
| INV-DOC-M2-09 | PPA/bill-savings is **not separately priced** (it aggregates the floor; summing would double-count); MIC/ASC headroom priced only where the connection binds. | value_stack.md §4, dispatch_engine §2.2 | `test_value_stack_value_functions.py::test_ppa_floor_not_double_counted`; `test_value_stack_gates.py::test_mic_asc_headroom_*` | |
| INV-DOC-M2-10 | `check_gates` is a pure, deterministic function of SiteModel + assumptions — no randomness, no wall-clock, no invented value. | value_stack.md §3 | `test_value_stack_interface.py::test_gates_and_value_are_deterministic`; `test_value_stack_value_functions.py::test_priced_paths_are_deterministic` | |
| INV-DOC-M2-11 | `GateContext` is additive and backward-compatible: `context=None` reproduces the data-ask-only v1 behaviour **exactly**; context opens & prices CM (deliverability) and Piclo (tender). | value_stack.md §1/§7, D12 | `test_value_stack_value_functions.py::test_gate_context_additive_backward_compatible`, `::test_context_opens_cm_and_piclo_gates`, `::test_cm_contracted_and_priced_with_deliverability_context` | |
| INV-DOC-M2-12 | Scenario `price_multiplier` is read by duck-typing (default 1.0); an invalid multiplier defaults to 1.0 with a warning. | value_stack.md §1, D13 | `test_value_stack_value_functions.py::test_scenario_price_multiplier_applied_ducktyped`, `::test_invalid_scenario_multiplier_defaults_to_one` | |
| INV-DOC-M2-13 | Single-site runs carry no portfolio aggregate — the BM/ancillary portfolio-MW gate reports a missing requirement, never a guess; export consent requires MEC + approved G99/G100. | value_stack.md §3 | `test_value_stack_gates.py::test_portfolio_threshold_not_met_for_single_site`, `::test_export_consent_requires_mec_and_approved_consents` | |

---

## M3 — Dispatch engine (dispatch_engine.md)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-M3-01 | SoE dynamics with `round_trip_efficiency.ac_round_trip`: total grid charge = total discharge ÷ RTE — the exact convention finance grosses up by. | dispatch_engine §2.1, D22 | `test_optimisation_milp_solver.py::test_round_trip_loss_grosses_charge_above_discharge`, `::test_physics_invariants_on_the_solved_schedule`; `test_mpc_solver.py::test_mpc_round_trip_loss_grosses_charge_above_discharge` | |
| INV-DOC-M3-02 | Connection constraint: `charge_t ≤ min(MIC, ASC) − site_load_t` — battery charging counts against the import connection on top of site load. | dispatch_engine §2.1, 01_spec M3 acceptance | `test_optimisation_milp_solver.py::test_connection_limit_binds_with_a_tight_mic` | |
| INV-DOC-M3-03 | Behind-the-meter floor: `discharge_t ≤ site_load_t` (**no export on the priced floor** — export is gated upside); `charge_t, discharge_t ≤ power`. | dispatch_engine §2.1 | `test_optimisation_milp_solver.py::test_physics_invariants_on_the_solved_schedule`; `test_mpc_solver.py::test_mpc_behind_the_meter_discharge_bound`, `::test_mpc_schedule_has_no_simultaneous_charge_discharge` | |
| INV-DOC-M3-04 | Warranty throughput is a **HARD** annual cap (`warranty_cycle_limits.max_cycles_per_year_by_duration_h`, conservative flooring); overcycling is not available to the optimiser. | dispatch_engine §2.1 | `test_optimisation_milp_solver.py::test_warranty_throughput_cap_binds_for_a_4h_battery` | |
| INV-DOC-M3-05 | Objective = the site's **own** time-of-use bill (`import_rates` + `duos_*_rate` placed by the tariff calendar); loss energy bought at the price faced; a flat tariff yields no arbitrage (never an invented spread). | dispatch_engine §2.1/§4, D22 | `test_optimisation_milp_solver.py::test_flat_tariff_yields_no_arbitrage`, `::test_real_arbitrage_and_red_band_value_in_the_firm_floor` | |
| INV-DOC-M3-06 | Determinism: identical (inputs, assumption version, seed) ⇒ identical `DispatchResult.model_dump()` and identical `SizingRun.fingerprint()`; HiGHS single-threaded, fixed seed, IPM+crossover; all extracted quantities rounded. | dispatch_engine §3 | `test_optimisation_milp_solver.py::test_deterministic_same_problem_same_result`; `test_optimisation_engine.py::test_engine_is_deterministic`; `test_mpc_solver.py::test_mpc_deterministic_same_problem_same_result` | `xarray <2025` pin is part of the reproduction contract. |
| INV-DOC-M3-07 | Honest degrade — the solver **never raises on a valid site**: no HH series ⇒ `INSUFFICIENT_DATA` (no spec, `None` volumes; a load shape is never invented); no observed peak ⇒ `INSUFFICIENT_DATA`. | dispatch_engine §4 | `test_optimisation_milp_solver.py::test_degrade_no_half_hourly_series`, `::test_degrade_no_observed_peak`, `::test_degrade_unreadable_file`; `test_optimisation_engine.py::test_canonical_sites_degrade_honestly_without_a_metered_series` | |
| INV-DOC-M3-08 | `bankable_status` is **never** `bankable_candidate` from the dispatch layer, and is `not_bankable` whenever any used assumption is placeholder/needs_review — so on `gb_v0_1` always `not_bankable`. | dispatch_engine §4 | `test_optimisation_milp_solver.py::test_solves_to_production_optimal_not_bankable`; `test_dispatch_contract.py::test_result_is_not_bankable` | |
| INV-DOC-M3-09 | A partial metered horizon is scaled to a full year over the **present** intervals only — missing intervals excluded, never back-filled. | dispatch_engine §2.1 | —(gap?) not obviously named | |
| INV-DOC-M3-10 | Per-stream valuation partitions the objective with **no double-counting**; MIC/ASC headroom = £0 in MILP valuation (realised through connection-bounded arbitrage). | dispatch_engine §2.2 | `test_optimisation_milp_solver.py::test_real_arbitrage_and_red_band_value_in_the_firm_floor` (partial) | |
| INV-DOC-M3-11 | The placeholder `SimpleSolver` reports `annual_charge_kwh`/`annual_discharge_kwh` as `None` — **never an invented volume** — and prices nothing. | D17, dispatch_engine §5 | `test_dispatch_contract.py::test_charge_discharge_volume_is_unavailable_never_invented`, `::test_nothing_is_priced_and_streams_split_correctly` | |
| INV-DOC-M3-12 | Dispatch-energy seam (D13): the real MILP fills both annual volumes; `to_finance_dispatch_energy` + `selected_dispatch_energy()` thread them to finance so levies + price-to-target compute — with **no field added to the frozen `OptimisationResult`**. | dispatch_engine §5, D13/D22 | `test_optimisation_dispatch_energy.py` (4 tests); `test_dispatch_energy_seam.py` (3 tests) | |
| INV-DOC-M3-13 | The tariff calendar is a **time-window constant, not a price** (red band = weekday 16:00–19:00; bands partition all half-hours); unrecognised labels/flat tariffs degrade with a warning — never an invented window. | dispatch_engine §2.3 | `test_optimisation_tariff_calendar.py` (7 tests) | Calendar-as-versioned-content is deferred. |
| INV-DOC-M3-14 | Solve-time benchmark: a full metered year solves in ≈2.5 s per dispatch. | dispatch_engine §3 | `test_optimisation_engine.py::test_benchmark_full_year_dispatch_solve` | |
| INV-DOC-M3-15 | Persisted dispatch profile is **one real solved day** (highest total discharge, earliest tie) — never an average, never an invented shape; `None` on a run without metered HH and for the hook-less `BaselineEngine`. | D25 | `test_dispatch_profile.py` (9 tests) | |
| INV-DOC-M3-16 | The engine echoes the request id and stamps provenance (`solver_metadata`: seed/versions/reproduction_env); passes the conformance suite. | 03_contracts §3.2, conformance | `test_optimisation_engine.py::test_engine_echoes_request_and_stamps_provenance`, `::test_milp_engine_passes_the_conformance_suite` | |

---

## M4 — Sizing optimiser (dispatch_engine.md §6 · 01_spec M4)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-M4-01 | `SearchSpace` is validated: negatives/zero rejected; de-duplicated; sorted; energy derived = power × duration; deterministic `(power, duration)` cartesian ordering. | dispatch_engine §6 | `test_sizing_search_space.py` (10 tests) | |
| INV-DOC-M4-02 | Power candidates **must be supplied** (site-specific scale — never invented); the duration grid defaults to the structural set the library is keyed on. | dispatch_engine §6 | `test_sizing_search_space.py::test_default_duration_grid_ties_to_library_keys` | |
| INV-DOC-M4-03 | Objective: **maximise levered return s.t. P90 DSCR ≥ covenant**, with the covenant *embedded* as a feasibility gate — a candidate it rejects is **never selected**; with injected `DebtTerms` the gate genuinely fires. | dispatch_engine §6, D21, 01_spec M4 acceptance | `test_sizing_sizer.py::test_covenant_violating_spec_is_never_selected`, `::test_covenant_gate_admits_and_rejects_in_one_run`, `::test_covenant_gate_rejects_on_zero_revenue_with_debt` | Spec acceptance: "at least one grid point rejected by the constraint" — the admit-and-reject test covers it. |
| INV-DOC-M4-04 | Primary metric = equity IRR on the **P50** base case (the **P90** case governs the covenant); with no debt it falls back to labelled unlevered project IRR — never an invented number. | D21, dispatch_engine §6 | `test_sizing_sizer.py::test_objective_primary_metric_available_when_levered_present`, `::test_levered_path_is_deterministic`; `test_finance_cashflows.py::test_equity_irr_present_with_debt_absent_without` | |
| INV-DOC-M4-05 | `feasible` rejects a spec with `end_of_life_deliverable is False`; `None` (unassessed) is a **data-ask, never a fabricated pass** (D12). | dispatch_engine §6, D21 | `test_sizing_sizer.py::test_feasible_rejects_known_non_deliverable_spec` | |
| INV-DOC-M4-06 | **"One engine, called twice":** `underwrite_spec` reconstructs the dispatch+finance path independently from raw `(site, assumptions, spec)` and reprices the chosen spec to **identical numbers** through the same `_evaluate_spec` primitive; `financial_fingerprint` is timestamp-free. | dispatch_engine §6, D21, boundary §3 (engine principle) | `test_sizing_sizer.py::test_sizing_eval_equals_underwrite_reprice` | |
| INV-DOC-M4-07 | A per-candidate unpriceable result (`InsufficientInputsError`) is caught and recorded — never aborts the run; an empty search space yields a clean not-bankable run. | dispatch_engine §6 | `test_sizing_sizer.py::test_unpriceable_candidate_is_caught_not_raised`, `::test_empty_search_space_is_clean_not_bankable_run` | |
| INV-DOC-M4-08 | On `gb_v0_1` placeholders a `SizingRun` stays `bankable=False` **by construction** (unconfirmed assumptions), however good the modelled return; the gate releases only on a confirmed library. | dispatch_engine §6 | `test_sizing_sizer.py::test_placeholder_run_is_not_bankable_with_no_selection`, `::test_real_site_returns_bankable_by_construction_spec`, `::test_bankable_gate_releases_on_confirmed_lib` | |
| INV-DOC-M4-09 | When no candidate clears the covenant, the best **indicative** candidate is surfaced (highest priced-floor value; smallest system as deterministic tie-break) as `not_bankable`; the grid optimum is honestly `None`. | dispatch_engine §6, D23 | `test_optimisation_engine.py::test_engine_selects_a_real_spec_on_a_metered_hh_site` (partial); `test_sizing_grid.py::test_grid_optimum_is_none_on_placeholder` | Tie-break rule itself not obviously named —(gap?). |
| INV-DOC-M4-10 | Sizing runs are deterministic and reproducible (fingerprint timestamp-free). | dispatch_engine §3/§6 | `test_sizing_sizer.py::test_run_is_deterministic`, `::test_levered_path_is_deterministic` | |
| INV-DOC-M4-11 | `SizingGrid` (D23): one point per evaluated candidate; optimum + feasible region marked; unavailable metrics stay `None` (nothing invented); JSON round-trips; deterministic; hash-excluded; `BaselineEngine` leaves it absent. | D23, dispatch_engine §6 | `test_sizing_grid.py` (12 tests) | |

---

## M5 / M9 — Finance & counterparty credit (financial_model.md)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-M5-01 | P50/P90 are mandatory (point estimates are not deliverables); P90 = P50 × (1 − haircut) per year, **costs unchanged**; both share year-0 capex; they coincide only when revenue is zero. | financial_model §3, 03_contracts §4 | `test_finance_cashflows.py::test_financial_case_validates_and_has_p50_p90`, `::test_p90_is_a_haircut_below_p50_when_revenue_present`, `::test_p50_p90_equal_when_no_revenue` | |
| INV-DOC-M5-02 | **DSCR is measured on the P90 case** and is *unavailable* (never guessed) until real gearing exists; the schedule/DSCR/covenant maths activates with injected `DebtTerms` with **no code change**; injected terms always win over credit-derived. | financial_model §4, operating_guide §4 | `test_finance_debt.py` (15 tests); `test_finance_credit.py::test_injected_debt_terms_bypass_the_credit_path`, `::test_library_gearing_enables_dscr_but_stays_not_bankable` | |
| INV-DOC-M5-03 | A cost line that cannot be computed is **explicitly `None` + a warning — never silently zero or invented** (levies need dispatch charge volume; pricing needs delivered energy). | financial_model §1/§5 | `test_finance_cashflows.py::test_levies_unavailable_without_dispatch_volume`, `::test_levies_computed_with_dispatch_volume`, `::test_pricing_unavailable_without_delivered_energy`, `::test_cost_lines_all_sourced_from_assumptions` | |
| INV-DOC-M5-04 | The priced floor counts firmness ∈ {`firm`, `contracted`} **only**; `conditional`/`excluded`/gated streams contribute no revenue or cost to the floor (C014). | 03_contracts §3 note, value_stack §2 | `test_finance_cashflows.py::test_revenue_by_stream_sums_priced_streams_only`, `::test_conditional_streams_contribute_no_revenue_or_cost`, `::test_excluded_streams_contribute_no_revenue_or_cost`, `::test_contracted_streams_count_toward_priced_floor` | |
| INV-DOC-M5-05 | Every run on placeholder assumptions is **not bankable** and carries warnings **naming each non-confirmed assumption used**; bankable only when: confirmed assumptions AND a bankable-candidate result AND covenant passes. | financial_model §2 | `test_finance_cashflows.py::test_placeholder_assumptions_produce_warnings`, `::test_not_bankable_on_placeholder_even_if_result_bankable_and_covenant_passes`, `::test_bankable_only_when_confirmed_result_bankable_and_covenant_passes` | |
| INV-DOC-M5-06 | No private £/%/ratio constants in finance (only the structural horizon default and IRR search bounds); output scales when the library value changes. | financial_model §7 | `test_finance_cashflows.py::test_capex_is_not_a_hidden_constant`, `::test_opex_is_not_a_hidden_constant`, `::test_p90_haircut_is_not_a_hidden_constant`, `::test_capex_estimate_scales_with_power` | |
| INV-DOC-M5-07 | Identical `SiteModel + OptimisationResult + assumption_version + horizon + scenario` ⇒ identical `FinancialCase`; `case_id` derives from the input hash, not the clock. | financial_model §7 | `test_finance_cashflows.py::test_deterministic_output_on_same_inputs` | |
| INV-DOC-M5-08 | An unsized/infeasible result raises `InsufficientInputsError` — the model never invents a system; negative capex/power rejected. | financial_model §5 | `test_finance_cashflows.py::test_no_sizing_raises_insufficient_inputs`, `::test_negative_capex_is_rejected`, `::test_estimate_capex_rejects_negative_power` | |
| INV-DOC-M5-09 | Fixed £/kWh is the only computed price structure (capital-recovery → revenue-requirement → £/kWh); savings-share/capacity-fee are **labelled placeholders**; `hits_target_return` true only on confirmed assumptions. | financial_model §1/§2 | `test_finance_cashflows.py::test_fixed_price_per_kwh_math`, `::test_pricing_savings_share_is_labelled_placeholder`, `::test_pricing_capacity_fee_is_labelled_placeholder`, `::test_pricing_hits_target_only_when_confirmed` | |
| INV-DOC-M5-10 | IRR reported **only when a unique root exists** (bisection, one-sign-change gate); payback undiscounted/interpolated; `equity_irr` is `None` — never guessed — with no debt, empty schedule, non-positive equity outlay, or non-conventional series. | financial_model §1, D21 | `test_finance_returns.py` (22 tests); `test_schemas.py::test_financial_case_equity_irr_defaults_none` | |
| INV-DOC-M5-11 | Multi-year extension: revenue decays by `degradation.annual_capacity_fade_pct`; costs held flat; **no indexation applied** (no assumption exists — none invented); horizon default warns when defaulted (D9). | financial_model §1/§2 | `test_finance_cashflows.py::test_horizon_default_emits_warning` | Decay-per-year maths not obviously separately named —(gap?). |
| INV-DOC-M9-01 | Credit mapping is deterministic and **total over every band**: a given `CreditStatus` always yields the same rate/tenor from the versioned `financing.credit_rate_tenor_policy`; `unknown`/`not_assessed` ⇒ unavailable + warning — **never defaulted to a band**. | financial_model §4.1, D18 | `test_finance_credit.py::test_maps_each_band_to_library_values`, `::test_mapping_is_total_and_complete_over_every_credit_band`, `::test_mapping_is_deterministic`, `::test_unassessed_credit_is_unavailable_never_defaulted` | |
| INV-DOC-M9-02 | **Monotonicity enforced in code**: a worse band ⇒ rate non-decreasing AND tenor non-increasing; a non-monotonic policy **refuses to map** ("no glowing return at a rate the covenant doesn't justify"). | financial_model §4.1, 01_spec M9 acceptance | `test_finance_credit.py::test_monotonic_worse_credit_is_dearer_and_shorter`, `::test_non_monotonic_policy_refuses_to_map`, `::test_malformed_policy_band_skipped_never_guessed` | |
| INV-DOC-M9-03 | Gearing is out of M9 scope (Sim's): without it rate/tenor still reach `FinancialCase.debt_assumptions` while DSCR stays unavailable; `expected_tenure_years` shorter than tenor **warns, never auto-caps**. | financial_model §4.1, D18 | `test_finance_credit.py::test_no_gearing_keeps_rate_tenor_but_no_debt_terms`, `::test_expected_tenure_shorter_than_tenor_warns_without_truncating`, `::test_credit_rate_tenor_flows_to_financial_case` | |
| INV-DOC-M5-12 | Installer capex is sanity-checked against a benchmark band (inclusive boundaries); out-of-band unit cost flagged; missing band skips the check (never invents one). | 01_spec M5 (benchmark), boundary §3.1 | `test_finance_capex_benchmark.py` (7 tests) | 01_spec said this was "deferred to Later" — now built; see drift. |

---

## M6 — Scenario engine (scenario_engine.md)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-M6-01 | Weights must sum to 1.0 or are **normalised with a warning — never silently**; recorded in `WeightedSummary.weights_normalised`. | scenario_engine §1 | `test_scenario_engine.py::test_off_sum_weights_are_normalised_with_warning`; `test_scenario_models.py::test_invalid_weights_are_warninged_not_silent`, `::test_normalise_weights_*` | |
| INV-DOC-M6-02 | **No Monte Carlo, no randomness** — deterministic discrete weighted cases; Monte Carlo deferred to portfolio behind the same interface. | scenario_engine §1/§5 | `test_scenario_engine.py::test_weighted_summary_is_deterministic`; `test_scenario_seam_mc_ready.py::test_discrete_engine_satisfies_the_seam` | |
| INV-DOC-M6-03 | **Central is the base case** — identity multipliers reproduce the input cashflows exactly. | scenario_engine §1 | `test_scenario_engine.py::test_central_identity_reproduces_base` | |
| INV-DOC-M6-04 | A missing Low/High band defaults to identity **and warns** (a ranked data ask) — the engine invents no spread. | scenario_engine §1/§3 | `test_scenario_engine.py::test_build_standard_set_with_explicit_price_band_notes_missing_cost_band`, `::test_cost_only_band_is_not_mislabelled_equals_central`; `test_scenario_forecast_prices.py::test_absent_band_yields_empty_series_not_fabricated` | |
| INV-DOC-M6-05 | `policy_branch` is a **label, not a number** — no numeric effect of its own; a non-default branch is flagged "descriptive only" so a placeholder can never masquerade as calibrated precision. | scenario_engine §1 | `test_scenario_engine.py::test_no_fake_precision_from_placeholder_policy_branch` | |
| INV-DOC-M6-06 | COD delay affects **timing only**: a whole-year delay shifts operating cashflows later (capex stays year 0; revenue past horizon deferred, not created); a sub-annual delay is **not modelled** — warned, timing unchanged. | scenario_engine §1 | `test_scenario_engine.py::test_cod_delay_whole_year_shifts_timing_only`, `::test_cod_delay_partial_year_warns_and_leaves_timing_unchanged`, `::test_cod_delay_multi_year_and_beyond_horizon`; `test_outputs_cod_reported.py` (4) | |
| INV-DOC-M6-07 | `ForecastReference`: the `(source + forecast_version + content_hash)` triple is **required non-empty**; a malformed `sha256:` digest is **rejected (raises), not warned**. | scenario_engine §2 | `test_scenario_models.py::test_forecast_reference_requires_source_and_hash`, `::test_forecast_reference_rejects_malformed_hash`, `::test_forecast_reference_rejects_malformed_file_sha256` | |
| INV-DOC-M6-08 | A directly-constructed reference is `verified=False`; an **unverified `forecast_ref` forces the run not bankable** + a warning; `build_forecast_reference` cannot create a verified ref to a file that doesn't exist. | scenario_engine §2 | `test_scenario_engine.py::test_unverified_forecast_ref_forces_not_bankable_with_warning`, `::test_verified_forecast_ref_does_not_force_not_bankable`; `test_scenario_models.py::test_converter_refuses_to_invent_provenance`, `::test_build_and_verify_forecast_reference_against_real_file` | |
| INV-DOC-M6-09 | Content-hash drift is detected: `verify_forecast_reference` re-loads and flags mismatch; a drifted file's series is **not served** (empty + re-pin warning). | scenario_engine §2/§4, D20 | `test_scenario_models.py::test_verify_reports_missing_file`, `::test_verify_reports_unreadable_forecast`; `test_scenario_forecast_prices.py::test_drifted_file_is_caught_and_not_served` | |
| INV-DOC-M6-10 | Multipliers stay **explicit sourced input** — never derived from the forecast file (D20); the forecast drives the dispatch price series, not the cashflow multiplier; forecast values live in **referenced files, never in assumptions**. | scenario_engine §3/§4, D20 | `test_scenario_forecast_prices.py` (10 tests, incl. `::test_forecast_backed_scenario_reaches_dispatch_seam`) | |
| INV-DOC-M6-11 | Scenario output inherits bankability: **not bankable** while weights come from placeholder assumptions. | scenario_engine §6 | `test_scenario_engine.py::test_standard_set_run_is_not_bankable_on_placeholder_weights`; `test_scenario_forecast_prices.py::test_forecast_backed_run_stays_not_bankable_on_placeholder_weights` | |
| INV-DOC-M6-12 | The engine computes **no IRR/NPV/discounting** (finance's job); outputs are undiscounted cashflows + weighted totals labelled coarse. | scenario_engine §1 | `test_scenario_engine.py::test_run_result_carries_coarseness_note` (partial) —(gap? no negative test that discounting is absent) | |

---

## X.3 — Quote telemetry (01_spec X.3 · 03_contracts §6)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-X3-01 | **Every run produces a telemetry record from the first quote** — telemetry ships with quote 1, never retrofitted. | 01_spec X.3 acceptance, CLAUDE.md §4 | `test_quotes_telemetry.py::test_event_maps_quote_and_counts_populated_asks`; `test_telemetry_run_fields.py::test_system_quote_run_logs_every_field`; `test_cli_run.py::test_run_sized_with_hh_and_forecast` (telemetry.json written) | |
| INV-DOC-X3-02 | The manual benchmark is captured as `event_type="manual_benchmark"` (a string marker, **not** a schema field — D11); a benchmark record requires positive `human_minutes` and a valid tier. | 03_contracts §6 note, D11, manual_benchmark.md | `test_manual_benchmark.py` (12 tests, incl. `::test_convert_benchmark_to_telemetry_event`, `::test_zero_or_negative_human_minutes_fails`) | |
| INV-DOC-X3-03 | Telemetry captures: data tier at intake, human-minutes, installer iterations, outcome (win/loss + reason), versions; realised-vs-underwritten recordable (via `linked_quote_id`). | 01_spec X.3, project_status W5 check 2 | `test_telemetry_run_fields.py` (5 tests) | |
| INV-DOC-X3-04 | Analytics (time-compression vs benchmark, hit-rate by tier) name data-gated metrics **unavailable, not guessed**; empty dataset is safe. | X.3 later-items, telemetry analytics | `test_telemetry_analytics.py` (4 tests) | |

---

## M8 — Outputs (outputs.md)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-M8-01 | **Every number in both documents matches the run record exactly; no number originates in prose**; aggregations shown are sums of record values. | outputs.md rules, 01_spec M8 acceptance, D14 | `test_outputs_numbers_match_record.py::test_memo_figures_match_record`, `::test_every_cashflow_year_rendered_from_record`, `::test_floor_and_upside_totals_are_sums_of_record_values`, `::test_no_record_figure_leaks_into_prose` | |
| INV-DOC-M8-02 | Documents **regenerate byte-identically** from the same record (no wall-clock in artifacts — time comes from the record). | outputs.md rules, D14 | `test_outputs_numbers_match_record.py::test_regeneration_is_byte_identical` | `gridq render` re-runs this contract at the CLI. |
| INV-DOC-M8-03 | A missing figure degrades to an explicit `unavailable(reason)` / placeholder label — **never a 0 or a guess**; a minimal record (null result + finance) must not raise. | outputs.md, D14 | `test_outputs_generate.py::test_generates_from_minimal_example_record_without_exception`, `::test_site_optional_counterparty_section_labelled` | |
| INV-DOC-M8-04 | The HTML generator (and formatting split) does **no arithmetic** — AST scan forbids arithmetic BinOp/`sum`/`round`; value scaling lives only in `formatting`. | outputs.md rules | `test_outputs_no_arithmetic_in_html.py` (2 tests) | Extended to app templates (APP-01). |
| INV-DOC-M8-05 | `ProseDrafter` methods take **categorical inputs only** (never a £-figure); v1 default is deterministic/LLM-free. | outputs.md, D14 | `test_outputs_numbers_match_record.py::test_no_record_figure_leaks_into_prose` | |
| INV-DOC-M8-06 | A NOT-BANKABLE banner is shown on baseline/placeholder runs; **every warning is surfaced**; placeholder figures flagged. | outputs.md rules | `test_outputs_sections_and_provenance.py::test_not_bankable_banner_on_baseline`, `::test_every_warning_is_surfaced`, `::test_bankable_banner_when_result_is_bankable_candidate` | |
| INV-DOC-M8-07 | Provenance (input hash + assumption/code/schema versions + expiry) appears in **every** artifact. | outputs.md rules, provenance.md | `test_outputs_sections_and_provenance.py::test_provenance_present_in_memo_and_html` | |
| INV-DOC-M8-08 | The firm-vs-conditional (contracted-vs-merchant) split is explicit; gated/conditional upside is **never in a headline** and each gated stream carries its unlock condition. | outputs.md, D15, boundary §3.6 | `test_outputs_sections_and_provenance.py::test_firm_vs_conditional_is_clear`; `test_outputs_indicative_gated.py::test_split_drops_gated_only_for_banded_modes` | |
| INV-DOC-M8-09 | An artifact **refuses a bankable claim without the mandatory trio** (P50 + P90 + DSCR present). | 01_spec §3 (P50/P90+DSCR mandatory), W5 check 1 | `test_outputs_bankable_guard.py` (2 tests) | |
| INV-DOC-M8-10 | An **indicative** quote omits gated streams; an underwritten one may cite them (fast-banded vs slow-firm modes). | 03_contracts §5 note ("process convention"), boundary §3 (quote lifecycle) | `test_outputs_indicative_gated.py` (3 tests) | Doc still says "not a validator in v1" — now tested; see drift. |
| INV-DOC-M8-11 | Bill before/after/savings is a **labelled-pending seam with a data ask — never estimated** (no customer-bill model in v1). | outputs.md, D15, C017 | `test_outputs_artifact_consistency.py::test_savings_seam_is_consistently_pending_not_fabricated` | |
| INV-DOC-M8-12 | The three artifacts are **consistent by construction** — they cite the same size, price and value from one record. | outputs.md, boundary §1 | `test_outputs_artifact_consistency.py::test_three_artifacts_cite_consistent_size_price_and_value`; `test_app_demo.py::test_three_artifacts_render_and_match_the_record` | |
| INV-DOC-M8-13 | Without a `SiteModel` passed alongside, site-context sections render "**site context not supplied**" — never assumed (C018). | outputs.md, operating_guide §2, D14 | `test_outputs_generate.py::test_site_optional_counterparty_section_labelled` | |
| INV-DOC-M8-14 | Memo/proposal must contain their required sections; commercial structure/term/indexation/buyout are cited from the record and never fabricated. | outputs.md contents, boundary §3.3 | `test_outputs_sections_and_provenance.py::test_memo_has_all_required_sections`, `::test_proposal_has_all_required_sections`; `test_outputs_commercial_terms.py` (2); `test_outputs_proposal_term_structure.py` (2) | |
| INV-DOC-M8-15 | Scenario sensitivities render only from **persisted** record rows; deterministic; absent ⇒ a labelled pending note. | outputs.md (sensitivities slot) | `test_outputs_scenario_sensitivities.py` (6 tests) | |

---

## App — web UI (`gridq.app`, D24–D26 · wave5)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-APP-01 | The UI computes and formats **nothing** itself — all figures come from `outputs.build_view_model` + `formatting`; templates are arithmetic-free (AST-enforced, the template analogue of M8-04). | D24(6), operating_guide §3 | `test_app_templates_no_arithmetic.py` (2 tests); `test_app_charts_wiring.py` | |
| INV-DOC-APP-02 | **Two explicit run modes, no default** — Real site (primary) and Demo (opt-in); synthetic data is never substituted for a real run. | D24(2), wave5_scope §2 | `test_app_smoke.py::test_landing_boots_with_both_modes`; `test_app_real_mode_gating.py` (3) | |
| INV-DOC-APP-03 | Demo = the **real** MILP on a clearly-labelled synthetic dataset + illustrative debt; badged *illustrative* and honestly `not_bankable`; every figure engine-derived, never typed in. | D24(4) | `test_app_demo.py::test_demo_is_labelled_illustrative_and_not_bankable`, `::test_sizing_grid_is_populated_with_feasible_region_and_optimum` | |
| INV-DOC-APP-04 | Real mode degrades honestly: ranked data-asks surfaced, `insufficient_data` when inputs are missing — never an invented value. | D24(2) | `test_app_real_mode_gating.py::test_missing_inputs_surface_ranked_data_asks`, `::test_degrades_without_inventing_numbers`; `test_app_smoke.py::test_real_run_surfaces_ranked_data_asks_and_degrades_honestly` | |
| INV-DOC-APP-05 | Plotly is **vendored (pinned v2.35.2), never CDN-loaded** — no external asset reference; charts read JSON islands of raw record values + pre-formatted label strings (Plotly draws, never formats a figure). | D25(A) | `test_app_charts_wiring.py::test_plotly_is_vendored_and_no_external_cdn_is_referenced`, `::test_every_chart_div_and_island_is_present`; `test_app_view_islands.py` (6) | |
| INV-DOC-APP-06 | Identical input ⇒ identical `quote_id` and a deterministic inspect page — locally **and** over the public URL; runs persist keyed by `quote_id` (idempotent). | W5 acceptance check 3, provenance.md | `test_app_smoke.py::test_run_is_deterministic`; `test_app_charts_wiring.py::test_inspect_page_is_deterministic`; `test_determinism_golden.py` | |
| INV-DOC-APP-07 | `GET /healthz` returns 200 unauthenticated; the Basic-Auth gate is **dormant unless `GRIDQ_BASIC_AUTH` is set** (blank/malformed stays off) and exempts `/healthz` + `/static`; no credential in the repo. | D26(C) | `test_app_deploy.py` (7 tests) | |
| INV-DOC-APP-08 | A run with no metered HH series shows an **honest seam** in the dispatch chart — never an invented series. | D25(B) | `test_app_charts_wiring.py::test_run_without_dispatch_shows_the_honest_seam_not_an_invented_series`; `test_app_view_islands.py::test_dispatch_island_none_is_unavailable` | |
| INV-DOC-APP-09 | Only the synthetic demo dataset ships publicly; real-prospect prefill is gitignored and absent ⇒ `None` (no fabricated series); invalid input handled gracefully; unknown run is 404 not 500. | wave5_scope §5, project_status W5 | `test_app_examples.py` (4); `test_app_smoke.py::test_invalid_input_is_handled_gracefully`, `::test_unknown_run_is_404_not_500` | |
| INV-DOC-APP-10 | Design system: tokens defined, reduced-motion baseline, no repeated motion literals, skip-link/main/ambient wiring. | frontend redesign lane (post-W5) | `test_app_design_system.py` (6 tests) | Not described in the 18 core docs — code/tests ahead of docs. |

---

## CLI (operating_guide.md §2)

| ID | Invariant | Source | Likely existing test | Notes |
|----|-----------|--------|----------------------|-------|
| INV-DOC-CLI-01 | `gridq run` exit codes: `0` success · `2` fatal input error (site won't load / unknown assumption version). | operating_guide §2 | `test_cli_run.py::test_run_missing_site_exits_nonzero` | Unknown-assumption-version exit not obviously named —(gap?). |
| INV-DOC-CLI-02 | `gridq run` with no priced inputs degrades honestly: ranked data asks printed, quote `not_bankable`, `insufficient_data` — never invented load. | operating_guide §2 (worked examples) | `test_cli_run.py::test_run_degraded_no_inputs` | |
| INV-DOC-CLI-03 | `gridq render` regenerates memo/proposal/report from a saved `QuoteRecord` **byte-identical** to the original run; without `--site` the site-context sections render "site context not supplied". | operating_guide §2, outputs.md | `test_outputs_numbers_match_record.py::test_regeneration_is_byte_identical` (library level) — no named CLI-level render test —(gap?) | |
| INV-DOC-CLI-04 | `--hh` folds the meter CSV in and **re-grades** the tier; a file that would invalidate the site leaves the run on the original site + a warning. | operating_guide §2 | `test_cli_run.py::test_run_sized_with_hh_and_forecast`; `test_ingestion_assembly.py::test_hh_fold_regrades_to_t1`, `::test_inconsistent_fold_raises` | |
| INV-DOC-CLI-05 | `--forecast` is pinned for provenance and treated as **gated upside only — never priced into the floor**. | operating_guide §2 | `test_cli_run.py::test_run_sized_with_hh_and_forecast` (partial); floor exclusion covered by M2/M5 tests | |
| INV-DOC-CLI-06 | Same site ⇒ same `quote_id` across CLI runs; `--json` prints the `QuoteRecord`. | operating_guide §2 | `test_cli_run.py::test_run_deterministic_quote_id`, `::test_run_json_output` | |

---

# Status snapshot (docs/project_status.md, "Updated 2026-06-25")

**Headline claim:** *Wave 5 — COMPLETE; final acceptance GREEN; platform LIVE and Karim-ready (W5-5).* All six acceptance checks pass (bankable-honesty, fast/telemetry, determinism incl. over the public URL, security/privacy, honesty/no-LLM, green bar **568 tests / ruff / format / mypy(101) clean**). App deployed via systemd uvicorn :80 + cloudflared tunnel. Work left uncommitted per the parallel-lane convention.

| # | Milestone | Claimed status |
|---|-----------|----------------|
| 0 | Project setup & docs | ✅ complete |
| 1 | Schema & contracts (SiteModel, OptimisationResult, stream registry, fixtures) | ✅ complete |
| 2 | Assumption library & provenance | ✅ complete |
| 3 | Mock optimisation & financial outputs | ✅ (engine interface + baseline; M5 + M6 green; real MILP landed in Wave 4) |
| 4 | Ingestion workflow (M0) | ✅ (HH + SiteModel JSON + Modo loaders, tiering, ranked asks) |
| — | Value & gating stack (M2) + dev-quality tooling | ✅ built & green (Wave 1) |
| 5 | Quote lifecycle & telemetry (X.3) | ✅ (W2-4: stamped QuoteRecord, append-only lifecycle, TelemetryEvent from quote 1) |
| 6 | Outputs (M8) | ✅ (W3-3: pack + proposal + HTML from one record) |
| 7 | Real dispatch M3/M4 behind `OptimisationResult` | ✅ (W4-1 D22 MILP + W4-2 D21 real sizing objective; `MilpEngine` default; still `not_bankable` on `gb_v0_1`) |
| 8 | UI polish & demo flow | ✅ (W5-1 D24 surface; W5-2 D25 vendored-Plotly charts; W5-3 D26 hosting prepared; live deploy done per W5-5) |

**Wave history:** W1 complete+harmonious (281 tests) → W3 audit GO (417) → W4 complete, GO for W5 (515; equity-IRR doc drift fixed) → W5 complete (568). **Remaining core:** M7 calibration (interface/haircut placeholder only) + the deferred deliberate D13 promotion of `annual_charge_kwh`/`annual_discharge_kwh` onto `OptimisationResult`. **Every output stays `not_bankable`** on `gb_v0_1`/`gb_v1_0` (0 confirmed).

---

# Decision index (docs/decisions.md, D1–D26, newest first)

| D | One-line summary |
|---|------------------|
| D26 | W5-3 deployment: reproducible pinned wheels-only Docker build + determinism env pins, `/healthz` + dormant Basic-Auth, ephemeral-by-default run store; live go-live actions reserved for Olly. |
| D25 | W5-2 viz: vendored offline Plotly v2.35.2 supersedes D24's WebGL surface; a representative dispatch day persisted additively (`QuoteRecord.dispatch_profile`, hash-excluded; frozen contract untouched). |
| D24 | Wave-5 world-class UI: FastAPI+Jinja2 minted; two explicit run modes (real primary / demo opt-in); demo = real engine on labelled synthetic data + illustrative debt; arithmetic-free invariant extended to templates; gitignored per-run persistence. |
| D23 | Full sizing evaluation grid exposed via duck-typed `MilpEngine.last_sizing_run()` + additive hash-excluded `QuoteRecord.sizing_grid`; frozen `OptimisationResult` untouched; `SCHEMA_VERSION` not bumped. |
| D22 | Real half-hourly dispatch MILP (`gridq.optimisation`, linopy/HiGHS) is the default engine; objective = the site's own time-of-use bill; valuation in the dispatch engine; determinism via HiGHS IPM single-thread + `xarray<2025` pin. |
| D21 | Sizing turns real: equity (levered) IRR on P50 while P90 governs the covenant; bankable constraint embedded in the objective; "one engine, called twice" enforced by an independent `underwrite_spec` consistency seam. |
| D20 | Modo/wholesale forecasts reach dispatch as a hash-pinned per-scenario price series; multipliers stay explicit (never derived from the file); HH price-series field shape deferred to the real MILP. |
| D19 | `gb_v1_0` is a sourced provenance release that **confirms nothing** (0/10/15); no web-scraped forecasts; the round-trip-efficiency citation corrected; `gb_v0_1` stays frozen. |
| D18 | Deterministic M9 credit→rate/tenor mapping (`finance/credit.py`) from a versioned per-band policy table; monotonicity enforced; gearing stays Sim's; rate/tenor flow without it. |
| D17 | Dispatch solver contract (`gridq.dispatch`) + sizing skeleton (`gridq.sizing`); placeholder `SimpleSolver` non-bankable; charge/discharge on `DispatchResult`, not the frozen `OptimisationResult`. |
| D16 | Empty `optimisation_contracts/` stub removed; seam consolidated in `gridq.engine` + an importable conformance suite; `BASELINE_POWER_FRACTION` stays a labelled heuristic. |
| D15 | Customer bill before/after + savings is a labelled-pending seam in M8 — never estimated (additive `FinancialCase` fields raised as C017). |
| D14 | M8 outputs render the record and never author a number; v1 prose deterministic/LLM-free behind a categorical-only `ProseDrafter` Protocol; HTML arithmetic-free by AST scan. |
| D13 | Dispatch energy stays an injected `DispatchEnergy` hook; the `annual_charge_kwh`/`annual_discharge_kwh` promotion onto `OptimisationResult` is a deferred deliberate seam event; scenario multipliers first-class, finance keeps duck-typing. |
| D12 | Gate inputs (CM deliverability / Piclo tender / portfolio MW) are **not** SiteModel fields; data-ask-only now; a generic market-agnostic `GateContext` later. |
| D11 | Manual benchmark = `event_type="manual_benchmark"` string marker, not a schema field. |
| D10 | Tier-boundary constants (17,520 / 90% / tolerance) stay labelled structural constants in `ingestion/data_tier.py`, not the assumption library. |
| D9 | `DEFAULT_HORIZON_YEARS = 15` stays a labelled structural model parameter that warns when defaulted — not a library £-value. |
| D8 | The implemented Pydantic schemas are ratified as the frozen v1 contracts; `03_data_contracts.md` rewritten to mirror them (code wins). |
| D7 | Minimal typer CLI (`version`, `info`) in the scaffold, no pricing logic. |
| D6 | No separate `telemetry/`/`core/` packages yet — telemetry capture folds into `quotes/`, utils into `provenance/`. |
| D5 | `docs/changelog.md` is the canonical changelog; top-level `CHANGELOG.md` is a pointer. |
| D4 | Storage: files first; SQLite only if needed; Postgres later. |
| D3 | Runtime deps pinned light; solver stack (linopy/highspy) deferred until the real engine (later landed by D22). |
| D2 | `src/`-layout, setuptools; `pip install -e ".[dev]"`. |
| D1 | Python is the first implementation layer. |

---

# Open issues

**`docs/issues/` status headers (verbatim):** 14 of the 15 issue files still carry `**Status:** Not started` (001–012, 014, 015); only **013 UI shell** was updated (`v1 built (W3-7b) … polished/branded layer still M8-Later`). This directly contradicts `project_status.md`, which claims all the corresponding milestones complete — the issue-file statuses are stale, not open work (recorded under Drift candidates). Taking `project_status.md` + `CLAUDE.md` as authoritative, the genuinely open workstreams are:

- **M7 calibration** — never built beyond the labelled placeholder haircut (no issue file exists for it).
- **D13 promotion** of `annual_charge_kwh`/`annual_discharge_kwh` onto `OptimisationResult` (deliberate, deliberate versioned event).

**Open OPEN_COMMENTS (docs/shared_work/OPEN_COMMENTS.md):**

| ID | Summary |
|----|---------|
| C005 | Value stack lives in `value_stack/` not `optimisation/` as the parallel plan sketched — Olly to confirm/relocate (effectively settled by the C019 response but still marked open). |
| C007 | Data sources gating the conditional/gated streams (RTM partner, Piclo tender, CM de-rating/deliverability, ancillary forecast) — confirm sources + owners before promotion toward the floor. |
| C008 | Gearing (`financing.debt_gearing_pct`) is the one remaining piece blocking DSCR — Sim's portfolio finance; rate/tenor half resolved by D18. |
| C012 | Real Modo export schema unconfirmed (columns, units, granularity, scenario layout, provenance conveyance) — representative fixture only. |
| C017 | Customer-bill model fields (`customer_bill_before/after_gbp`, `annual_savings_gbp`) — additive, finance-owned, needed for the proposal's headline savings. |
| C018 | `QuoteRecord` carries `site_id` not the `SiteModel` — long-term shape for self-contained `gridq render` (keep passing site vs additive embedded site-context). |
| C020 | Per-value assumption `source`/`status`/`confidence` should thread into outputs so each displayed figure shows its origin. |
| C021 | Olly to confirm the dispatch-side forecast consumption seam (lazy-resolve via `problem.scenario.forecast_ref`; draft response 2026-07-01 pending self-confirm). |
| C023 | Per-figure provenance in the view model (UI-side follow-up to C020). |

**Blockers (docs/shared_work/BLOCKERS.md):**

- **B001** — M9 credit-tool ownership (PE vs Finance) unresolved; workaround built (D18 deterministic mapping; Creditsafe drops in upstream).
- **B002** — Route-to-market partner/API unknown; wholesale/BM/ancillary stay labelled gated upside, never in the priced floor.

---

# Review gates contract (docs/review_gates.md)

`gridq.dev.review_gates.check_quality_gate()` — a fast structural pre-flight (does **not** replace pytest/ruff/mypy; touches no lender-facing number). `report.passed` is `True` iff no **error**-severity check failed; a missing dev tool is a `warn` and never blocks. CLI: `python -m gridq.dev.review_gates` (non-zero exit on a blocking failure).

| Check | Severity |
|-------|----------|
| `pytest` importable | warn |
| `SKILLS.md` exists | error |
| Protected slow-update markers present + ordered | error |
| `skills/edit_log.md` exists | error |
| `skills/rejected_edits.md` exists | error |
| `skills/skill_update_protocol.md` exists | error |
| `docs/shared_work/*` all exist (TEAM_SYNC / ACTIVE_PLAN / HANDOFF_LOG / OPEN_COMMENTS / BLOCKERS) | error |
| Assumption library file(s) under `assumptions/` | error |
| Example input file(s) under `examples/` | error |
| No banned smell strings in core modules | error |

**Banned-string scan:** walks core `*.py` under `src/gridq/` (excluding `dev/`), flags case-insensitive occurrences of `"fake bankable"`, `"hallucinated revenue"`, `"todo invent"` — a blunt detector for the invented-number/spoofed-bankable failure mode. Both entry points accept a `root`/`repo_root` for testing against synthetic skeletons.

**Skill-update helpers (`gridq.dev.skills`)** — deterministic non-LLM rails: ≤3 edits per patch; edits must be mechanically applicable (missing/non-unique anchor ⇒ reject); the protected slow-update block must be byte-identical unless `slow_update=True`; candidate must keep every required heading and stay ≤12 KB unless `allow_long=True`. Accepts write `SKILLS.md` + `edit_log.md` rows; rejects write `rejected_edits.md` only.

Test coverage: `tests/test_review_gates.py` (13 tests, incl. `test_real_repo_passes_quality_gate` and per-failure-mode synthetic skeletons); `tests/test_skill_update_protocol.py` (14 tests).

---

# M4 current search space (docs + code cross-check)

- **Shape:** a validated **power × duration** grid (`gridq.sizing.SearchSpace`); energy is derived (`power × duration`), never an independent axis. Candidates de-duplicated, sorted, deterministic cartesian `(power, duration)` order.
- **Powers:** must be supplied by the caller (site-specific scale — never invented). `MilpEngine` builds a deterministic **site-scaled** grid from the observed peak using the labelled structural constant `POWER_FRACTIONS = (0.25, 0.5, 1.0)` (`src/gridq/optimisation/engine.py` — "injects no £/%", same precedent as D9/D10/D16).
- **Durations:** `DEFAULT_DURATION_HOURS = (1.0, 2.0, 4.0)` (`src/gridq/sizing/search_space.py`) — the structural set the assumption library's `warranty_cycle_limits.max_cycles_per_year_by_duration_h` is keyed on (tested: `test_default_duration_grid_ties_to_library_keys`).
- **Default grid size:** **3 powers × 3 durations = 9 candidates**, one MILP solve per candidate (≈2.5 s each for a full metered year — `dispatch_engine.md` §3).
- **Baseline (non-MILP) fallback:** `BaselineEngine.BASELINE_POWER_FRACTION = 0.1` — a labelled integration heuristic, not a library value (D16); baseline output is always `not_bankable`.
- **Search method:** enumeration-first; "smarter search only if the solve-time benchmark demands it" (dispatch_engine §6). Warm-start / parallel candidate solves explicitly deferred.
- **Grid exposure:** every evaluated candidate is retained (`SizingRun.candidates_evaluated`) and projected via `SizingRun.sizing_grid()` → `QuoteRecord.sizing_grid` (D23; hash-excluded, additive) for the 3D surface (power × duration × levered return, P90-DSCR-feasible region, optimum marked).

---

# Deferred capabilities (docs' explicit "not built / not invented" register)

| Capability | Documented state | Source |
|------------|------------------|--------|
| **Gearing / real debt** | `financing.debt_gearing_pct` absent — Sim's portfolio finance; rate/tenor flow from the D18 credit mapping, but full `DebtTerms` (and thus DSCR) cannot assemble until gearing lands. Never guessed. | financial_model §4, C008, B001 |
| **DSCR covenant verdict** | The covenant value (`dscr_covenants.min_p90_dscr`) exists, but `CovenantStatus.passes` is `None` until a DSCR exists (which needs gearing). Maths fully implemented + tested with injected `DebtTerms`; activates with **no code change**. | financial_model §4 |
| **Debt sculpting** | v1 is a level-payment (annuity) schedule that *measures* DSCR; **DSCR-sculpting** (sizing debt to a target DSCR on the P90 profile) is a named future enhancement. | financial_model §2 |
| **Parasitic load** | A `parasitic_load` library section exists, but the v1 MILP **omits** the parasitic-load term ("omission is honest, not invented"). | dispatch_engine §2.2, D22(3) |
| **Degradation marginal cost** | The degradation *marginal-cost* objective term is **omitted** (no lifetime-throughput/£-per-cycle number exists); the warranty throughput cap carries the cycling constraint; finance uses `annual_capacity_fade_pct` as a revenue-decay + replacement-reserve proxy. Real treatment is M7's ex-post SoH. | dispatch_engine §2.2, financial_model §2 |
| **MPC solver** | **Present in code, absent from the docs**: `gridq.optimisation.mpc_solver.MpcDispatchSolver` + `battery_model.py` landed opt-in (`MilpEngine(use_mpc=False)` default — default dispatch path byte-identical to the annual MILP), per HANDOFF_LOG (Olly's rolling-horizon wrapper). Benchmark before flipping the default. Tests exist (`test_mpc_solver.py` ×12, `test_battery_model.py` ×8) but no `docs/*.md` documents it. | HANDOFF_LOG (agent/dispatch-mpc); no doc coverage |
| **D13 field promotion** | `annual_charge_kwh`/`annual_discharge_kwh` onto the frozen `OptimisationResult` — deferred deliberate, deliberate versioned conformance-tested event. | D13/D17/D22, dispatch_engine §7 |
| **Tariff band→window calendar as versioned content** | Currently an indicative labelled code default; a future assumption version can override. | dispatch_engine §2.3/§7 |
| **Warm-start / parallel candidate solves; shadow-price MIC/ASC valuation** | Only if the sizing-run benchmark demands them. | dispatch_engine §7 |
| **CM final-year deliverability horizon** | Engine supplies duration-de-rated capability; multi-year end-of-life verification is a sizing/degradation output, still deferred (D12: `None` = data-ask). | dispatch_engine §7 |
| **Wholesale/Modo HH price-series field on the dispatch input** | Shape stays the real MILP's to define; lazy-resolve seam via `scenario.forecast_ref` (D20; C021 pending Olly's confirm). Feeds only gated upside — never the floor. | D20, C021 |
| **Monte Carlo** | Deferred to portfolio work behind the same `ScenarioSet → ScenarioRunResult` interface; v1 is discrete/deterministic by design. | scenario_engine §5 |
| **Customer-bill model** | No bill model in v1; proposal shows a labelled-pending seam + data ask (D15); additive `FinancialCase` fields are finance's to land (C017). | outputs.md, D15, C017 |
| **Indexation / CPI** | Not applied — no library assumption exists, so none is invented; costs held flat. | financial_model §1, D9 |
| **Per-value provenance in outputs/UI** | Run-level provenance only today; per-figure `Assumption.source/status/confidence` threading is C020/C023. | C020, C023 |
| **LLM-assisted ingestion of arbitrary installer formats; human-confirm loop** | M0 "Later" — v1 accepts structured JSON/CSV only. | 01_spec M0, ingestion.md |
| **Creditsafe integration** | No live feed; the boundary is `CreditStatus` — a future tool produces `score → CreditStatus` upstream; `Counterparty` deliberately not widened. | financial_model §4.1, B001 |
| **M7 calibration / backtesting** | Interface + labelled placeholder haircut only; "no calibration claim without backtest evidence". | 01_spec M7 |
| **EMS/M&V** | Data-contract definition only in v1; the operating system itself is out of scope. | boundary §2/Q4 |
| **Retention/TTL sweep for real submissions** | Deletion path exists (`delete_run`); **no automatic TTL yet** — named follow-up before real submissions at scale. | D26 consequences |

---

# Drift candidates (recorded only — no fixes applied)

1. **Issue-file statuses vs project_status.md** — 14/15 files in `docs/issues/` still say `Status: Not started` while `project_status.md` marks all corresponding milestones ✅ complete (only 013 was updated). Doc-vs-doc staleness.
2. **`project_status.md` / CLAUDE.md are behind the tree** — status says 568 tests (updated 2026-06-25); the tree now holds substantially more test files (e.g. `test_confidentiality.py`, `test_mpc_solver.py`, `test_battery_model.py`, `test_netload_seam_battery_only.py`, `test_supply_boundary.py`, `test_app_design_system.py`, `test_app_examples.py`, `test_assumptions_reprice.py`, `test_outputs_bankable_guard.py`, `test_outputs_indicative_gated.py`, `test_outputs_cod_reported.py`, `test_outputs_commercial_terms.py`, `test_outputs_artifact_consistency.py`, `test_outputs_proposal_term_structure.py`, `test_outputs_scenario_sensitivities.py`, `test_telemetry_run_fields.py`, `test_telemetry_analytics.py`, `test_finance_capex_benchmark.py`, `test_quote_record_contract.py`, `test_determinism_golden.py`). Post-W5 lanes (HANDOFF_LOG) are not reflected in the status table.
3. **MPC solver undocumented** — `mpc_solver.py`/`mpc_settings.py`/`battery_model.py` exist in `gridq.optimisation` with 20 tests, but no `docs/*.md` mentions MPC (only HANDOFF_LOG). `dispatch_engine.md` describes only the annual MILP.
4. **`SiteModel.supply_boundary` not in 03_data_contracts.md** — the code carries an additive `supply_boundary: SupplyBoundary` field (with tests), but the ratified contract doc's §1 field table does not list it. 02_architecture §4 *does* name "supply boundary" — so the older doc is closer than the ratified one here.
5. **NDA/confidentiality machinery vs the doc's "Not in v1"** — 03_data_contracts §1 open questions say NDA state is "Not in v1", yet `test_confidentiality.py` covers NDA state, personal-data flags and an HH access log.
6. **"Process conventions, not schema validators" is stale** — 03_data_contracts §5 says lifecycle ordering, reprice-on-assumption-change and "indicative must not cite gated streams" are unenforced conventions; all three now have enforcement + tests (`test_quotes_lifecycle.py::test_illegal_forward_jump_raises`, `test_assumptions_reprice.py`, `test_outputs_indicative_gated.py`).
7. **Capex benchmark "deferred to Later"** — 01_project_spec M5 MVP defers the installer-capex benchmark sanity-check, but `test_finance_capex_benchmark.py` (7 tests) shows it built.
8. **`operating_guide.md` describes Wave 4** — it says the web UI two-mode chooser/3D surface/hosting are "Planned for Wave 5 (not built)" while `project_status.md` says W5 is complete and live. Same doc says examples "don't price out of the box", which predates `examples/demo_site/`.
9. **`architecture.md` (short) vs 03_data_contracts on `QuoteRecord` fields** — the short doc says every quote records `output_hash`, `reproduction_env`, human-minutes and lifecycle status; 03_contracts §5 explicitly says `QuoteRecord` has **no** `output_hash`/`reproduction_env`/`human_minutes` (they live on `solver_metadata`/`TelemetryEvent`). Doc-vs-doc.
10. **01_spec §6 / 02_arch §4 `OptimisationResult` sketch vs the frozen contract** — the older sketch (`chosen_spec`, HH `dispatch` array, `value_by_stream` + `total_value`, `energy_accounting`, `meta`) was intentionally superseded by D8 (flat `selected_*`, `value_streams[]`+`firmness`, `gated_upside[]`, `solver_metadata`, `dispatch_schedule_ref`), but the two early docs still present the old shape without a superseded banner.
11. **`module_ownership.md` places M2 in `optimisation/`** — the value stack actually lives in `value_stack/` (C005 open; C019's Olly response effectively confirms the split but C005 is still marked open).
12. **CLAUDE.md §6 says the solver stack is "deferred until issue 014; see D3"** — D22 made linopy/highspy/xarray core dependencies; the same file's header says the real MILP is built. Self-inconsistent.
13. **Full-year period counts** — dispatch_engine.md benchmarks "a full metered year (17,472 half-hours)" (W4 audit likewise) while the T2 structural bar is 17,520 (48×365). Consistent only because the benchmark fixture is 364 days; easy to misread as a contradicted constant.
14. **`gb_v1_0` and D18's policy key** — D19 notes `gb_v1_0` was built from the committed `gb_v0_1` and deliberately does **not** absorb the D18 `financing.credit_rate_tenor_policy` addition; the next release must fold it in. A run on `gb_v1_0` may therefore lack the credit-policy key that `gb_v0_1` (working copy) has.
15. **Quote lifecycle vocabulary** — boundary/spec/02_arch describe `draft → indicative → underwritten → contracted → operating`; the implemented `QuoteMode` adds `lapsed` (documented in 03_contracts, absent from the earlier docs).
16. **No automated `market == "GB"` guard** — the most-repeated architecture rule (ARCH-03/M2-02) has no mechanical test/grep gate; it is asserted only by wave harmony audits.
17. **Baseline default engine wording** — CLAUDE.md says `MilpEngine` is "the default engine for `gridq run`" while D22(5) kept `BaselineEngine` as the `generate_quote` *signature* default. Both true, but easy to misquote as a contradiction.
18. **`project_status.md` W5-5 exposure notes vs deployment docs** — status references the live systemd/cloudflared deploy (Hetzner) while D26/`deployment.md` document Fly.io as the worked primary host with go-live reserved for Olly; the actual deploy path diverged from the runbook's primary.
