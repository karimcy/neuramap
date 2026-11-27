# Contracts changelog

Newest first. One entry per contract version. The git tag is created when the version is committed.
Eleven `v1.0.0` tags exist for the contracts below: IC-1, IC-2, IC-4, IC-11 and IC-13, then IC-12
(WP-CORE-03), IC-15 (WP-GRC-09), IC-3 (WP-SIZE-07), IC-5 (WP-EDGE-01), IC-9 (WP-SITE-07) and IC-14
(WP-TEL-01). The four `v1.1.0` tags below are annotated and sit on one commit, the one that
completes WP-BO-06 on branch `roadmap/wp-bo06`. The IC-4 `v1.2.0` and IC-15 `v1.0.0` tags are
annotated and sit on the commit that completes WP-GRC-09 on branch `roadmap/wp-grc09-templates`. The
IC-3 `v1.0.0` tag is annotated and sits on the commit that completes WP-SIZE-07 on branch
`roadmap/wp-size07-ic3`. The IC-5 and IC-9 `v1.0.0` tags are annotated and sit on the commits that
complete WP-EDGE-01 (`roadmap/wp-edge01-ic5`) and WP-SITE-07 (`roadmap/wp-site07-ic9`), not on the
merges. The IC-12 `v3.0.0` tag is annotated and sits on the integration commit on
`roadmap/p1-foundations` that renders 3.0.0 over 2.1.0, and the IC-12 `v3.1.0` tag on the
integration merge of WP-UW-17, and the IC-12 `v3.2.0` tag on the integration merge of WP-SIZE-02.
The IC-14 `v1.0.0` tag is annotated and sits on the commit that completes WP-TEL-01 on branch
`roadmap/wp-tel01-ic14`, not on the merge. With IC-12's `v2.0.0`, `v2.1.0`, `v3.0.0`, `v3.1.0` and
`v3.2.0`, twenty-one contract tags exist in all.

## 2026-11-26: IC-12 chassis 3.2.0, an additive minor of 3.1.0 (WP-SIZE-02, Olly; D178; branch `roadmap/wp-size02-library`)

IC-1, IC-2, IC-3, IC-4, IC-5, IC-9, IC-11, IC-13, IC-14 and IC-15 are unchanged; no schema file of
theirs changed.

- **IC-12 3.2.0.** Tag `contracts/ic12-chassis/v3.2.0`, annotated, on the integration merge
  of WP-SIZE-02. Release name unchanged, `gridq_chassis_v3`.
  - **Why:** WP-SIZE-02 extracts the dispatch constraint set into the versioned package
    `gridq.constraint_library`, and its acceptance requires the library version on every
    `OptimisationResult`. Adding that member moved the result schema and, through the result it
    embeds, the quote record's; this release versions the change instead of editing the 3.1.0
    files.
  - **Archived:** the three `v3.1.0` files, byte for byte, pinned by SHA-256 in
    `__main__.ARCHIVED` at their hashes under the tag `contracts/ic12-chassis/v3.1.0`. No archived
    file was edited.
  - **`SiteModel`:** unchanged; its `v3.2.0` file equals the `v3.1.0` file byte for byte.
  - **`OptimisationResult`, added, optional, defaulting to `null`:** `constraint_library`
    (`ConstraintLibraryRef`: `library_id`, `library_version` (semver), `library_hash`
    (`sha256:` + 64 hex), `constraint_ids` (sorted, unique, each `^[a-z][a-z0-9_]*$`)), one new
    closed definition. Its members restate IC-3's `ConstraintLibraryPin`, so an envelope exporter
    copies the stamp into `source.constraint_library` without translation.
  - **`QuoteRecord`:** grows only through the embedded `OptimisationResult` (the same member and
    definition). Nothing else moved.
  - **Compatibility:** every 3.1.0 instance is a valid 3.2.0 instance with the same bytes: the
    member is dropped from every dump while unset (the 2.0.0 growth rule, `omit_unset`). Set, it
    is excluded from `input_hash` (`quotes.assembly._canonical_result_dump`): the library is
    code, pinned by `code_version` through `run_id`, so no identity hash moves. A 3.1.0 reader
    refuses a 3.2.0 result that carries the stamp, which is correct: it could not check it.
  - **Pinned by:** `tests/contracts/test_ic12_chassis.py::test_3_2_0_is_3_1_0_plus_the_stated_optional_members`,
    `::test_the_3_2_0_stamp_is_one_closed_definition_mirroring_the_ic3_pin`,
    `::test_a_3_1_0_reader_refuses_a_payload_that_sets_the_3_2_0_stamp`;
    `tests/contracts/test_schema_archive.py::test_the_archive_pins_every_tagged_file`.

## 2026-11-26: IC-14 telemetry series 1.0.0 (WP-TEL-01, Olly)

New contract; no other contract changed and no existing schema file changed.

- **IC-14 1.0.0** (roadmap NEW-IC-14). `SeriesDescriptor`, `TelemetryBatch`, `CuratedSeries` and
  `DqAnnotation` (`ic14_series_descriptor`, `ic14_telemetry_batch`, `ic14_curated_series`,
  `ic14_dq_annotation`, each `.v1.0.0.schema.json`), in `telemetry_series.py`. Tag
  `contracts/ic14-telemetry-series/v1.0.0` (annotated, on the commit that carries the four schemas
  and the specification).
  - **Shape** (D148 to D153): point naming (`PointName`, `point_key`, `series_id`), one
    integer unit per quantity (`QUANTITY_TABLE`), fixed UTC blocks of integer slots, a
    `ClockStatement` per block, `ValueQuality` and `GapReason` mapped to OPC UA status codes, raw
    and curated tiers with a data-quality overlay, and a `TenantKey` on every record.
  - **A missing sample is a gap, never a zero:** run-length `ValueRun`/`GapRun` tiling in raw;
    `Aggregate` only with enough usable time, else `CuratedGap`, in curated.
  - **Readers:** `parse_point_key`, `counterparts`, `split_import_export_w`, the unit converters,
    `slot_instant`, `batch_end`, `settlement_period_of`, `clock_drift_ms`, `to_opcua`,
    `from_opcua`, `check_batch`, `check_batch_size`, `verify_batch_chain`, `track`, `resample`,
    `curate`, `effective_annotations`, `verify_curated`, `check_supersession`, `current_revision`,
    `require_certified`, `resource_ref`, `read_guard`, `age_at` and `values_in`.
  - **Reads** IC-13 (claims, `authorise`, the audit genesis hash) and IC-4 (`EvidenceRef`). It does
    not read IC-5: IC-5 1.0.0 is unchanged, and carrying a batch in an IC-5 frame is an IC-5 minor.
  - **Generated specification** `specs/ic14_telemetry_series.v1.0.0.md` (`SPECS` now has two
    entries; the IC-9 spec tests read their own entry instead of assuming one).
  - **Tests:** `tests/contracts/test_ic14_telemetry_series.py` (fixtures in `fixtures/ic14/`,
    seven golden hashes in `GOLDEN_HASHES_IC14`, property tests P1 to P14, 51 mutants). The IC-4
    pin inventory lists `ic14_series_descriptor` as an embedder of `EvidenceRef`.

## 2026-11-26: IC-12 chassis 3.1.0, an additive minor of 3.0.0 (WP-UW-17, Olly; D128 to D132; integration on `roadmap/p1-foundations`)

IC-1, IC-2, IC-3, IC-4, IC-5, IC-9, IC-11, IC-13 and IC-15 are unchanged; no schema file of theirs
changed.

- **IC-12 3.1.0.** Tag `contracts/ic12-chassis/v3.1.0`, annotated, on the integration merge of
  WP-UW-17. Release name unchanged, `gridq_chassis_v3`. WP-UW-17 released these members as 2.2.0
  on a branch cut before 3.0.0 (WP-UW-07) was integrated; on this line they are 3.1.0, and no
  `v2.2.0` file or tag exists.
  - **Why:** WP-UW-17 (finance completeness) added optional members to `FinancialCase` and to
    `PriceRecommendation`. `QuoteRecord` embeds both, so `--check` reported it drifting from
    `ic12_quote_record.v3.0.0.schema.json`. This release versions that change instead of editing
    the 3.0.0 file.
  - **Archived:** the three `v3.0.0` files, byte for byte, pinned by SHA-256 in `__main__.ARCHIVED`
    at their hashes under the tag `contracts/ic12-chassis/v3.0.0`. No archived file was edited.
  - **`SiteModel` and `OptimisationResult`:** unchanged; their `v3.1.0` files equal the `v3.0.0`
    files byte for byte.
  - **`QuoteRecord`, added, all optional, all defaulting to `null`:**
    `FinancialCase.lifecycle_inputs` (typed user inputs: construction programme, COD delay, CM
    agreement indexation, terminal value, decommissioning, replacements) and
    `FinancialCase.lifecycle` (the derived record: line bases, indexation, construction and IDC,
    dated flows, the lifecycle-flow ledger); `PriceRecommendation.levelisation_basis`,
    `.measured_irr_with_price_pct`, `.equity_closed_loop_gbp_per_kwh`. Twenty-four new
    definitions (listed in `DELTA_V310`). No description was reworded; no existing member, type,
    `required` list or definition changed, and nothing was removed.
  - **Compatibility:** every 3.0.0 instance is a valid 3.1.0 instance. `lifecycle_inputs` is
    dropped from the canonical dump while unset, and the derived members are excluded from the
    case identity, so no identity hash moves for a run without lifecycle inputs. A 3.0.0 reader
    refuses a 3.1.0 record that carries a financial case, as for every minor here.
  - **Conformance:** `tests/contracts/test_ic12_chassis.py::test_3_1_0_is_3_0_0_plus_the_stated_optional_members`
    pins the delta exactly; `::test_a_3_0_0_reader_refuses_a_payload_that_sets_a_3_1_0_member`
    states the down-level behaviour.
  - **IC-3:** IC-3 1.0.0 still binds IC-12 `2.x` only; a 3.x quote needs an IC-3 minor before
    WP-SIZE-08 exports from it (unchanged by this release).

## 2026-11-26: IC-12 chassis 3.0.0, release `gridq_chassis_v3`, rendered on 2.1.0 (WP-UW-07, Olly; integration on `roadmap/p1-foundations`)

IC-1, IC-2, IC-3, IC-4, IC-11, IC-13 and IC-15 are unchanged. WP-UW-07 (D103 to D106) was built
on IC-12 2.0.0; at its integration merge 3.0.0 was rendered on 2.1.0 (WP-UW-03, D79), so it carries
every 2.1.0 member. The three IC-12 `v2.1.0` files are archived and pinned by SHA-256
(`__main__.ARCHIVED`) at their hashes under `contracts/ic12-chassis/v2.1.0`; the three `v2.0.0`
files stay archived at theirs. No archived file was edited.

- **Required, so a major:** `TariffStack.basis` (`TariffBasis`: `vat` included/excluded with
  `vat_rate_fraction` when included, `vat_attestation` stated_by_record/derived, `levies` and
  `network_charges` itemised/in_unit_rates/not_charged) and `TariffStack.provenance`
  (`TariffProvenance`: `commodity`, `network`, `levies`, each own_invoice / published_for_site /
  by_analogy / assumed / not_charged). D103 and D104. A derived VAT basis must carry its
  `DerivedAssumption` on `tariff_stack.basis.vat` (a `SiteModel` rule, not a schema member).
- **Added, optional, absent from a dump while unset:** `TariffStack.network_tariff` (a published
  `CdcmTariff` or `EdcmTariff` row), `.import_rate_windows` (`BandWindows` per label) and
  `.evidence`; `CustomerBill.reactive_before_gbp` and `.reactive_after_gbp` (in `QuoteRecord`).
  New definitions in the site schema: `BandWindows`, `CdcmTariff`, `EdcmTariff`, `TariffBasis`,
  `TariffProvenance`.
- **Carried from 2.1.0:** every `QuoteRecord` member 2.1.0 added (`FinancialCase.lender_terms`, the
  DSCR status, covenant-tier and debt-sizing members). `test_3_0_0_is_2_1_0_plus_the_stated_members`
  proves 3.0.0 is 2.1.0 plus exactly the members above.
- **Unchanged:** `OptimisationResult`; its 3.0.0 file is its 2.1.0 (and 2.0.0) file byte for byte.
- **Compatibility:** `SUPPORTED_VERSIONS` is `("1.0.0", "2.0.0", "2.1.0", "3.0.0")`. A 2.x site
  payload (2.0.0 and 2.1.0 share one site schema, byte for byte) is refused, never read with a
  guessed basis. `chassis.upgrade_site_2_to_3(payload, basis=..., provenance=..., vat_record=...)`
  lifts either with the caller's statement. Every committed site, with exactly its basis,
  provenance and VAT record stripped, still hashes to its 2.0.0 pin; its 3.0.0 bytes are pinned
  beside it, and the upgrader lifts the stripped payload back to them
  (`tests/contracts/test_ic12_chassis.py`). The five synthetic identity sites (WP-GRC-05) gained the
  synthetic test basis (`tests/_tariff_support.py`) and their 3.0.0 pins were minted at the merge;
  no 2.0.0 pin moved. The campaign records' 3.0.0 pins are WP-UW-07's, checked through the DPA
  guard only. Every 2.1.0 `QuoteRecord` and `OptimisationResult` payload is a valid 3.0.0 one with
  the same bytes. A 2.x reader refuses a 3.0.0 site.
- **Dependants.** IC-15 names no IC-12 version. IC-3 1.0.0 binds an assessment quote at any IC-12
  `2.x` (`QuoteBinding.contract_version`), so it cannot bind a quote written at 3.0.0 until an IC-3
  minor widens that range. No producer writes an envelope yet (WP-SIZE-08); IC-3 was not re-versioned
  here.
- **Tag:** `contracts/ic12-chassis/v3.0.0`, annotated, on the integration commit that renders 3.0.0
  over 2.1.0 on `roadmap/p1-foundations`.
- The instance stamp `schema_version` ("0.1.0") is not the contract version and did not move.

## 2026-11-26: IC-9 compute turn-down 1.0.0 (WP-SITE-07, Olly)

New contract; no other contract changed and no existing schema file changed.

- **IC-9 1.0.0** (spec §10A IC-9, B8-01). `ComputeTurndownDeclaration`
  (`ic9_compute_turndown_declaration.v1.0.0.schema.json`) and its compiled timeline
  `ResolvedTimeline` (`ic9_resolved_timeline.v1.0.0.schema.json`), in `compute_turndown.py`. Tag
  `contracts/ic9-compute-turndown/v1.0.0` (annotated, on the commit that carries both schemas and
  the specification).
  - **Shape** (D122 to D127): a tiered menu of envelopes (duration curves, absolute kW or
    basis points of running IT load with a kW cap, exclusion groups) plus a GB-clock availability
    template with dated overrides; SLA classes that own the call budget and say whether energy
    returns; a structured `PriceOffer`; stacking (merit order, concurrent cap) and a retained IT
    floor; the stored `resolved_hash` of the compiled UTC timeline.
  - **Readers:** `resolve`, `resolved_hash_of`, `timeline_matches`, `offered_at`,
    `timeline_state_at`, `declared_at_local` (`fold=0`), `turn_down_kw`, `deliverable_kw`,
    `deliverable_against`, `stack_at`, `call_budget_used`, `check_call`, the price functions,
    `declaration_ref`, `active_at` and `resolve_sla_evidence`.
  - **Policy in the contract:** no firmness field, no gate and no stream ranking (D57, B8-03);
    the only cross-offer rules are no-double-booking arithmetic. No instruction or
    acknowledgement message (ADR-D2).
  - **Generated specification.** `specs/ic9_compute_turndown_declaration.v1.0.0.md`, rendered by
    the new `_specdoc.py` from the module and its committed schemas. `--check` now also fails
    when a committed specification is not a fresh render, and `--write` regenerates it.
  - **Tests:** `tests/contracts/test_ic9_compute_turndown.py` (fixtures in `fixtures/ic9/`, six
    golden hashes in `GOLDEN_HASHES_IC9`, 42 mutants). The IC-4 pin inventory lists IC-9 as an
    embedder of `EvidenceRef`.

## 2026-11-26: IC-5 edge ↔ cloud protocol 1.0.0 (WP-EDGE-01, Olly)

IC-1, IC-2, IC-3, IC-4, IC-11, IC-12 and IC-13 are unchanged; no schema file of theirs changed, no archived
schema was edited and no golden hash was re-pinned.

- **IC-5 edge ↔ cloud protocol 1.0.0** (roadmap WP-EDGE-01, spec §10A, B1/B2, decisions
  D114 to D121). Module `gridq.contracts.edge_cloud`.
  - **Schemas:** `ic5_cloud_to_edge_frame.v1.0.0.schema.json`, `ic5_edge_to_cloud_frame.v1.0.0.schema.json`
    and `ic5_link_profile.v1.0.0.schema.json`, all listed in `__main__.FROZEN`.
  - **Tag:** `contracts/ic5-edge-cloud/v1.0.0`, annotated, on the commit that completes WP-EDGE-01 on branch
    `roadmap/wp-edge01-ic5`.
  - **Frames:** one model per direction, each a closed body union on `kind`; header `site_id`, `sender_id`,
    `sent_at`, `link_epoch`, `seq`, `prev_hash` (IC-13 genesis at seq 0), `profile_hash` and `peer_head`.
  - **Cloud to edge bodies:** `cloud_heartbeat`, `setpoint_schedule`, `envelope_install` (an IC-3 envelope
    and its compiled timeline), `operator_command` (`enter_fail_safe`, `release_trigger`).
  - **Edge to cloud bodies:** `edge_heartbeat`, `schedule_ack`, `fail_safe_event`, `anm_event`.
  - **`LinkProfile`:** every threshold, the latency budgets, the clock skew and the frame size, all required
    with no default; IC-1 by `ConnectionAgreementRef`; `anm_authority: edge_local`.
  - **Units:** integers only; durations in milliseconds (`_ms`), power in kW, energy in kWh, temperatures in
    tenths of a degree (IC-3's).
  - **Embeds by the `1.x` range:** IC-1 `ConnectionAgreementRef`, IC-3 `EnvelopeRef` and (in
    `envelope_install`) the whole IC-3 `DispatchEnvelope`, so `ic5_cloud_to_edge_frame` embeds IC-4's
    `EvidenceRef` and `IssuerRole`.
  - **Pure functions in the leaf:** `ChainTracker`, `verify_frame_chain`, `check_frame_size`,
    `check_setpoint_schedule`, `to_scheduled_periods`, `admit_schedule`, `verify_ack_chain`,
    `governing_timeline`, `link_state`, `edge_silent`, `primary_trigger`, `fail_safe_target`,
    `verify_episode`, `effective_import_cap`, `anm_state_after`, `anm_triggers`, `verify_anm_response`,
    `deliver_by`, `latency_ms`, `nearest_rank`, `check_latency`.
  - **Fixtures:** `fixtures/ic5/`, three synthetic files; hashes in `GOLDEN_HASHES_IC5`.
  - **Mutation checks:** twenty guards in `test_ic5_edge_cloud.py`.

## 2026-11-26: IC-15 template set 0.2.0 binds IC-3 1.0.0 (integration on `roadmap/p1-foundations`, Olly)

No contract version changed and no schema file changed: `--check` is clean with IC-15 still at 1.0.0.
No tag was created or moved. This is manifest data and template text only.

- **The `ic3_dispatch_envelope` reference** goes from `not_yet_frozen` (WP-SIZE-07) to `frozen`,
  minimum `1.0.0`, tag `contracts/ic3-dispatch-envelope/v1.0.0`. `live_contract_versions()` now
  lists IC-3 at 1.0.0, so `reference_findings` checks the reference against the live module. IC-8
  stays `not_yet_frozen` (WP-LEDGER-07).
- **Texts.** The six lines that cite IC-3, in five templates (the Assessment SOW, the MSA twice, the
  flexibility services agreement, the reliance letter and the wrapper), drop "(binding unavailable
  until WP-SIZE-07)". Those five go to template version 0.2.0, stated in each text, and the set to
  `set_version` 0.2.0. Two field asks drop "once that contract is frozen". The new text hashes are
  pinned in the manifest. Every template is still a draft that needs legal review.
- **Fixtures.** The reliance letter's executed terms bind the IC-3 `ic3_envelope_hybrid_bess`
  fixture by content hash. That envelope binds the same IC-1 and IC-2 fixtures the letter does. The
  MSA's executed terms take the new template version and text hash. Their hashes, and the manifest's,
  are in a new table, `GOLDEN_SET_0_2_0`. The set 0.1.0 table, `GOLDEN`, is not edited: a test
  checks it against the files at the IC-15 1.0.0 tag.
- **Why no schema bump.** `Frozen` already admits any `contracts/ic<n>-<name>/v1.x.y` tag, and a
  contract-instance value already admits IC-3. Nothing in either IC-15 schema changed.

## 2026-11-26: IC-3 dispatch envelope 1.0.0 (WP-SIZE-07, Olly)

IC-1, IC-2, IC-4, IC-11, IC-12 and IC-13 are unchanged; no schema file of theirs changed, no archived
schema was edited and no golden hash was re-pinned.

- **IC-3 `DispatchEnvelope` 1.0.0** (roadmap WP-SIZE-07, spec §10A, decisions D85-D89).
  Module `gridq.contracts.dispatch_envelope`.
  - **Schemas:** `ic3_dispatch_envelope.v1.0.0.schema.json` and `ic3_resolved_timeline.v1.0.0.schema.json`,
    both listed in `__main__.FROZEN`.
  - **Tag:** `contracts/ic3-dispatch-envelope/v1.0.0`, annotated, on the commit that completes WP-SIZE-07
    on branch `roadmap/wp-size07-ic3`.
  - **Time base:**
    - a recurring template on `Europe/London` (seasons, ISO day groups, 30-minute bands, cells);
    - dated UTC overrides;
    - a half-open validity and `supersedes`;
    - `resolved_hash`, the content hash of the compiled `ResolvedTimeline`, recomputed on validation.
  - **Per cell (`CellTerms`):**
    - the discharge and charge `PowerPartition`: resilience, service slices, merchant and withheld,
      summing exactly to the rating;
    - the `EnergyStack`: floor, held, merchant, headroom and withheld, summing exactly to the usable energy;
    - the import and export limits, with the import basis;
    - `ThermalTerms` when the envelope names an IC-11 surface.
  - **Envelope-wide:**
    - `services`: stream, sustain, counterparty role, reference, the evidenced, assumed-approved or
      unknown basis, and the underwritten value;
    - `fail_safe`;
    - `thermal`: an IC-11 `AvailabilityRef` plus the binding band, not applicable, or unknown;
    - `source`: a required `input_hash`, the run pins with a re-derived `run_id`, the IC-12 quote binding
      (`2.x` range), the study or underwriting basis label, and the IC-1, IC-2 and constraint-library
      references.
  - **Units:** integers only. kW, kWh of absolute state of energy, minutes, tenths of a degree. The
    producer's safe-side rounding obligation is stated in the module docstring.
  - **Embeds by the `1.x` range:**
    - IC-1 and IC-2 `ConnectionAgreementRef` and `ScenarioSetRef`;
    - IC-4 `EvidenceRef` and `IssuerRole`;
    - IC-11 `AvailabilityRef` and `TemperatureBand`.
    IC-3 is now in the IC-4 embedder set (`test_ic4_pin_and_inventory.py`).
  - **Consumer functions, in the leaf:**
    - `resolve`, `terms_at` and `timeline_matches`;
    - the views `flat_limits`, `merchant_headroom`, `reserved_kw`, `offered_kw`, `service_windows` and
      `underwritten_total`;
    - the checks `check_schedule`, `check_against_surface`, `check_against_agreement`, `check_source`
      and `check_constraint_library`;
    - `active_at` and `envelope_ref`.
    A consumer needs only `gridq.contracts` and pydantic.
  - **Fixtures:** five synthetic files in `tests/contracts/fixtures/ic3/`, built by
    `tests/contracts/_ic3_fixtures.py`. Their hashes are in the new table `GOLDEN_HASHES_IC3`; the
    `GOLDEN_HASHES` and `GOLDEN_HASHES_V110` tables are unchanged.
  - **Not in 1.0.0:**
    - a bank-holiday day type, and 5- or 1-minute grids;
    - partial or recurring overrides;
    - ramp, efficiency and reactive power;
    - prices, penalties and tenor;
    - rebound kW, turn-up and pre-cool;
    - humidity;
    - multi-asset envelopes;
    - signatures;
    - a quote content hash;
    - a constraint-library content schema;
    - the IC-1 time-of-day profile check.

## 2026-11-26: IC-12 chassis 2.1.0, an additive minor (WP-UW-03, Olly; D79)

IC-1, IC-2, IC-4, IC-11 and IC-13 are unchanged; no schema file of theirs changed.

- **IC-12 2.1.0.** Tag `contracts/ic12-chassis/v2.1.0`. Release name unchanged, `gridq_chassis_v2`.
  - **Why now:** WP-UW-03 (D75 to D77) added optional members to `FinancialCase` and three of its
    parts. `QuoteRecord` embeds `FinancialCase`, so `--check` reported `QuoteRecord` drifting from
    `ic12_quote_record.v2.0.0.schema.json`. This release versions that change instead of editing
    the 2.0.0 file.
  - **Archived:** the three `v2.0.0` files, byte for byte, pinned by SHA-256 in `__main__.ARCHIVED`
    at their hashes under the tag `contracts/ic12-chassis/v2.0.0`.
  - **`SiteModel` and `OptimisationResult`:** unchanged; their `v2.1.0` files equal the `v2.0.0`
    files byte for byte.
  - **`QuoteRecord`, added, all optional, all defaulting to `null`:** `FinancialCase.lender_terms`;
    `DscrSummary.status`, `.unavailable_reason`, `.cashflow_case`; `CovenantStatus.source`,
    `.lock_up_dscr`, `.lock_up_passes`, `.not_evaluated_reason`; `DebtAssumptions.principal_gbp`,
    `.sized_by`. New definitions: `LenderTerms`, `LenderTermsProvenance`, `LenderTermsSource`,
    `DscrCovenantTerms`, `DscrStatus`, `CashflowCase`, `AmortisationProfile`,
    `CalculationPeriod`.
  - **Reworded, not retyped:** the descriptions of `DscrSummary` and `CovenantStatus`. No existing
    member, type, `required` list or definition changed otherwise, and nothing was removed.
  - **Compatibility:** every 2.0.0 instance is a valid 2.1.0 instance, and no identity hash moves
    (`input_hash`, `quote_id`, finance `case_id`; D78). Unlike the 2.0.0 members, these are dumped
    as `null` while unset, so a 2.0.0 reader refuses a 2.1.0 record that carries a financial case.
    That is the same rule as every minor here: an earlier reader refuses what it cannot honour.
  - **Conformance:** `tests/contracts/test_ic12_chassis.py::test_2_1_0_is_2_0_0_plus_the_stated_optional_members`
    pins the delta exactly; `::test_a_2_0_0_reader_refuses_a_payload_that_sets_a_2_1_0_member`
    states the down-level behaviour.

## 2026-11-26: IC-4 1.2.0 and IC-15 contract template set 1.0.0 (WP-GRC-09, Olly)

IC-1, IC-2, IC-11 and IC-13 are unchanged. The IC-4 `v1.1.0` schema is archived and pinned by
SHA-256 beside the six `v1.0.0` files, so the IC-4 1.0.0 and 1.1.0 schemas are byte-identical to
their tags. Every earlier fixture and golden hash is unchanged. Decision: Fable 5.2 under
DEL-2026-11-25-1 (plan `docs/shared_work/wp_grc09_plan.md` §2).

- **IC-4 `EvidenceObject` 1.2.0, a types-only minor.**
  - **Types:** five appended after the thirty-two: `assessment_statement_of_work`,
    `master_services_agreement`, `site_access_installation_agreement`,
    `lender_reliance_letter` and `performance_wrapper_agreement`. None is self-issuable.
  - **Roles:** no role is added, so IC-2 and IC-11 are not re-versioned.
  - **R7:** an object labelled 1.1.0 holds only a 1.1.0 type.
  - `SUPPORTED_VERSIONS` is `("1.0.0", "1.1.0", "1.2.0")`. Inventory gap G2 is closed and G8 is
    added (no MGA or floor-provider role; owner WP-GRC-13).
  - Tag: `contracts/ic4-evidence-object/v1.2.0`.
- **IC-15 `ContractTemplateSet` and `ExecutedContractTerms` 1.0.0** (roadmap NEW-IC-15, new).
  - Seven templates: the MSA in full text, and the other six as heads of terms. Each is pinned by
    its hash and opens with the DRAFT header.
  - **All seven are `draft_needs_legal_review`.** `counsel_reviewed` binds the reviewed text hash
    and a third-party `legal_opinion`.
  - The MSA carries eight named clauses, the spec §8 safety hierarchy as an ordered list, and
    control authority staged L0 to L4 with the ADR-D2 gates.
  - IC-3 and IC-8 are referenced by contract and `1.x` range, `not_yet_frozen` (WP-SIZE-07;
    WP-LEDGER-07 with D-2, XG-22), and never named in prose.
  - Only ADR-D1's four list prices are on record. Every other term is to be agreed, with its ask.
  - The schemas embed IC-4 only through `EvidenceRef`.
  - Functions: `check_executed_terms`, `check_counsel_review`, `effective_review`,
    `template_set_findings` and `reference_findings`. New exceptions: `TemplateRefused`,
    `DraftTemplateRefused`, `TemplateTextMismatch`.
  - Tag: `contracts/ic15-contract-template-set/v1.0.0`.
- 27 new mutation guards (`test_mutations_v120.py`), 66 in all.

## 2026-11-26: IC-12 chassis 1.0.0 and 2.0.0, release `gridq_chassis_v2` (WP-CORE-03, Olly)

IC-1, IC-2, IC-4, IC-11 and IC-13 are unchanged; no schema file of theirs changed.

- **IC-12 1.0.0** (roadmap NEW-IC-12). `SiteModel`, `OptimisationResult` and `QuoteRecord` as they stood
  at the `gridq_clean_baseline` tag, rendered retroactively: `ic12_site_model`,
  `ic12_optimisation_result`, `ic12_quote_record`, each `.v1.0.0.schema.json`. Archived and pinned by
  SHA-256 on the commit that introduces 2.0.0. Tag `contracts/ic12-chassis/v1.0.0`.
- **IC-12 2.0.0.** Tag `contracts/ic12-chassis/v2.0.0`.
  - **Added, all optional, all absent from a dump while unset:** `ConnectionDetails.agreement`
    (IC-1 `ConnectionAgreementRef`); `SiteModel.evidence_refs`;
    `OptimisationResult.connection_agreement` and `.evidence_refs`;
    `QuoteRecord.connection_agreement` and `.evidence_refs`. New definitions: `EvidenceLink`
    (`supports` plus an IC-4 `EvidenceRef`), and the embedded `ConnectionAgreementRef` and
    `EvidenceRef`, both with the `1.x` version range.
  - **Changed meaning:** `ConnectionDetails.anm_or_curtailment_notes` is commentary. Connection
    terms come from the IC-1 agreement `agreement` points at. This is why the version is a major.
  - **Compatibility:** every 1.0.0 instance is a valid 2.0.0 instance and dumps to the same bytes,
    so no hash moves. A 1.0.0 reader refuses an instance that sets a new member.
  - The models live in `gridq.schemas` and the version constants in `gridq/schemas/chassis.py`.
    Only `__main__` reads them; no contract module imports outside this package.
  - The instance stamp `schema_version` ("0.1.0") is not the contract version and did not move.

## 2026-11-24: IC-4 1.1.0, and IC-1, IC-2 and IC-11 to 1.1.0 (WP-BO-06, Olly)

IC-13 is unchanged. All eight `v1.0.0` schema files are byte-identical to their tags: the six that
1.1.0 supersedes are archived and pinned by SHA-256 (`__main__.ARCHIVED`), and `--write` refuses to
change one. Every 1.0.0 fixture and golden hash is unchanged and still validates.

- **IC-4 `EvidenceObject` 1.1.0** (roadmap WP-BO-06; ADR-D10 option b, widened by the owner on
  2026-11-23).
  - **Types:** seventeen appended after the fifteen of 1.0.0: `data_processing_agreement`,
    `data_sharing_agreement`, `non_disclosure_agreement`, `legal_opinion`,
    `conformity_certificate`, `technical_report`, `data_licence`, `method_agreement`,
    `service_level_agreement`, `commissioning_record`, `control_authority_signoff`,
    `fmar_registration`, `metered_data_record`, `operational_data_record`, `baseline_method`,
    `trial_record`, `decision_record`.
  - **Roles:** six third-party `IssuerRole` members appended after the sixteen: `legal_adviser`,
    `certification_body`, `data_provider`, `funder`, `research_partner`,
    `independent_verifier`. `IssuerRole` still has no Neura member.
  - **The restricted issuer:** `SelfIssuerRole.NEURA_SELF_ISSUED` (`neura_self_issued`), the marker
    on the wire. Neura may issue only `SELF_ISSUABLE_TYPES` (`baseline_method`, `trial_record`,
    `decision_record`); third parties may issue them too.
  - **Rules:** R1 and R2, the down-level rule (an object labelled 1.0.0 holds only a 1.0.0 type
    and role); R3, Neura self-issues only those three types; R4, Neura cannot verify its own
    record; R5, a self-issued decision record names its decision; R6, it states when it takes
    effect.
  - **Functions:** `require_third_party` (raises `SelfIssuedEvidenceRefused`; judges the object
    re-validated from its canonical JSON); `resolve_ref` with `expected_type`, `admit_self_issued`
    and `include_synthetic`, refusing in a stated order (admission without a type, not found,
    version mismatch, wrong type, synthetic, self-issued); `assess(..., admit_self_issued=False)`
    skips a self-issued match unless admitted, and `EvidenceCheck` gains `best_is_self_issued`.
    `evidence_ref` states the object's own version. New exceptions: `EvidenceRefused`,
    `SelfIssuedEvidenceRefused`, `SyntheticEvidenceRefused`, `EvidenceNotFound`.
  - `EvidenceRef.contract_version` accepts the `1.x` range (`_base.Version1x`).
  - `IC4_INVENTORY.md` maps the roadmap's evidence needs to types and roles; gaps G2 to G7 have
    owners.
  - Tag: `contracts/ic4-evidence-object/v1.1.0`.
- **IC-1 `ConnectionAgreement` 1.1.0:** version references accept the 1.x range; own version
  1.1.0. Tag: `contracts/ic1-connection-agreement/v1.1.0`.
- **IC-2 `CurtailmentScenarioSet` 1.1.0:** version references accept the 1.x range; own version
  1.1.0; the IC-4 third-party role list it embeds grew by six. Tag:
  `contracts/ic2-curtailment-scenario-set/v1.1.0`.
- **IC-11 thermal flex 1.1.0:** version references accept the 1.x range; own version 1.1.0; the
  IC-4 third-party role list it embeds grew by six. Tag: `contracts/ic11-thermal-flex/v1.1.0`.

## 2026-11-23: IC-11 thermal flex 1.0.0, pre-tag review fixes (Olly)

Fixes from the pre-tag review (`docs/reviews/2026-11-23-ic11-thermal-flex-review.md`). The
version stays 1.0.0 because no tag exists yet. The IC-11 schemas were regenerated. IC-1, IC-2,
IC-4 and IC-13 are unchanged, and their schemas re-check byte-identical.

- **F1: `check_instruction` reads the whole event.** It used to check only `start`, so an event
  could run from an allowed cell into an `unavailable` one, or past `valid_until`. It now refuses
  with `outside_validity_window` when `end > valid_until`, with `availability_unavailable` when
  `[start, end)` touches any `unavailable` cell, and with `availability_unknown` when it touches
  any `unknown` cell (the stated, conservative rule). The kW is still read from the start cell.
  The walk is on the GB clock and does not step over a clock change. No new reason codes were
  needed: each existing code now documents the whole-event rule.
- **F2: the IT-safety nesting is never skipped, and `binding_source` is checkable.** The binding
  band, and a known SLA band, must be measured at the ASHRAE band's point. The binding band must
  lie inside the ASHRAE band and inside a known SLA band. `binding_source: ashrae` requires the
  binding band to equal the ASHRAE band, `customer_sla` requires it to equal the SLA band, and
  `operator_margin` requires it to lie inside both. Before this fix a 5.0-45.0 °C band at supply
  air validated against an 18-22 °C ASHRAE band at the IT inlet. Known v1 limit: the binding
  band is temperature only, and which humidity limit is enforced is not stated.
- **F3: a declared kW is firm.** This is the owner's decision of 2026-11-23. Asked what a
  declared kW in a season and time band should mean, the owner chose "Firm (Recommended)": "X can
  be delivered whenever an event starts in that band, while staying within the IT temperature
  limits. Sizing and dispatch can rely on it as stated." The contract's meaning is now stated in
  the module docstring, in the `FlexPoint` and `ThermalFlexAvailability` schema descriptions and
  in the README. For each curve point, the kW is deliverable for that point's duration at any
  event start inside the cell, within the binding IT-safety band. It is not a typical or
  expected value. Validation is unchanged, and no P50/P90 range is added.
- **F4: fixture and text corrections.**
  - The ASHRAE source string named the 5th edition (2021) beside a 60 % RH figure that may be
    another edition's. No number was changed. The string now names no edition and says the
    figures are unverified against the standard.
  - The rule is now stated: `evidence_class` is the floor for every `flex` and `zero_flex` cell,
    and a cell without evidence of that grade is `unknown`. It is a producer obligation; the
    model does not check it.
  - The metered fixture follows the rule. Its trial ran January to March, so `winter` is split
    into `winter_jan_mar` (metered cells) and `winter_oct_dec` (all `unknown`). Before, October to
    December were served as metered.
  - All six IC-11 golden hashes changed: the two surfaces changed, and the instruction and its
    acknowledgements bind the metered surface by hash.
- **F5** items stay accepted inputs, as the review allows.

## 2026-11-22: IC-11 thermal flex 1.0.0 (Olly)

- **IC-11 1.0.0** (roadmap WP-THERM-02, NEW-IC-11, spec A4-02, A4-03, TF-02 and TF-04).
  - `ThermalFlexAvailability`: kW holdable for a duration in minutes, by season and local
    time-of-day band, with the rebound (energy, peak, recovery time) on every curve point.
  - Each cell is `flex`, `zero_flex`, `unavailable` or `unknown`: zero flex is valid and
    distinct from unavailable, and no cell is null or missing.
  - IT-safety limits are declared, never computed: the ASHRAE class and envelope, an optional
    customer SLA, and a binding band. (Corrected 2026-11-23, review F2: as first committed, the
    binding band was checked against the ASHRAE and SLA bands only when they shared a
    measurement point, so "inside both" did not always hold. See the entry above for what is
    checked now.)
  - Evidence class `modelled`, `trialled` or `metered`. Trialled and metered require IC-4
    `EvidenceRef`s and a registered baseline method.
  - `FlexInstruction` (id, kW, start, duration, issuer role, surface hash) and
    `FlexAcknowledgement` (accepted, rejected with a reason code, or partially accepted with a
    kW), bound to the instruction by hash. All instants UTC.
  - Integers throughout: kW, kWh, minutes, tenths of a degree.
  - Consumer helpers `query`, `query_at`, `check_instruction` and `verify_acknowledgement` need
    no plant model.
  - Tag: `contracts/ic11-thermal-flex/v1.0.0`.
- IC-1, IC-2, IC-4 and IC-13 are unchanged; their schemas re-check byte-identical.

## 2026-11-17: IC-2 `CurtailmentScenarioSet` 1.0.0 and IC-13 identity/audit 1.0.0 (Olly)

- **IC-2 1.0.0** (roadmap WP-CONN-03, spec §10A and A1-06).
  - Each scenario carries a half-hourly series and a minute series (or `NotApplicable`), its
    events (start, end, mean and peak depth, notice, source ref), and an exact rational weight.
  - Weights must sum to exactly 1 (`fractions.Fraction`), with no tolerance.
  - Series are step-encoded, with one encoding per series, so the hash is stable under
    re-serialisation.
  - The evidence class (replayed real data, modelled or synthetic) and the signal basis
    (generation export, demand import or loading headroom) are explicit.
  - Provenance reuses IC-4 (`IssuerRole`, `EvidenceRef`), and the set links to IC-1 by hash.
  - Consumer helpers `expected_series` and `expected_energy_mwh` need no engine.
  - Committed fixtures are synthetic-shape. Real UKPN and NPg fixtures are built from recorded
    responses but kept out of git under the Atlas eyes-only gate (`fixtures/real/README.md`).
  - Tag: `contracts/ic2-curtailment-scenario-set/v1.0.0`.
- **IC-13 1.0.0** (roadmap WP-PLAT-05, NEW-IC-13, spec E-04 and B1-04).
  - Claims: issuer, subject, audience, times, token id, organisation, role, sorted scopes and
    `amr`. Roles are Neura ops, customer, lender read-only and verifier, with per-role actions
    enforced in the model.
  - RS256 JWT validated offline with the standard library, so no new dependency.
  - Audit events cover customer-data reads and control writes, allowed and denied. Each carries
    actor, scope, target, action, timestamp, `seq` and `prev_hash`, forming a hash chain checked
    by `verify_chain`.
  - Fixture keys are marked TEST-ONLY and refused by default.
  - Tag: `contracts/ic13-identity-audit/v1.0.0`.
- `_base.UtcInstant` added (UTC-normalised instants; naive times refused). IC-1 and IC-4 are
  unchanged; their schemas re-check byte-identical.

## 2026-11-16: IC-4 `EvidenceObject` 1.0.0 and IC-1 `ConnectionAgreement` 1.0.0 (Olly)

- **IC-4 1.0.0** (roadmap WP-BO-01, spec §10A, D1).
  - Fields: type, issuer (name, role, reference), document hash, issued and effective dates,
    expiry (date, no-expiry or unknown), scope (site, or stream at one site or all sites), and
    verification (verifier, role, method and date, or unverified with a reason).
  - Expired documents validate and report `expired`. `assess()` distinguishes `expired` from
    `absent`.
  - Assumptions cannot be constructed or serialised as evidence.
  - Tag: `contracts/ic4-evidence-object/v1.0.0`.
- **IC-1 1.0.0** (roadmap WP-CONN-01, spec A1-01 and §10A).
  - Carries every A1-01 term plus ANM response time and signal protocol.
  - Unknown terms are explicit `Unknown` values with a reason, and no field accepts null.
  - Firm, hybrid and fully non-firm synthetic archetype fixtures are included.
  - It replaces the free-text `ConnectionDetails.anm_or_curtailment_notes` as the contract.
  - Migrating `SiteModel` onto it is consumer work (WS-CORE and WS-CONN) and is not part of this
    freeze.
  - Tag: `contracts/ic1-connection-agreement/v1.0.0`.
