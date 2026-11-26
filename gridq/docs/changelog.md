# Changelog

All meaningful changes to this project. Newest first. Dates are ISO (YYYY-MM-DD).
(Canonical location; the former top-level `CHANGELOG.md` now points here.)

## 2026-11-26 — Integration: WP-SITE-08 merged into `roadmap/p1-foundations`

- **D-numbers.** WP-SITE-08's five placeholders took D158 to D162 (SITE08-1 to -5 in order), in the
  `gridq.datacentre.orchestrators` docstrings, decisions, changelog and handoff entry. No hashed
  artefact or fixture cites them.
- **Conflicts** only at the tops of `docs/decisions.md`, `docs/changelog.md` and the handoff log (the
  branch's 18:49 entry placed in time order).
- **Tidy.** The WP-EDGE-02 integration notes quoted that branch's placeholder literally; they now
  name it as "the branch placeholder for decision EDGE02-3", so a search for unresolved
  placeholders again finds only the two deliberate IC-9 lines (pinned to the IC-9 tag).
- **Kept green.** The DPA read waiver is still 12 (the Slurm fixtures are synthetic and labelled so);
  no contract or IC-15 reference changes.

## 2026-11-26 — WP-SITE-08: workload orchestrator adapter pattern, Slurm reference adapter (`gridq.datacentre.orchestrators`)

- **The pattern (D159).**
  - Interface: `OrchestratorAdapter`, implemented by every `SealedAdapter`, whose `declare(state,
    policy)` cannot be overridden.
  - Outcome: `Declared` (an IC-9 1.0.0 declaration validated by the frozen model, a field-level
    `DerivationLedger`, the classes skipped and the resources withheld with reasons, and a
    `derivation_hash` binding state, policy, ledger and declaration) or `Refused` (closed
    `RefusalCode`s, each with a JSON pointer and a detail; never an envelope).
  - Registry: `orchestrator_adapters`, keyed by API id, owned by the package (never CORE). Importing the
    package registers nothing.
  - Inputs: a labelled `StateDocument` (raw API bodies, `recorded` or `synthetic`, API doc URL,
    `captured_at`) and an operator `AdapterPolicy`. Nothing is defaulted, fetched or written.
- **The reference adapter, Slurm (D158, D160, D161, D162).**
  - It reads five slurmrestd `v0.0.46` response bodies (jobs, nodes, partitions, reservations,
    slurmdb QOS) in the shape documented at https://slurm.schedmd.com/archive/slurm-26.11.0/rest_api.html.
  - Classes are (partition, QOS, outcome) from the effective preempt mode: suspend and
    requeueable-requeue are deferred, the rest dropped.
  - kW is measured node power less the operator's stated residual, counted on nodes wholly in one class.
  - Each half-hour of the horizon offers nested rung envelopes as dated overrides. MAINT reservations
    clamp job ends and black out half-hours.
  - The ramp is the grace time plus the policy's settle time.
- **Fixtures.** `tests/fixtures/orchestrators/` holds a synthetic reference state and policy, built by
  `tests/_orchestrator_support.py` and labelled:
  - `SYNTHETIC` notes;
  - `SYNTHETIC-` QOS, site and operator;
  - placeholder prices and evidence hashes.
  Nothing is recorded from a cluster.
- **Tests.** `tests/test_orchestrator_adapters.py`, 48 tests:
  - IC-9 conformance (frozen-model round trip, committed 1.0.0 schema, `resolve`/`timeline_matches`);
  - a hand-checked reference;
  - 400 seeded random clusters and 300 damaged payloads;
  - refusal tests for every code;
  - the registered branch with in-test documents only;
  - sealed and registry tests and an AST no-I/O test;
  - ten mutants plus a control.
- **Untouched.** IC-9 and every archived schema, every golden, and the DPA waiver (12).

## 2026-11-26 — Integration: WP-EDGE-02 merged into `roadmap/p1-foundations`

- **D-numbers.** WP-EDGE-02's four placeholders took D154 to D157 (EDGE02-1 to -4 in order), in the
  package docstrings, decisions, changelog and handoff entry.
- **`DESIGN_HASH` re-pinned once, before any consumer bound to it.** One invariant statement
  (`bms_mode_is_what_it_says`) quoted the branch placeholder for decision EDGE02-3 into the released design, so the renumbering
  changed the release's bytes. `python -m gridq.edge_design --write` re-rendered the release JSON
  and `docs/design/edge_zone_conduit_design.v1.0.0.md`; `DESIGN_HASH` moved from
  sha256:76ccb848… to sha256:9737bb54…. Checked: the new JSON equals the branch's with
  that placeholder → `D156` and nothing else, and the document differs only there and in the hash
  line. The design stays v1.0.0 because nothing outside this merge had bound to the old hash; the
  re-pin is recorded in a comment above `DESIGN_HASH`. `--check` returns 0.
- **Conflicts** only at the tops of `docs/decisions.md`, `docs/changelog.md` and the handoff log (the
  branch's 18:45 entry placed in time order).
- **Kept green.** The DPA read waiver is still 12; no contract or IC-15 reference changes.

## 2026-11-26 — WP-EDGE-02: IEC 62443-3-3 SL 2 zone and conduit design (`gridq.edge_design`)

- **New release `edge_zone_conduit_design` v1.0.0 (D154 to D157).** A typed zone and conduit
  graph on the Purdue model, authored in code, released as canonical JSON
  (`src/gridq/edge_design/release/edge_zone_conduit_design.v1.0.0.json`, hash pinned as
  `DESIGN_HASH`) with a generated document (`docs/design/edge_zone_conduit_design.v1.0.0.md`, Mermaid
  diagram by Purdue level). `python -m gridq.edge_design --check | --write`. Not a frozen contract: no
  schema archive, tag or `gridq.contracts` entry.
  - Twelve zones by owner and Purdue level; eleven base conduits plus the BMS conduits of four modes.
  - Every conduit carries its protocols, a three-part direction (initiator, data flow, authority) and
    its controls; a conduit missing any cannot be built, and one whose controls miss the FRs its
    attributes demand is refused (acceptance 1).
  - Twenty named invariants, checked in every BMS mode on release (adjacent levels only, outbound-only
    boundary, ANM on site only, writes only into admitting zones, no wireless, and others).
  - The BMS: a data diode by default (`diode_read_only`, L2 at most); L3 only by a one-way chiller band
    offer the host's BMS adopts (`diode_with_band_offer`) or a brokered conduit that is a listed gap.
    The design document notes that spec B1-07's two sentences cannot both hold for a write path.
  - Consistent with IC-5 1.0.0: point-class stages probed from `check_setpoint_schedule`, every
    comms-loss trigger mapped to a conduit, fail-safe writes matching the write bindings, and a
    chiller band at a site without an L3 mode refused `outside_write_scope`.
  - `write_bindings(stage, mode)` is the set WP-EDGE-03's register allow-list must stay inside.
- **SL 2 review (acceptance 2).** The 51 SRs by their public identifiers and titles only (ISA's
  published table of contents), with NIST CSF 1.1 as a second witness (47 SRs cited), four public
  sources cited by URL and SHA-256. 13 title-level mapped, 20 partially mapped, 17 deferred to a work
  package, 1 host responsibility; 23 gaps. Not certified, not a conformity assessment: the status
  fields are `Literal[False]`, the achieved level is `Unknown`, and the disclaimer is printed nine
  times in the document.
- **Sites.** `SiteZoneConduitInstance` binds a site by design hash with every site fact unknown and
  twelve data asks (host, DNO, OEM, meter operator, partner, ThermaFlex); an unknown BMS mode
  permits L2 at most; an L3 mode needs the safety case and host sign-off by hash.
- **Tests.** `tests/edge_design/` (344), including twelve mutation guards with their control runs.
  No archived schema, golden or DPA waiver touched.

## 2026-11-26 — Integration: WP-TEL-01 merged into `roadmap/p1-foundations` (IC-14 1.0.0; OPS-11 re-bound)

- **D-numbers.** WP-TEL-01's six placeholders took D148 to D153 (TEL01-1 to -6 in order). Neither
  the IC-14 module docstring nor its generated specification cites them, so the tagged bytes are
  untouched: the four `ic14_*.v1.0.0` schemas, `specs/ic14_telemetry_series.v1.0.0.md` and
  `telemetry_series.py` equal the `contracts/ic14-telemetry-series/v1.0.0` tag byte for byte. The
  tag stays on the branch commit 9e3b004.
- **Kept both sides** at the contracts README (the IC-14 row after IC-12 3.1.0; 197 mutation guards,
  recounted after WP-UW-17: 104 + IC-9's 42 + IC-14's 51, UW-17 added none) and CHANGELOG (eleven
  `v1.0.0` tags, twenty contract tags; the IC-14 entry above IC-12 3.1.0). `__main__` (FROZEN,
  SPECS), `__init__`, `_contracts_support.py`, the IC-4 embedder set and the IC-9 spec tests merged
  cleanly.
- **OPS-11 re-bound (the tripwire fired as designed).** With IC-14 in `FROZEN`,
  `test_tripwire_no_awaited_contract_or_product_exists_yet` failed. Seven telemetry terms now bind
  to `ic14_curated_series` v1.0.0 (`/intervals/*/value`, a good `Aggregate`; a `CuratedGap` or any
  other quality is a gap), at `settlement_30min` or `standard_1min`, converted by `w_to_kw`:
  - `a.boundary_import_kw` / `a.boundary_export_kw`: `site_boundary.<id>.active_power.revenue_meter.whole`,
    the import / export half of `split_import_export_w` (floor / ceil);
  - `c.net_import_kw`: the same series, used directly (ceil);
  - `a.asset_output_kw`: `{genset,pcs,pv}.<id>.active_power.{operational_meter,revenue_meter}.whole`,
    negated (floor); `b.pcs_ac_kw`: `pcs.<id>.active_power.{device_reported,operational_meter}.whole`,
    negated (floor);
  - `c.asset_kw`: `{chiller,crah,it_load,ups}.<id>.active_power.operational_meter.whole` (ceil);
  - `b.instructed_kw`: `pcs.<id>.active_power.setpoint_echo.whole`, rounded away from zero (the
    formula only tests it against zero).

  Rounding is the conservative side for each term's place in its formula. `FrozenContractField`
  gains a unit-from-quantity variant (`UnitFromQuantity`): an IC-14 value's unit is its point's
  quantity's (W, checked against `QUANTITY_TABLE`), not a field-name suffix; `ic14_kw` applies the
  sign, split and rounding and is tested on values. `control_authority` (no IC-14 1.0.0 quantity)
  and `b.asset_enveloped` (the meter registry, WP-TEL-04) stay not yet measurable. The tripwire now
  watches IC-8, the named products (`telemetry_store`, WP-TEL-02, still absent) and IC-14 moving
  past 1.0.0. Formula hashes are unchanged; binding hashes moved, so the options pack was
  re-rendered and `GOLDEN_WORKED_COUNT` re-pinned (sha256:68d182b3… → sha256:2fb489da…): only
  `definitions_hash` differs, every tally, cell and ask is byte-identical.
- **Kept green.** The DPA read waiver is still 12; no IC-15 reference names IC-14.

## 2026-11-26 — WP-TEL-01: IC-14 telemetry series contract frozen (`gridq.contracts.telemetry_series`)

- **New frozen contract IC-14 1.0.0** (roadmap NEW-IC-14; D148 to D153, Fable 5.2). Four
  schemas: `SeriesDescriptor`, `TelemetryBatch` (raw), `DqAnnotation` (data-quality overlay) and
  `CuratedSeries`, each `ic14_*.v1.0.0.schema.json`, plus the generated specification
  `specs/ic14_telemetry_series.v1.0.0.md`, checked by `python -m gridq.contracts --check`.
  Annotated tag `contracts/ic14-telemetry-series/v1.0.0` on the WP-TEL-01 commit (local).
- **What it freezes.** Point naming (`PointName`, `point_key`, `series_id`); one integer unit per
  quantity (W, Wh, var, VA, mHz, mV, mA, basis points, millidegrees C); fixed UTC blocks of integer
  slots with a `ClockStatement` per block; `ValueQuality` and `GapReason` mapped to verified OPC UA
  status codes; a chained raw tier, an overlay and superseding curated revisions; a `TenantKey` and
  `data_class` in every record and its hash, with readers that need an IC-13-backed `ReadDecision`.
- **A missing sample is a gap with a reason, never a zero.** Raw values and gaps are different types
  that must tile every slot; a curated interval below its method's stated usable time is a
  `CuratedGap`. Property tests P1 to P14 cover producers, resampling and aggregation.
- **IC-5 is unchanged.** A batch is shaped to become an IC-5 body in a later IC-5 minor; a union test
  proves it fits.
- **Tests:** `tests/contracts/test_ic14_telemetry_series.py` (synthetic, labelled fixtures in
  `tests/contracts/fixtures/ic14/` pinned by seven hashes; a schema-only gateway; a consumer in a
  fresh interpreter without the producer; 51 mutants). The IC-9 spec tests now read their own entry,
  because `SPECS` has two. `gridq.telemetry` (quote telemetry) gained a pointer to IC-14.

## 2026-11-26 — Integration: WP-OPS-11 merged into `roadmap/p1-foundations`

- **D-numbers.** WP-OPS-11's five placeholders took D143 to D147 (OPS11-1 to -5 in order). The
  options pack `docs/ops/demonstrator_mw_options.md` cites them in its header, so it was re-rendered
  (`python -m gridq.ops.demonstrator_mw --as-of 2026-11-26 --write-pack ...`); the render equals the
  renumbered file, and the definitions and grant-text register hashes did not move.
- **Fixed at the merge.** One `gridq.ops` docstring line was 101 characters on the branch (E501);
  the paragraph is rewrapped, wording unchanged.
- **Conflicts** only at the append points; both sides kept, HANDOFF entries in time order.
- **Kept green.** The OPS-11 tripwire holds (no NEW-IC-14 or IC-8 in code yet; WP-TEL-01 merges
  next and rebinds the telemetry terms). The DPA read waiver is still 12; no contract changed.

## 2026-11-26 — WP-OPS-11: demonstrator MW definitions, measurement basis and worked count

- **Olly** (on `roadmap/wp-ops11-mw`, cut at 8e1bd45). Design: D143 to D147 (Fable 5.2, under the
  owner's delegation).
- **Three candidate formulas** (`gridq.ops.mw_formula`, `mw_definitions`). Site load under management (A),
  BESS MW actively dispatched (B) and verified flexible MW (C) are typed, unit-checked expression trees,
  evaluated exactly. Each states its quantity, measurement points, averaging period and qualifying
  conditions. Every open parameter is a slot listing each option; none has a default.
- **Every term mapped** (`mw_bindings`): to a frozen contract field (IC-1, IC-3, IC-4, IC-11, by
  pointer), a dossier field, or "not yet measurable" with the contract, product and roadmap work packages
  that will measure it (NEW-IC-14 `telemetry_store`, `preregistered_baselines`, `reconciliation_report`,
  IC-8 `verifier_dataset`), quoted from a pinned Atlas roadmap commit. A tripwire fails when one lands.
- **Worked count** (`demonstrator_mw`). Per site and definition: an upper bound from evidenced dossier
  fields, or unknown with the missing field and its asks, or not in the dossier with the inputs that will
  supply it. With no DPA registered, all 8 real sites are unknown under A and B and not in the dossier under
  C. Per-site output goes only to git-ignored `dossiers/`; the tracked pack holds tallies.
- **Grant text** (`mw_grant_text`). 20 verbatim quotes from the held Q9 PDF, the earlier Q9 draft, the
  competition 2509 page and (as context only) the kickoff notes, each with document and section and
  checked by sha256 in place. No document is copied into the repo. The held PDF says "~50MW distributed";
  the draft states no 50 MW figure; which was submitted is an ask. Theory of Change, themes, assessor
  guidance, briefing, offer letter (AW-01/04/09) and letters of support are owner asks.
- **Pack** `docs/ops/demonstrator_mw_options.md` (generated; a test fails if stale), with a draft agreement
  text marked DRAFT, rendered from the definitions. It recommends no definition and records none as agreed
  (WP-OPS-02).
- **Tests.** `tests/ops/test_mw_formula.py`, `test_mw_bindings.py`, `test_mw_grant_text.py`,
  `test_demonstrator_mw.py`. The DPA read waiver is still 12.

## 2026-11-26 — Integration: WP-UW-02 merged into `roadmap/p1-foundations`

- **D-numbers.** WP-UW-02's ten placeholders took D133 to D142 (UW02-1 to -10 in order). The D-ids
  sit only in comments, docstrings and the fixture PIN note, so the eligibility rule-table hash and
  the pinned Atlas snapshot bytes do not move.
- **Conflicts** only at the append points (decisions, this changelog, `HANDOFF_LOG.md`); both sides
  kept. The new `gridq.underwriting.eligibility*` modules are imported by nothing else; the only edit
  to existing code is two docstring lines in `underwriting/__init__.py`.
- **Kept green.** The DPA read waiver is still 12; no contract changed.

## 2026-11-26 — WP-UW-02: the site eligibility engine (every stream eligible, ineligible or undetermined, with the reason)

- **Olly** (on `roadmap/wp-uw02-eligibility`, cut from `roadmap/p1-foundations` at 8e1bd45).
- **New modules in `gridq.underwriting`.** `build_eligibility` lists every site of an OPS-01 dossier against
  seven streams: metering class and GSP group, P511 export band, ANM zone, CM class, DFS, DNO flexibility
  zone and load-control exemption class. Each stream is eligible, ineligible or undetermined, with a reason
  per check, the rows it cites, the facts it read and the data requests that would close it. The product is
  `gridq.site_eligibility` 1.0.0 (hashed, None-free). The rule table `gridq.underwriting.eligibility_rules`
  1.0.0 is hashed separately; UW-01's table hash does not move. Design: D133 to D142 (Fable 5.2,
  under the owner's delegation).
- **Every rule cites a register row.** Each check cites Atlas register rows and asks the register gate
  (`require_verified`, with the run's `as_of`) about each one on every run. A refused row makes its check
  undetermined with the refusal, never ineligible.
- **Load control through `load_rule` only.** Each run starts a fresh `python -I -B` subprocess
  (`eligibility_bridge.py`) that calls the Atlas `load_rule` with the run's `as_of`, the Atlas `evaluate` per
  site, and the gate per row. Any loader failure, timeout, bad output or file changed during the read is
  undetermined with the reason. A domestic or small-business customer fails the check. While the rule is
  provisional a passing site is undetermined, "never exempt"; a test swaps in a final-rule stand-in and the
  answer changes. Under `registered_only` the engine relies only on the adopted loader pair (by sha256).
- **No invented figures.** P511's thresholds are read only from a machine-readable `thresholds` field on the
  register row, verified against the row's own quotes. No row has one yet, so the band is undetermined (owner
  action ATLAS-THRESHOLDS).
- **Stands alone.** Nothing in `gridq run`, the app or the UW-01 gate reads it (WP-UW-04 wires it); it
  never gates `all_streams_enabled_study_v1`. CLI `python -m gridq.underwriting.eligibility --as-of` writes
  only to a git-ignored path (`dossiers/eligibility/`). With no DPA and no facts, every real site is
  undetermined on every stream, with data requests.
- **Tests.** `tests/test_site_eligibility_engine.py` (54) on SYNTHETIC TEST FIXTURE Atlas trees around byte
  copies of the Atlas gate and loader pinned at 0cc045be (`tests/fixtures/atlas_load_control/`).

## 2026-11-26 — Integration: WP-UW-17 merged into `roadmap/p1-foundations` (IC-12 3.1.0)

- **D-numbers.** WP-UW-17's five placeholders took D128 to D132 (UW17-1 to -5 in order), in code,
  tests, the rendered quote schema descriptions and the docs.
- **IC-12 2.2.0 became 3.1.0.** The branch released its additions as 2.2.0 over 2.1.0, but WP-UW-07
  (merged first) moved IC-12 to 3.0.0. On this line the same members are the additive minor 3.1.0
  of 3.0.0: `CONTRACT_VERSION` 3.1.0, `SUPPORTED_VERSIONS` ending `("3.0.0", "3.1.0")`, release still
  `gridq_chassis_v3`. The three `v3.0.0` files are archived and pinned by SHA-256 at their tagged
  hashes (`__main__.ARCHIVED`, `TAGGED_IC12_V3_0_0`); the `v3.1.0` site and optimisation files
  equal `v3.0.0` byte for byte, and the quote delta is exactly the branch's 2.2.0 delta (24 new
  definitions; `FinancialCase` and `PriceRecommendation` grown). No `v2.2.0` file or tag exists.
  Tag `contracts/ic12-chassis/v3.1.0` sits on this merge. Both sides of the IC-12 conformance test
  are kept: the 2.1.0 → 3.0.0 delta and upgrader tests, and the new 3.0.0 → 3.1.0 delta.
- **Renamed:** the chassis constants, the archive pin, the contracts CHANGELOG (the 2.2.0 entry is now
  the 3.1.0 entry; nineteen contract tags), the contracts README (nineteen archived files), CLAUDE.md
  section 7, D132 and the WP-UW-17 changelog entry. `docs/financial_model.md` section 4.2 named no
  version; its decision references now read D128 to D132.
- **Adapted at the merge.** `tests/test_finance_lifecycle.py` builds its `TariffStack` with the
  synthetic basis and provenance that 3.0.0 requires (`tests/_tariff_support.py`). The B001 line
  citations in the findings register and its finance evidence were recomputed on the merged files
  (`cli.py:411`, `app.py:1216` and `:1325`, `pipeline.py:578`, `financial_model.py:282` and `:493`,
  and the D78, D76 citations at `:473`, `:959`/`:961`/`:963` and `:279`).
- **Kept green.** No IC-15 reference names IC-12 3.x, so no IC-15 schema bump. The DPA read waiver
  is still 12. IC-3 1.0.0 still binds IC-12 `2.x` only (open: an IC-3 minor before WP-SIZE-08
  exports from a 3.x quote).

## 2026-11-26 — WP-UW-17: levelised price, CPI indexation, construction with IDC, and lifecycle flows (IC-12 3.1.0)

- **Olly** (on `roadmap/wp-uw17-finance`, cut from `roadmap/p1-foundations` at afd7871). The design rulings are
  D128 to D132, made by Fable 5.2 under the owner's delegation.
- **Why.** Findings N12, N13, N14 and H-15, H-16 and D-33. The price-to-target used a flat year-one volume, so
  `hits_target_return` did not test the price. Nothing read `financing.cpi_pct_per_year`, so no line was real or
  nominal. The CM term was not indexed. No construction phase or IDC existed, and the COD delay was a
  scenario field that nothing applied. Terminal value, decommissioning and replacements were one generic warning.
- **Price (D128).** The price is now levelised over the delivered volume in each modelled year, with fade
  and availability applied, on the run's own P50 pre-debt series at the target rate. `hits_target_return` is
  measured in a closed loop: the price is added back and the equity (or project) IRR is measured. An
  equity-solved price is reported as a diagnostic only. `PriceRecommendation` gains three optional fields.
- **Indexation (D129).** When the CPI key is numeric:
  - Opex, insurance, metering, DNO and business rates escalate at CPI. Every other line is tagged flat nominal,
    and each line states its basis. A real ledger and a real IRR sit alongside the nominal ones.
  - A T-4 CM agreement in its term is paid U·E_t, per the Capacity Market Rules (SI 2014/2043 Sch 1 para 3(5)
    and 3(7), retrieved from legislation.gov.uk on 2026-11-26).
  - T-1 is held flat, because para 3(6) was omitted by SI 2025/917.
  - Without the ONS CPI figures the year-one uplift U is 1.0, and the run says so.

  When the key is absent the numbers are bit-identical. When it is null the run warns and never reads it as 0%.
- **Construction (D130).** A typed `LifecycleInputs` gives a dated monthly timeline:
  - Construction draws are made before COD, and IDC is rolled into the principal at COD.
  - The IDC rate falls back to the term rate, with a label, when none is stated.
  - The COD delay is supplied through `generate_quote(lifecycle=)`, `gridq run --lifecycle FILE` and the
    app's lifecycle field. It is applied as a zero-weight `cod_delay` scenario row.
  - Without a programme, construction is excluded by a labelled warning, and the set says no delay is assumed.

  No durations, rates or costs default.
- **Lifecycle flows (D131).** Terminal value, decommissioning and replacements are modelled from typed
  inputs, or excluded by name with a data request.
  - Terminal value is in the IRR and never in CFADS. Decommissioning and replacements reduce CFADS in their
    year, and a sculpt is floored at zero in a negative year.
  - A ledger is always populated, and it reaches the underwriting pack as "Lifecycle capital flows".
- **Contract.** IC-12 chassis 3.1.0 is an additive minor of 3.0.0 (D132; 2.2.0 on the branch, renamed at the
  integration merge). The v3.0.0 files are archived by sha256.
  The new members sit outside the quote identity unless lifecycle inputs are supplied.
- **Goldens.** The demo library (gb_v1_4) has no CPI key, so no number moves. Two warning lines are added, and
  the golden's run-to-run checks hold. Academic gb_v1_8 to 12 runs move under D129, recomputed by hand
  in the decision.
- **Tests.** tests/test_finance_lifecycle.py (26) and tests/test_lifecycle_entry_points.py (13). The IC-12 3.1.0
  delta and refusal tests are added. The identity test in test_dscr_on_every_run and the B001 citations are
  updated. In test_consulting_report, the rich fixture's price is no longer £0. Its project IRR is 7.41%
  against a 12% target, so the old zero was the N12 defect. The test now checks the positive price still sits
  beside its target-return state. 14 mutations were made to the call sites and all were killed.
  The fast tier: 4687 passed, 94 skipped, 0 failed. The golden module: 5 passed.

## 2026-11-26 — Integration: WP-SITE-07 merged into `roadmap/p1-foundations`

- **D-numbers.** WP-SITE-07's six placeholders took D122 to D127 (SITE07-1 to -6 in order). Its
  datacentre docstrings that cited the WP-SITE-01 placeholders now cite D81 to D84.
- **One exception.** The IC-9 1.0.0 module docstring and the specification `_specdoc` renders from
  it keep the branch placeholder text: both are pinned byte for byte to the
  `contracts/ic9-compute-turndown/v1.0.0` tag by
  `test_the_git_tag_is_annotated_and_carries_the_frozen_schemas_and_spec`, and the tag is not moved.
  The contracts README states the mapping; a new IC-9 version can carry the real numbers.
- **Kept both sides** at every shared append point: `__main__` (the `_specdoc` import and spec check
  beside IC-3, IC-5 and IC-15; the IC-9 schemas in `FROZEN`), `__init__` (docstring and `__all__`),
  `_contracts_support.py` (`GOLDEN_HASHES_IC9`), the IC-4 embedder set, the contracts README (the IC-9
  row and section; 146 mutation guards = 104 + IC-9's forty-two) and CHANGELOG (ten `v1.0.0` tags,
  eighteen contract tags), decisions, this changelog and `HANDOFF_LOG.md`.
- **Kept green.** `tests/test_datacentre_ic9_untouched.py` (its git oracle rebuilds d155e91, which is
  in this branch's history); the IC-9 schemas are byte-identical to the tagged files; no IC-15
  reference names IC-9; the DPA read waiver is still 12.

## 2026-11-26 — WP-SITE-07: IC-9 compute turn-down envelope contract frozen (`gridq.contracts.compute_turndown`)

- **New frozen contract IC-9 1.0.0 (D122 to D125 and D127).**
  - `ComputeTurndownDeclaration` is the versioned API through which a GPU operator declares optional
    turn-down envelopes: MW, duration, notice, price, SLA class and validity.
  - Its compiled UTC timeline is `ResolvedTimeline`.
  - Schemas: `ic9_compute_turndown_declaration.v1.0.0.schema.json` and
    `ic9_resolved_timeline.v1.0.0.schema.json`. Tag `contracts/ic9-compute-turndown/v1.0.0`.
  - Shape:
    - a tiered menu of envelopes (Pareto duration curves, absolute kW or basis points of running IT load);
    - a GB-clock availability template with dated overrides, whose compilation is bound by a stored
      `resolved_hash`;
    - SLA classes that own a shared call budget and say whether energy returns;
    - a structured `PriceOffer` in whole pence with a typed basis;
    - declared stacking (merit order, exclusion groups, concurrent cap) and a retained IT floor.
  - Readers include `stack_at`, `check_call`, `declared_at_local` (PEP 495 `fold=0`) and the price
    functions.
  - No firmness field, gate or stream ranking: every consumer treats IC-9 kW as CONDITIONAL (B8-03), and
    the only cross-offer rules are no-double-booking arithmetic (D57). There is no instruction message
    (ADR-D2).
- **Generated API specification.**
  - `src/gridq/contracts/specs/ic9_compute_turndown_declaration.v1.0.0.md` is rendered by the new
    `gridq.contracts._specdoc` from the module and its committed schemas.
  - `python -m gridq.contracts --check` now fails when a committed specification is not a fresh render.
- **Load model accepts it as an optional declared input (D126).**
  - `synthesise`, `DataCentreArchetype.synthesise` and `derive_data_centre_half_hourly` take
    `workload_flex=`, whose default is the sentinel `NO_ENVELOPE_DECLARED`.
  - The baseline builder never sees the declaration. It is bound beside the ensemble
    (`Synthesis.workload_flex`).
  - `gridq.datacentre.turndown` computes on request the IT, meter-instant and meter-settled turn-down for
    a caller-stated call duration, labelled conditional.
  - With a declaration, `derive` adds a binding JSON and optional overlay CSVs. The four WP-SITE-01 files
    are byte-identical.
- **Tests.**
  - `tests/contracts/test_ic9_compute_turndown.py`: 51 test functions, with six synthetic fixtures and
    golden hashes in `fixtures/ic9/`, and 42 mutants.
  - `tests/test_datacentre_ic9_untouched.py`: 15 test functions, which prove IT load is untouched with no
    declared envelope across the half-hourly, one-minute and band paths. They include a git oracle against
    WP-SITE-01's tree and seven mutants.
  - `test_seed_and_draws_have_no_default` now also pins the sentinel default.
- No archived schema, golden or DPA waiver changed.

## 2026-11-26 — Integration: WP-EDGE-01 merged into `roadmap/p1-foundations`

- **D-numbers.** WP-EDGE-01's eight placeholders took D114 to D121 (EDGE01-1 to -8 in order;
  listed newest first in `docs/decisions.md`). Its copies of WP-SIZE-07's decisions are the existing
  D85 to D89, not added twice.
- **Kept both sides** at every shared append point: `__main__.FROZEN` (the three IC-5 schemas beside
  IC-3, IC-12 and IC-15), `__init__.__all__`, the IC-5 golden table in `_contracts_support.py`, the
  IC-4 embedder set (`ic5_cloud_to_edge_frame`), the contracts README (the IC-5 row and dependency
  paragraph beside IC-12 3.0.0 and IC-15; 104 mutation guards = 84 + WP-EDGE-01's twenty), the
  contracts CHANGELOG (nine `v1.0.0` tags, seventeen contract tags) and this changelog.
- **Tag.** `contracts/ic5-edge-cloud/v1.0.0` stays on e943f13, the branch commit; it was not moved.
  The IC-5 schemas are byte-identical to the tagged files: the renumber touched only the module
  docstring and comments.
- **IC-15.** No IC-15 reference names IC-5, so nothing was bound and IC-15 stays 1.0.0.

## 2026-11-26 — WP-EDGE-01: IC-5 edge ↔ cloud protocol 1.0.0 frozen, with conformance tests and a reference simulator

- **IC-5 frozen (D114 to D121).** New leaf module `src/gridq/contracts/edge_cloud.py`, version 1.0.0.
  Schemas `ic5_cloud_to_edge_frame`, `ic5_edge_to_cloud_frame` and `ic5_link_profile` (`v1.0.0`) are listed in
  `__main__.FROZEN`, and `python -m gridq.contracts --check` covers them. Annotated tag
  `contracts/ic5-edge-cloud/v1.0.0`.
- **What it carries.**
  - Two hash-chained frame models, one per direction, bound to a per-site `LinkProfile` by hash. The receiver's
    verdicts are fixed (`ChainTracker`, `verify_frame_chain`).
  - `SetpointSchedule`: an attributed command (merchant plus service deliveries, exactly), an import cap, an
    optional chiller band and the expected outcome, checked against the IC-3 `ResolvedTimeline` it names by
    IC-3's own rule and against the staged authority (ADR-D2).
  - `ScheduleAck`: a hash-chained ack per transition; `admit_schedule`, `verify_ack_chain` and
    `governing_timeline`.
  - Heartbeats both ways and `link_state`: every threshold a required profile field, equality fail-safe.
  - 17 fail-safe trigger codes in a frozen table; `FailSafeEvent` episodes with one exit rule; `fail_safe_target`.
  - ANM edge-local by construction: `AnmEvent` travels edge to cloud only; `effective_import_cap` only
    tightens; `verify_anm_response` against IC-1 exactly.
  - The latency budget: a required `cloud_to_edge_schedule` budget, `deliver_by` on each schedule,
    `check_latency` with nearest rank and explicit clock skew.
- **Reference simulator.** New package `src/gridq/edge_reference` (stdlib, pydantic and `gridq.contracts`
  only): a relay, an edge twin and a cloud twin over stdlib HTTP on loopback
  (`python -m gridq.edge_reference --role relay|edge-twin|cloud-twin`), with a simulated clock, fault
  switches, ANM injection and a canonical transcript.
- **Acceptance.**
  - (1) `test_the_contract_version_is_tagged`, `test_the_git_tag_is_annotated_and_carries_the_frozen_schemas`,
    and the schema, fixture, golden-hash, canonical-line and version tests in `test_ic5_edge_cloud.py`.
  - (2) `test_the_schedule_latency_budget_is_a_required_contract_field`, `test_check_latency_verdicts`,
    `test_deliver_by_is_sent_at_plus_the_bound_and_the_edge_checks_it` and related tests.
  - (3) `test_a_cloud_client_and_an_edge_client_talk_through_the_relay`, plus the two twin tests, in
    `test_ic5_reference_endpoint.py`.
  - (4) `test_a_gateway_validates_ic5_from_the_json_schema_files_alone` (no GridQ, no pydantic: a stdlib
    validator, `tests/contracts/_schema_subset.py`, that fails on any keyword it does not implement) and
    `test_a_gateway_runs_ic5_on_the_contracts_package_alone` (no gridq module outside contracts; none of numpy,
    pandas, linopy, highspy, xarray or scipy).
- **Fixtures.** Three synthetic files under `tests/contracts/fixtures/ic5/`, built by `_ic5_fixtures.py`, with
  hashes in the new `GOLDEN_HASHES_IC5`. The profile's numbers (2-minute max age, 5 s p99) are the spec's
  internal targets and appear only in this `SYNTHETIC-` fixture; every profile number is required with no
  default, so no published source was needed.
- **Mutation checks.** 20 contract guards and 5 simulator guards, each red when reverted, with passing controls.
- **Other.** `test_ic4_pin_and_inventory.py` lists `ic5_cloud_to_edge_frame` as an IC-4 embedder (an
  `envelope_install` frame carries an IC-3 envelope). No archived schema was edited, no golden was re-pinned and
  the DPA waiver count is unchanged.
- **Open.** The spec's 2-minute staleness and B2-02's 5-minute MPC cadence cannot both hold (D116). No
  contracts-only distribution exists in `pyproject.toml`, and `gridq.contracts.__main__` imports
  `gridq.schemas.chassis`, so one would need to exclude or guard that file.

## 2026-11-26 — Integration: WP-OPS-01 merged into `roadmap/p1-foundations`

- **D-numbers.** WP-OPS-01's seven placeholders took D107 to D113 (OPS01-1 to -7 in order). Its copy
  of the WP-GRC-05 waiver decision is the one already numbered D80 on this branch; it was not added twice.
- **Conflicts.** Only the shared append points: `docs/decisions.md` (D113 to D107 above D106),
  this changelog and `HANDOFF_LOG.md`. `.gitignore` (`dossiers/`), the IC-4 inventory gap G9 and its
  test merged cleanly.
- **Kept green.** The ADR-D4 pin test (the Atlas file at its pinned commit, by sha256) ran and
  passed; the dossier golden hash is unchanged; the DPA read waiver is still 12. The dossier reads no
  `TariffStack`, so IC-12 3.0.0 does not touch it.

## 2026-11-26 — WP-OPS-01: the candidate site dossier (evidenced or asked, never guessed)

- **Olly** (on `roadmap/wp-ops01-dossier`, cut from `roadmap/p1-foundations` at afd7871).
- **New package `gridq.ops`.** `build_dossier` characterises every candidate site by connection type and
  IC-1 connection terms, connecting DNO, DNO licence area, metering class, net export, gensets and on-site
  generation, cooling topology, BESS owner and BESS funder. Each field is evidenced by admissible IC-4
  evidence (or, for the licence area, a cited public boundary dataset), or unknown with typed,
  de-duplicated data asks. Design: D107 to D113 (Fable 5.2, under the owner's delegation).
- **Through the DPA guard only.** Customer site records are read only after `require_dpa` releases the
  site and `check_load` releases the record, via the guarded SiteModel loader. With no DPA registered (today)
  all 8 real sites are fully unknown, every customer document ask waiting on "DPA for <customer> not
  registered (WP-GRC-05)". The read waiver is unchanged at 12.
- **No verdict.** A D-4 input map cites ADR-D4 by file hash and reports only whether each criterion's
  fields are evidenced. No score, rank or boolean at site level; a test walks the JSON and Markdown for
  verdict words.
- **Owner-side output.** `python -m gridq.ops.dossier --as-of YYYY-MM-DD` writes JSON and Markdown into
  `dossiers/` (now git-ignored) and refuses any path git does not ignore.
- **IC-4 inventory.** Gap G9 added: no evidence type for a BESS funding commitment (owner WP-BO-02).

## 2026-11-26 — Integration: WP-UW-07 merged into `roadmap/p1-foundations`; IC-12 3.0.0 rendered on 2.1.0

- **D-numbers.** WP-UW-07's four placeholders took D103 to D106 (UW07-1 to -4 in order).
- **IC-12 3.0.0 over 2.1.0.** WP-UW-07 was cut before IC-12 2.1.0 (WP-UW-03, D79). Its 3.0.0 was
  rendered again from the merged models, so `QuoteRecord` 3.0.0 carries every 2.1.0 member (lender
  terms, DSCR status) plus the two reactive bill members. The three `v2.1.0` files are archived at
  their hashes under `contracts/ic12-chassis/v2.1.0`; the `v2.0.0` archive is kept.
  `SUPPORTED_VERSIONS` is `("1.0.0", "2.0.0", "2.1.0", "3.0.0")`; `upgrade_site_2_to_3` lifts a 2.0.0
  or a 2.1.0 site (one site schema, byte for byte). The `OptimisationResult` 3.0.0 file is the 2.1.0
  file byte for byte. `python -m gridq.contracts --check`: 31 ok (15 live, 16 archived). Annotated tag
  `contracts/ic12-chassis/v3.0.0` on the merge commit.
- **Identity pins.** The 2.0.0 pins hold, unedited, for every site with its basis stripped. The five
  WP-GRC-05 synthetic identity sites gained the synthetic test basis (VAT excluded, stated by the
  record, every component `assumed`; network `not_charged` on the private-wire one) and their 3.0.0
  pins were minted at the merge. The campaign records' 3.0.0 pins are WP-UW-07's and run only
  through the DPA guard. The upgrader is now checked on every pinned site.
- **DPA guard kept green, waiver unchanged (12).** WP-UW-07 edited ten quarantined site records
  (nine `examples/real/sites/*.json` and the Robert Thomas Farms site model) to state their tariff
  basis. The manifest's dataset hashes were regenerated with `python -m gridq.governance.dpa_scan
  --write-manifest`: ten `sha256` values changed and nothing else (count, statuses, waiver). The demo
  `sitemodel.json` gained the synthetic basis, so its `DEMO_ATTESTATION` pin was re-verified (exactly
  the two members added; the HH series unchanged) and re-pinned. WP-UW-07's two new tests that read
  every site record (`test_tariff_basis.py`, `test_tariff_provenance_sentences.py`) now take the
  campaign records from the manifest and read them through `_dpa_support.dpa_released`, each case
  skipping with its blocker until the DPA is registered.
- **IC-3 and IC-15.** IC-15 names no IC-12 version. IC-3 1.0.0 binds a quote at IC-12 `2.x`, so
  binding a quote written at 3.0.0 needs an IC-3 minor; no producer writes an envelope yet
  (WP-SIZE-08). Stated in the contracts README and CHANGELOG; IC-3 is not re-versioned here.
- **Pipeline.** The quote pipeline prices the net-of-VAT site (WP-UW-07) and still passes the lender
  terms (WP-UW-03) to the financial model and the scenario sensitivities.
- **Tests from the earlier merges.** Five test files that WP-UW-07 never saw build a `TariffStack`
  inline (`test_assumptions_gb_v1_11`, `test_duration_term_on_consented_study_rates`,
  `test_lender_terms`, `test_one_warranty_budget_and_the_netting_proof`, `test_quote_disclosures`).
  Each now states the synthetic test basis from `tests/_tariff_support.py`, as WP-UW-07's 38
  migrated files do. B001's call-site citations move with the merged lines (`app/app.py:1211`,
  `app/app.py:1317`, `quotes/pipeline.py:571`).

## 2026-11-26 — WP-UW-07: tariff model for LV, HV and EHV (`hv_tariff_model`); IC-12 chassis 3.0.0

- **Every TariffStack states its basis and provenance (D103/D104).** `TariffStack.basis` (`TariffBasis`:
  VAT included/excluded and its rate, how the VAT basis is known, levies and network charges itemised / in the
  unit rates / not charged) and `TariffStack.provenance` (`TariffProvenance`, per component) are required. A
  stack without them is refused. Every real site is `excluded`/`derived` under H-01, with the
  `DerivedAssumption` on `tariff_stack.basis.vat`. A VAT-inclusive stack is priced net of VAT once, in
  `quotes/pipeline.py`, and the record keeps the site as supplied. All committed carriers and 38 test files
  migrated.
- **Each band priced as itself; no mean of bands (D105).** `tariff_calendar.energy_price_envelope`:
  - a resolved half-hour is priced at its own band on both sides;
  - an unresolved one charges at the highest quoted rate and credits at the lowest;
  - the mean-of-bands fallback is deleted;
  - peak/off-peak is no longer mapped onto DUoS red.
  The MILP builds a two-price energy term only when a stack is not spread-neutral, so a full red/amber/green
  stack builds the same model as before. A stack may carry its own `import_rate_windows`.
- **Published CDCM/EDCM rows (D105).**
  - `TariffStack.network_tariff: CdcmTariff | EdcmTariff` holds the statement's pence as `Decimal`.
  - New pure package `gridq.tariffs` holds the published CDCM algebra, the half-hourly CDCM and EDCM bills, the
    LC14 exceeded-capacity and chargeable-kVArh rules, and H-02 avoidability per charge line.
  - ENWL's 2026/27 LV and HV Site Specific Band 1 "Net revenue summary" reproduces to the penny.
  - Sources are recorded with URL, date and SHA-256 in `data/fetched/dno_charging/`; extracts are in
    `tests/fixtures/dno_charging/`.
- **Reactive power modelled (D106).** On a file metering both reactive channels, the MILP carries the
  inverter's kVAr compensation inside an inscribed P-Q octagon and holds the connection in kVA. With a
  published CDCM excess reactive rate, it prices the LC14 excess in the objective. The bill carries
  `reactive_before_gbp`/`reactive_after_gbp`. The unity-power-factor warning is retired on exactly those runs.
- **R8 closed (D104).** The no-spec bill basis no longer says "priced on its own tariff stack"
  unconditionally. `tariff_provenance_phrase` is the one producer of that phrase, and the solved bill, the
  BEFORE-only bill and the consulting report read it.
- **IC-12 chassis 3.0.0 (`gridq_chassis_v3`).**
  - The v2.0.0 schemas are archived by SHA-256.
  - `chassis.upgrade_site_2_to_3` requires the basis and provenance as arguments.
  - Each committed site, with exactly its basis, provenance and VAT record stripped, hashes to its 2.0.0 pin.
  - The 3.0.0 bytes are pinned beside it.
  - The tag is deferred to integration over 2.1.0 (contracts CHANGELOG).
- **Golden pins.** No priced pin in `tests/test_determinism_golden.py` moved, so none was re-minted. The demo
  stack is a full red/amber/green stack and is spread-neutral.
- **Findings closed.** `docs/reviews/2026-11-16-findings-register-triage.md`: B-17, JSON-F-38 ≡ F-41,
  JSON-F-39, MD-F-18 and R8 move to C, each with its call site and guarding test.
- **Mutation checks.** Eight mutants (envelope to mean, amber at the red rate twice, optional basis, zero
  reactive charge, provenance always own, the old R8 sentence, pricing the VAT-inclusive site) are all killed.
- **Not met, stated.** Acceptance 1 (a published EDCM example to the penny) is unmet: data request
  DR-UW07-EDCM. ENWL publishes its EDCM model blank "to ensure customer data is kept confidential", and no
  DNO or DCUSA document found carries a worked EDCM bill. The EDCM tests use constructed inputs, are labelled
  as such, and are not claimed against it.
- **Pre-existing, not caused here.** `tests/test_windowed_levy_capacity_market.py` has 2 failures. They were
  reproduced on the untouched base `cd064f7`: the latest release's DFS prices make the flat test stack cycle.

## 2026-11-26 — Integration: WP-SITE-01, WP-SIZE-07 and WP-UW-01 on `roadmap/p1-foundations`; D80 to D102

- **Olly** (merges of `roadmap/wp-site01-dcload`, `roadmap/wp-size07-ic3` and `roadmap/wp-uw01-basis`, in that
  order). Docs conflicts kept both sides; decisions and changelog entries newest first, handoffs in time order.
- **Real D-numbers, in merge order.** WP-GRC-05's placeholder became D80; WP-SITE-01's four became D81 to D84; WP-SIZE-07's
  five became D85 to D89; WP-UW-01's thirteen became D90 to D102. Every reference moved: decisions, changelogs,
  handoffs, docstrings, comments, tests and the underwriting rule table's text (so its `RULE_TABLE_HASH` moved;
  no run or test pins it, and its version stays 1.0.0).
- **WP-UW-01 meets IC-4 1.2.0.** Its tests labelled every synthetic IC-4 fixture 1.1.0, and the G6 test makes
  one of every type, which on this branch includes the five 1.2.0 types (WP-GRC-09), so two tests failed on
  the down-level rule. The fixtures are now labelled with the live IC-4 version. The G6 data ask and gate
  finding name the live version ("IC-4 1.2.0 has no evidence type for ..."), which is still true.
- **IC-15 binds IC-3** (entry below). **CLAUDE.md §2.0** now says `underwriting_basis_v1` is the plug-in
  `gridq.underwriting` (D90 to D102), not yet wired into `gridq run` or the app (WP-UW-04).
- No golden re-pinned, no archived schema edited, the DPA waiver unchanged at 12 modules.

## 2026-11-26 — WP-UW-01: `underwriting_basis_v1`, the evidence-gated study basis (plug-in `gridq.underwriting`)

Attributed to Olly. Design decisions D90 to D102 (`docs/decisions.md`) were taken by Fable 5.2 under the
owner's delegation.

- **The basis (D96 to D99).** `gridq.underwriting.basis.UnderwritingBasis` meets the D74 `NamedBasis`
  contract and is registered beside `all_streams_enabled_study_v1` through
  `gridq.extensions.load_plugin("gridq.underwriting")`. It has required fields `authorised_by`, `as_of` (the date
  evidence is assessed on, with no wall-clock default) and `evidence_admission` (`registered_only`, or
  `synthetic_fixtures_test_only`, which labels every artefact "SYNTHETIC TEST FIXTURES — NOT EVIDENCE"). It also
  carries the rule table's version and hash and a LOW confidence ceiling. Every field is in `input_hash`, and so is
  `study_basis_extension: {owner: "gridq.underwriting", version: "1.0.0"}`.
- **The rule table (D92 to D95).** `gridq.underwriting.rules` (version 1.0.0, content-hashed) names, per
  stream, the IC-4 evidence objects it needs: registration, route-to-market agreement, customer consent, export
  permission, contract. It also holds the quoted size minimum with its aggregation route, and five stacking rules.
  Each row says whether it is a published rule, the product spec's route table or underwriting policy. The twelve
  published rules are quoted from documents retrieved on 2026-11-26 (`gridq.underwriting.sources`: NESO Wider
  Access to the BM guidance, Dynamic Response Services guidance, DFR/BM stacking note, DFS Procurement Rules v5.0
  and DFS Stacking List v2; SI 2014/2043 reg 15(4)). Bill-side streams carry no evidence gate.
- **The gate (D90, D91, D101).** `gridq.underwriting.gate` wraps every stream of the default registry so its
  gate can only tighten. A market stream enters the priced floor only when every object its row requires is
  referenced by the site (`SiteModel.evidence_refs`), held, VALID on `as_of` and verified. Otherwise it is
  CONDITIONAL upside with the unlock named, or, where IC-4 has no type for the document (DFS and Dynamic Response
  unit registrations, gap G6), UNKNOWN at £0 with a data ask. The default valuation's notes are kept verbatim below
  the line `DEFAULT-BASIS GATE NOTES, NOT ADOPTED ON underwriting_basis_v1:`.
- **The engine (D90, D100, D102).** `gridq.underwriting.engine.UnderwritingEngine` hands the gated registry to
  a plain `MilpEngine`, so every sizing candidate and the final valuation are gated. Its post-pass:
  - holds out of the floor any stream the evidence does not admit;
  - demotes, whole, a floor holding two or more services that share the inverter budget (until WP-SIZE-03);
  - stamps `OptimisationResult.evidence_refs` with the objects each admitted stream rests on;
  - leads the record with the basis note and holds it NOT_BANKABLE.
  `generate_quote(site, lib, engine=UnderwritingEngine(basis, evidence))` is the entry; nothing in `cli` or `app`
  calls it yet (WP-UW-04).
- **Tests.** `tests/test_underwriting_basis_v1.py`, 56 tests in about 2 s:
  - Criterion 1: `test_acceptance_1_bm_without_its_evidence_is_not_priced`,
    `test_acceptance_1_through_generate_quote`, and the remove-each-object, held-but-inadmissible and G6 cases.
  - Criterion 2: `test_acceptance_2_the_bases_never_share_a_run_id`.
  - Criterion 3: `test_acceptance_3_the_plugin_moves_no_academic_identity_or_default_gate`.

  No golden was re-pinned. A real MILP run (about 2 minutes, conftest demo site, `gb_v1_12`, no evidence) was
  checked once by hand and kept out of the suite. Its inner engine read the gated registry, the record led with
  the label, the band was LOW, and no market stream was in the floor.
- **Not done, stated.**
  - The dispatch objective still co-optimises DFS ungated (WP-SIZE-03; the basis note says so).
  - The asset-level connection/G99/G100 gate is WP-UW-02.
  - An independent-verifier tier is WP-UW-05.
  - `cli` and `app` wiring is WP-UW-04.
  - No CORE path was edited. CORE's confidence factor prints the D46 export-tariff reason for this basis's LOW cap
    (defect logged, with a request for a per-basis reason hook).
  - Data asks: IC-4 types for DFS and Dynamic Response unit registration; the local-flex and P415 minimums; NESO on
    DFS × DC/DM/DR stacking; the BMU type on a `bmu_registration`; a G99 source; owner confirmation of the policy
    rows.

## 2026-11-26 — Integration: IC-15 binds IC-3 1.0.0 (template set 0.2.0)

- **Olly** (on `roadmap/p1-foundations`, after merging WP-SIZE-07).
- **IC-15's IC-3 reference is bound.** WP-GRC-09 left `ic3_dispatch_envelope` `not_yet_frozen`
  pending WP-SIZE-07. It is now `frozen`, minimum 1.0.0, tag `contracts/ic3-dispatch-envelope/v1.0.0`,
  and `live_contract_versions()` lists IC-3. The six template lines that cited it lose "(binding
  unavailable until WP-SIZE-07)". The five templates concerned go to version 0.2.0, and the set to
  0.2.0. IC-8 stays pending (WP-LEDGER-07).
- **No schema bump.** Only manifest data and template text changed; both IC-15 schemas are
  unchanged and `python -m gridq.contracts --check` is clean with IC-15 at 1.0.0.
- **Fixtures.** The synthetic reliance letter now binds the IC-3 hybrid BESS fixture by content
  hash; that envelope binds the same IC-1 and IC-2 fixtures as the letter. The new hashes are in
  `GOLDEN_SET_0_2_0`. The set 0.1.0 hashes in `GOLDEN` are unchanged and checked against the
  IC-15 tag. The not-yet-frozen guard is now shown on the set with IC-3 pending again, because no
  field binds IC-8.
- **Contracts CHANGELOG.** It now counts eight `v1.0.0` tags and fifteen contract tags in all, since
  IC-3's tag is on this branch.

## 2026-11-26 — WP-SIZE-07: IC-3 `DispatchEnvelope` 1.0.0 frozen, with conformance tests and synthetic fixtures

- **IC-3 frozen (D85-D89).** New leaf module `src/gridq/contracts/dispatch_envelope.py`:
  `DispatchEnvelope` and its compiled `ResolvedTimeline`, version 1.0.0. Schemas
  `ic3_dispatch_envelope.v1.0.0.schema.json` and `ic3_resolved_timeline.v1.0.0.schema.json` are listed in
  `__main__.FROZEN`, and `python -m gridq.contracts --check` covers them. Annotated tag
  `contracts/ic3-dispatch-envelope/v1.0.0`.
- **What it carries.** Per cell of a GB-clock template, with dated overrides on a UTC half-hour grid:
  - an exact partition of the discharge and charge ratings into resilience, one slice per committed service,
    merchant and a withheld tranche;
  - an exact energy stack: resilience floor, held energy, merchant, held headroom, withheld;
  - the import and export limits and their firmness;
  - thermal terms against the IC-11 surface.
  It also carries the fail-safe, the half-open validity and `supersedes`. `source` is required: the
  `input_hash`, the run pins, the IC-12 quote, the study or underwriting basis label, and the IC-1, IC-2 and
  constraint-library references.
- **Consumer API in the leaf.** It needs no sizer:
  - `resolve`, `terms_at` and `timeline_matches`;
  - `flat_limits`, `merchant_headroom`, `reserved_kw`, `offered_kw`, `service_windows` and
    `underwritten_total`;
  - `check_schedule`, `check_against_surface`, `check_against_agreement`, `check_source` and
    `check_constraint_library`;
  - `active_at` and `envelope_ref`.
- **Acceptance.**
  - (1) `test_the_contract_version_is_tagged` and `test_the_git_tag_is_annotated_and_carries_the_frozen_schemas`
    (it reads the tag from git and compares the schemas at the tag; a checkout without the tag skips it), plus
    the schema, fixture and version tests.
  - (2) `test_an_envelope_without_a_source_input_hash_is_invalid` and five related tests.
  - (3) `test_a_consumer_runs_on_the_fixtures_without_the_sizer`. It runs in a fresh interpreter and asserts
    that no `gridq` module outside `gridq.contracts` was imported, and none of numpy, pandas, linopy,
    highspy, xarray or scipy.
- **Fixtures.** Five synthetic files under `tests/contracts/fixtures/ic3/`, all from `tests/contracts/_ic3_fixtures.py`:
  - a hybrid BESS with a maintenance override;
  - its successor;
  - a data-centre envelope against the IC-11 chilled-water fixture;
  - a clock-change week;
  - that week's compiled timeline.
  Each is labelled synthetic (`SYNTHETIC-` site ids, the `synthetic_fixture` record class, placeholder hashes
  that name what they stand for), and `test_the_committed_fixtures_equal_a_fresh_build` rebuilds them. Their
  hashes are pinned in a new table, `GOLDEN_HASHES_IC3`. No existing golden was re-pinned and no archived
  schema changed.
- **D57.** Nothing refuses a service on its basis, stream, size or value; no double-booking is the closure
  sum. Guarded by `test_the_basis_of_a_service_never_gates_its_partition`.
- **Tests.**
  - `tests/contracts/test_ic3_dispatch_envelope.py` has 227 tests. Among them: an independent-oracle check at
    every half-hour of every fixture; the 23- and 25-hour days; and 18 mutation guards, each turned red by
    reverting it.
  - `tests/contracts/test_canonical_hash_ic3.py` has 37 tests.
  - `test_ic4_pin_and_inventory.py` now lists IC-3 as an IC-4 embedder.
- **Not done, stated.**
  - No producer writes an envelope; the sizer-side exporter is WP-SIZE-08.
  - IC-15 does not yet bind one by hash (WP-GRC-09).
  - The IC-1 time-of-day profile is not checked, because its periods 1-48 do not define the 46- and
    50-period days.
  - The constraint library has no content schema, only a pinned version and hash (WP-SIZE-02).
  - Rebound kW is not modelled, and turn-up and pre-cool are not either (WP-THERM-12).
  - The `gridq.extensions.resources` registry is not read; multi-asset envelopes are out of v1.
  - There is no underwriting-basis fixture, because `underwriting_basis_v1` is not implemented.

## 2026-11-26 — WP-SITE-01: data-centre load and PUE synthesiser (`gridq.datacentre`)

- **New plug-in package `gridq.datacentre` (D81 to D84).**
  - It synthesises a pre-operational data-centre load series at half-hourly and one-minute resolution, with
    uncertainty bands.
  - Inputs: IT ramp schedule (racks × rated kW per rack, energisation dates, fill and slip), within-day
    utilisation, UPS (curve or constant efficiency, N, N+1 or 2N), distribution loss, auxiliaries, cooling
    (WP-THERM-04 §6 forms C1, C2 and C3, free-cooling regimes at T*, coefficients or duty points), variability,
    and an ambient input series.
  - PUE is an output. No weather is fetched.
- **Registered through the chassis, not CORE.** `data_centre` is a `DataCentreArchetype(ReferenceProfile)`
  added by `load_plugin("gridq.datacentre.plugin")`.
  - It has no day shape, so CORE's scale-to-total route refuses it.
  - It is selected by `gridq.datacentre.derive.derive_data_centre_half_hourly`, which:
    - writes CORE's `timestamp,import_kwh` CSV plus band, member and manifest sidecars;
    - stamps `half_hourly_basis="modelled"` and the reference profile;
    - appends a MEDIUM-ceiling `DerivedAssumption`.
  - Nothing is registered on import. No CORE path and no `pyproject.toml` line names the plug-in.
- **Uncertainty.**
  - Sampling is a seeded two-layer Latin hypercube (PCG64 raw uniforms, the package's own inverse
    transforms), with the draw count required and at least 72.
  - Bands are pointwise P05, P10, P50, P90 and P95, each edge with a 95 % order-statistic interval.
  - The "expected" series is the mean, never P50.
  - Aggregates are computed per draw, and variance attribution is reported as a ranked data request.
  - The series hash covers the outputs and a canonical manifest.
- **One minute.** An AR(1) is conditioned exactly on each half-hour's mean, with optional dips. Each day is
  keyed for random access.
  - Facility power at one minute is the exact model re-centred per block, so every block averages to its
    half-hourly value.
  - The Jensen gap is emitted, and flagged only against a user threshold.
- **Data rule.** No input field has a numeric default. The only numbers in the package are two opt-in presets
  quoted from Shehabi et al. 2024 (LBNL-2001637): Table 4.3, the UPS efficiency range, US-derived; and
  Table 3.1, per-node SD/Pavg, labelled `node_level_upper_bound`. No ASHRAE figure is used.
- **Tests.** 48 new tests:
  - `tests/test_datacentre_registration.py` (8)
  - `tests/test_datacentre_series.py` (15)
  - `tests/test_datacentre_reproducibility.py` (5)
  - `tests/test_datacentre_model.py` (13)
  - `tests/test_datacentre_data_rule.py` (7)
  - support module `tests/_datacentre_support.py`
- **Not done, stated.**
  - The bands are uncalibrated until the spec 16.2 post-energisation back-test.
  - No production caller reads the one-minute series yet (WP-SITE-04).
  - The Atlas ERA5 ambient series is pending.
  - No assumption release changed, and no golden moved.

## 2026-11-26 — WP-GRC-05: the read waiver back to 12, and no test outside it reads customer data

- **Olly** (on `roadmap/p1-foundations`; this reverses the waiver growth in the integration entry below).
- **Waiver 14 → 12.** The waiver may only shrink, and only the owner can widen a data gate. The manifest's
  `legacy_validation_waiver.modules` is back to its original 12, and so is `PINNED_WAIVER_COUNT`.
- **The two modules, without it.** `test_ic12_chassis` checks identity on the public examples plus five
  labelled SYNTHETIC sites (`tests/fixtures/ic12_sites/`), minted from the bfeafe4 models like the
  originals. Its nine campaign-record pins are unchanged and now DPA-gated. `test_local_flex_gross_label`
  T15 and T17, and the D61 and D-130 tests, price a SYNTHETIC SSEN SEPD award.
- **The guard, tightened.** `tests/governance/test_no_direct_quarantined_reads.py` fails any test outside
  the waiver that opens a path under a customer-data root. At the merge it would have flagged 13 modules.
  Six moved to synthetic sites, including the three local-flex modules above. Seven read through
  `tests/_dpa_support.dpa_released*` and skip, naming their blocker, until the DPA is registered
  (D80). The fast tier: 4641 passed, 94 skipped (82 of them these quarantine skips), 0 failed.
- **Docs.** The contracts `CHANGELOG.md` tag count is corrected, and this file is ordered newest first.

## 2026-11-26 — Integration: the DPA guard (WP-GRC-05) meets WP-CORE-03, WP-UW-03 and WP-GRC-09

- **Olly** (integration of `roadmap/p1-foundations` into `roadmap/wp-grc05-dpa-guard`, then fast-forwarded onto
  `roadmap/p1-foundations`).
- **Code.** `/api/run` and `/run/real` keep the transient workspace and parse the lender-terms block first;
  `gridq run` takes both `--evidence-dir` and `--lender-terms`. The guard is unchanged.
- **Tests brought in by the merge, held to the guard.** `test_dscr_on_every_run`'s CLI runs register a TEST
  FIXTURE DPA for the fictitious conftest site (`tests/_dpa_support.py`), and its app stubs carry the quote's
  `site_id`, which the app now reads to decide store or show once. Two modules read quarantined files
  read-only and join the legacy validation waiver: `tests/contracts/test_ic12_chassis.py` (the identity of
  every real example site) and `tests/test_local_flex_gross_label.py` (T15 on Harwell). The waiver pin goes
  from 12 to 14. **This is for the owner to decide**: the list was meant only to shrink.
- **Other.** B001's call-site citations move with the merged lines (`cli.py:388`, `app/app.py:1141` and
  `:1247`, `quotes/pipeline.py:548`). `test_the_scan_module_runs_as_a_script` now runs the scan from this
  checkout's `src` (it had imported whichever checkout the interpreter's editable install names).

## 2026-11-26 — IC-12 chassis 2.1.0: WP-UW-03's `FinancialCase` additions released as an additive minor (D79)

- **Olly** (integration of WP-UW-03 with WP-CORE-03, branch `roadmap/wp-uw03-dscr`, then fast-forwarded onto
  `roadmap/p1-foundations`).
- **Why.** After merging the IC-12 2.0.0 freeze into WP-UW-03 (`30acc23`), `python -m gridq.contracts --check`
  reported `QuoteRecord` drifting from `ic12_quote_record.v2.0.0.schema.json`: `QuoteRecord` embeds
  `FinancialCase`, which D75 to D77 grew. Two tests failed on that one cause.
- **What.** IC-12 is now 2.1.0 (`schemas/chassis.py`), tag `contracts/ic12-chassis/v2.1.0`. The three
  `v2.0.0` schema files are archived by SHA-256 at their tagged hashes; the `v2.1.0` files are rendered.
  `SiteModel` and `OptimisationResult` are byte-identical to 2.0.0; `QuoteRecord` gains only optional,
  `null`-defaulted members and eight definitions. `tests/contracts/test_ic12_chassis.py` pins the delta
  (`test_2_1_0_is_2_0_0_plus_the_stated_optional_members`). `test_schema_delta_v110` now reads only `v1.0.0`
  archive entries. No golden re-pinned, no archived file edited, no identity hash moved.

## 2026-11-26 — IC-4 EvidenceObject 1.2.0; IC-15 contract template set 1.0.0 (`gridq.contracts`)

- **Olly** (roadmap WP-GRC-09, NEW-IC-15; plan `docs/shared_work/wp_grc09_plan.md`; decision by
  Fable 5.2 under DEL-2026-11-25-1). No priced number moves. Nothing calls the new functions from
  pricing, eligibility or a revenue stream. Nothing outside `src/gridq/contracts/`,
  `tests/contracts/`, the plan, this entry and the HANDOFF_LOG changed. IC-1, IC-2, IC-11 and
  IC-13 are unchanged. The IC-4 1.0.0 and 1.1.0 schemas are byte-identical to their tags.
- **IC-4 1.2.0**: five contract evidence types are appended (assessment SOW, MSA, site access and
  installation agreement, lender reliance letter, performance wrapper agreement). No role is added,
  so IC-4 alone is re-versioned (WP-BO-06's cost table). R7 keeps a 1.1.0 label honest.
- **IC-15 1.0.0** (`contract_templates.py`, `templates/`): seven contract templates.
  - **Every template is a draft that needs legal review**, marked so in its text and its manifest
    entry. None is presented as reviewed.
  - Each template names the IC-4 type its signed copy is registered as, and its required fields,
    each with an ask.
  - The MSA carries the safety hierarchy, the IT-outage carve-out, step-in, the liability cap,
    service levels, termination, control-authority staging and the responsibility boundary as named
    clauses.
  - IC-3 and IC-8 are referenced by contract version, never by prose, and stay unavailable until
    WP-SIZE-07 and WP-LEDGER-07 freeze them.
  - Only ADR-D1's four list prices are on record. Every other term is to be agreed. No party is
    named.
  - The wrapper's insurer terms are unavailable until WP-GRC-13.
- `IC4_INVENTORY.md`: G2 is closed and G8 is added (no MGA or floor-provider role; owner
  WP-GRC-13). 27 new mutation guards, 66 in all.
- Person-only steps, listed in the manifest and the plan §4: legal review of all seven templates
  (the MSA and the wrapper first), engaging counsel, the risk carrier and the FCA perimeter
  (XG-20), agreeing commercial terms, signing, the critical-facilities sign-off (XG-11), the
  load-control licence (XG-17), and registering NEW-IC-15 in the roadmap.

## 2026-11-26 — DPA guard: no customer dataset stored or loaded without DPA evidence (WP-GRC-05)

- **Olly** (roadmap WP-GRC-05, spec `atlas/roadmap/governance/data_protection/gridq_dpa_guard_spec.md`).
  Merges WP-BO-06 first: the guard keys on IC-4 1.1.0 `data_processing_agreement`, issuer role
  `customer`, `registered`, verified, and `assess()` VALID on the day (one object per site).
- **`gridq.governance.dpa_guard`**: `authorise_store` (store gate, returns a `DpaPermit`; the
  curated demo is exempt only by sha256 attestation, `SYNTHETIC-` ids only off the web path),
  `check_load` (load gate: transient, run store by `dpa_evidence.json`, manifest by path then by
  hash, customer roots), the registry from `GRIDQ_EVIDENCE_DIR` (default `<repo>/evidence`,
  git-ignored; fails closed) and a transient workspace for uploads and modelled profiles
  (content-addressed, flock-held, deleted by its last holder at job end).
- **Call sites**: `load_half_hourly_csv`, `load_site_model_json_with_report`, `save_run` (now a
  required `dpa=` permit) and `load_run`, the web intake, `gridq run` and `gridq render` (exit 3,
  nothing written), `generate_quote`'s invoice evidence, Gate 3 read and opt-in derivation,
  `gb_streams`' local-flex minimum-load read, the portfolio register and the Haywood Oaks
  prefill. Every one has a test that fails when the call is removed
  (`tests/governance/test_dpa_call_sites.py`).
- **Web (owner decision (a), 2026-11-23)**: a run without a DPA is shown once and not stored.
  It is held in process memory for 15 minutes, bound to the visitor, with a "Not saved" banner.
  Anonymous visitors never store. Deviations for the owner: `DpaPermit` instead of the spec's
  `EvidenceCheck | "demo"` union; transient files are deleted at job end rather than request end;
  the 15-minute show-once window.
- **Quarantine (criterion 3, files in git)**: `src/gridq/governance/customer_data_manifest.json`
  lists 78 tracked customer datasets (data/sites 19, deliverables 35, examples/real 22,
  graphify-out/converted 2), all `quarantined`: the guard refuses to load them. Nothing deleted,
  no history rewritten. `python -m gridq.governance.dpa_scan` lists them (paths only, exit 1).
  A ratchet test pins the count at 78 and fails on any unlisted, changed or copied file.
  12 legacy validation test modules may still read them, read-only, through `tests/conftest.py`
  only; that list may only shrink. `scripts/hooks/pre-commit-dpa-scan` ships, not installed.
- **Tests that store now carry a TEST FIXTURE DPA** for a fictitious site
  (`tests/_dpa_support.py`, which refuses real site ids); web tests read the show-once run.
- `gridq run examples/real/sites/<site>.json` now exits 3 until that site's DPA is registered.

## 2026-11-26 — WP-UW-03: DSCR on every run and the lender-terms object (D75 to D78; B001 closed)

- **Olly** (roadmap WP-UW-03, spec A10-03). Branch `roadmap/wp-uw03-dscr`. Design calls by Fable 5.2 under
  the owner's delegation are marked as such in `docs/decisions.md`.
- **`LenderTerms`** (`06b4137`, D75). A typed, frozen lender-terms input in its own module
  (`schemas/lender_terms.py`): tenor, all-in rate, maximum gearing, amortisation (annuity, straight-line,
  sculpted), cash-flow case (P50, P90, LENDER), covenant (default and lock-up), test period and provenance
  (`term_sheet`, `lender_indication`, `synthetic`). Every core figure is required; none is defaulted, and no
  library value stands in for a lender's term.
- **DSCR status on every case** (`06b4137`, D75). `DscrSummary` gains `status`, `unavailable_reason` and
  `cashflow_case`. A run without terms reads "No lender terms supplied: …", never a silent `None` and never
  a default number. B001 closed (register row names the call sites and guards).
- **Case, shape and size** (`06b4137`, D77). The terms choose the measured case; straight-line amortisation
  added; a sculpting target sizes the loan as min(capex × gearing, PV(CFADS ÷ target)), recorded as
  `DebtAssumptions.principal_gbp` and `sized_by`. H-03 advanced (partial).
- **Covenant tiers** (`06b4137`, D76). Default and lock-up thresholds come only from the terms, with
  `CovenantStatus.source`, `lock_up_dscr`, `lock_up_passes` and `not_evaluated_reason`; terms with no
  covenant are not evaluated rather than tested against the library placeholder. A semi-annual or quarterly
  test is refused with its reason, not annualised. H-05 advanced (partial).
- **Identity** (`06b4137`, D78). Debt terms, lender terms and price structure enter `case_id` (N25 closed).
  The derived echo fields are excluded from the quote `input_hash` and `lender_terms` is popped when `None`,
  so every run without terms keeps its identity. No golden pin moved.
- **Entry points** (`2892dc6`). `gridq run --lender-terms FILE` (JSON or YAML; an invalid file exits 2
  before solving), and the CLI summary gains "min DSCR" and "lender terms" rows. The app wizard and real
  form gain an optional lender-terms block with nothing preselected (all blank means none; a half-filled
  block is a 400). `generate_quote(lender_terms=)` threads the terms to the base case and the sensitivities.
  The finance panel, underwriting pack and HTML report print the record's reason, the lock-up tier, the
  covenant source and a "Synthetic lender terms, not a lender's offer" lead for synthetic terms.
- **DIRECTION.** No figure moves on any run without lender terms. A run with terms gets a measured DSCR (or
  a stated refusal) and its own `quote_id`.
- **Tests.** New: `tests/test_lender_terms.py` and `tests/test_dscr_on_every_run.py`, synthetic terms only
  (`tests/fixtures/lender_terms.synthetic.json`). Mutation-checked red: each named call site in the B001
  row, the three `case_id` payload lines and the covenant-tier line.
- **Open.** Real lender terms per site are a data ask. The sizer (`MilpEngine`) does not see the terms or
  the covenant (WP-UW-09); sub-annual covenant tests need sub-annual CFADS (WP-UW-09); the demo keeps its
  illustrative `DebtTerms`.

## 2026-11-26 — WP-CORE-03: chassis frozen as IC-12 2.0.0 (`gridq_chassis_v2`), with extension registries

- **IC-12 named and frozen (D71).** `SiteModel`, `OptimisationResult` and `QuoteRecord` are IC-12
  (`src/gridq/schemas/chassis.py`). Six schema files under `src/gridq/contracts/schemas/`: the three `v1.0.0`
  (the models at `gridq_clean_baseline`, archived by SHA-256) and the three `v2.0.0`.
  `python -m gridq.contracts --check` covers them. Tags `contracts/ic12-chassis/v1.0.0` and `v2.0.0`.
- **Typed references (D72).** `ConnectionDetails.agreement` and `QuoteRecord.connection_agreement` carry an IC-1
  `ConnectionAgreementRef`; `evidence_refs` on all three models carries IC-4 `EvidenceLink`s. The free-text
  `anm_or_curtailment_notes` stays as commentary. Unset members are absent from every dump, so no existing
  `input_hash`, `quote_id`, `case_id` or golden moved; none was re-pinned.
- **Extension registries (D73).** New package `gridq.extensions`: `load_archetypes`, `study_bases`, `resources`,
  `constraints`. A plug-in registers from its own package through the entry-point group `gridq.extensions` or
  `load_plugin`. Wired at `ingestion/hh_synthesis._resolve_profile`, `quotes/assembly._basis_registration` and
  `dispatch/contract.dispatch_constraints_for(extension_constraints=...)`.
- **Study basis (D74).** Contract `NamedBasis`; an unregistered basis is refused at assembly; a plug-in basis
  also pins its package and version in `input_hash`. Built-in bases hash as before.
- **Tests.** `tests/contracts/test_ic12_chassis.py`, `tests/test_chassis_extension_registries.py`, dummy package
  `tests/plugins/dummy_chassis_plugin`.
- **Not done, stated.** No production caller passes `extension_constraints`, and the MILP does not enforce an
  extension descriptor (WP-SIZE-02). No engine reads `resources` (WP-SIZE-07). No engine sets the result's two
  new fields and no site file names an agreement (WP-CONN-06). No assumption release changed.

## 2026-11-26 — WP-GRC-14: knowledge_base index; changelog entries backfilled for five assumption releases

- **`knowledge_base/` added (WP-GRC-14).** `knowledge_base/index.json` is generated by
  `python -m gridq.dev.knowledge_base build` from the assumption registry (`assumptions/gb_v*.yaml`),
  this changelog, the Atlas ADR snapshot (`knowledge_base/adr_index.json`) and the hand-kept links in
  `knowledge_base/links.yaml`. `tests/test_knowledge_base.py` fails if any release has no changelog link,
  its changelog section does not name the release, it names an ADR that does not exist, or the index is
  stale. Meeting capture is `knowledge_base/meetings/` plus a template; no meeting content was written.
- **Backfill.** Five releases had no changelog section that names them. The sections below quote each
  release's own header comment, unaltered; nothing here is new content about the release.
- **`gb_v0_1`** (2026-06-17). Quoted from `assumptions/gb_v0_1.yaml`: "EVERY value here is a PLACEHOLDER until
  reviewed and confirmed. Each entry carries its own status/confidence/source/owner. No module may hold these
  numbers privately - they are loaded via gridq.assumptions.load_assumptions("gb_v0_1")."
- **`gb_v1_7`** (2026-08-12). Quoted from `assumptions/gb_v1_7.yaml`: "REGISTER-BACKED over gb_v1_6 (Tier A
  remediation, 2026-08-12): this release adds the constants the open Tier A findings need, every one
  transcribed from docs/bess_constants_register.md (422 sourced constants) or
  docs/bess_degradation_requirements.md, with the primary source and vintage carried through verbatim. NOTHING
  here is invented: where the register does not hold a figure, the KEY is added with a null value and
  `status: needs_review` plus the exact data ask, so the code can find it and warn instead of silently
  defaulting."
- **`gb_v1_8`** (2026-08-12). Quoted from `assumptions/gb_v1_8.yaml`: "NULL-CLOSURE RELEASE over gb_v1_7
  (decision D45 as amended 2026-08-12: 'a null input is never acceptable, and a null is worse than a blocked
  run'). Every value here is either carried forward from gb_v1_7 byte-identical or closed from a PRIMARY
  PUBLISHED SOURCE fetched on 2026-08-12, with the URL and effective/publication date carried on the key.
  Where a search genuinely failed, the key stays null/absent with the SEARCH RECORDED in its notes."
- **`gb_v1_9`** (2026-08-15). Quoted from `assumptions/gb_v1_9.yaml`: "HOME-FOR-A-DORMANT-KEY RELEASE over
  gb_v1_8 (2026-08-15). Every gb_v1_8 value is carried forward BYTE-IDENTICAL. Exactly ONE key is added, with a
  NULL value and the failed search recorded."
- **`gb_v1_10`** (2026-08-16). Quoted from `assumptions/gb_v1_10.yaml`: "CAPACITY MARKET DE-RATING CURVE RELEASE
  over gb_v1_9 (2026-08-16). Every gb_v1_9 value is carried forward BYTE-IDENTICAL except the THREE enumerated
  below. Nothing becomes confirmed; every quote on this release stays not_bankable, by design."

## 2026-11-26 — WP-UW-16: local-flex award pricing made consistent (D61 to D63, D66 to D68)

- **Olly** (roadmap WP-UW-16, with WP-CORE-01 triage items #7, #8, #12, #20 to #22). Branch
  `roadmap/wp-uw16-core01`. Design calls by Fable 5.2 under the owner's delegation are marked as such in
  `docs/decisions.md`.
- **D61, per-candidate local-flex reservation** (`60af5a1`). Each sizing candidate's dispatch reserves its own
  clamped award MW, the same MW the availability invoice bills, so "invoice MW = reserved MW in every period"
  holds at every size. Triage #7 and #8 pass with zero edits. DIRECTION: on awarded sites the double-booked MW
  is no longer free to earn elsewhere, so their priced numbers and selection move; the demo holds no award and
  no demo pin moved.
- **D62 part 1** (`1cf52d8`). The zero-dispatch note keys on discharge outside DFS-called periods; #12 moves to
  a no-DFS fixture; #20 to #22 restated as laws. No £ moves. Part 2 (#24, #25) stays open under WP-CORE-01.
- **D63** (`d3a5a5c`). The lock pins highspy 1.15.1, the version the golden pins were minted on. No £ moves.
- **D66, D-130 closed** (`ac3ceb7`). `run_financial_model` reads `site.local_flex_awards` (`financial_model.py:168`), so a
  one-year award earns as a one-year award on every path from `generate_quote`. DIRECTION: P90 and the lender
  case fall in all 15 years on all nine real sites (Tanhouse P90 y1 1,384.83 → 1,021.45; Colnbrook P90 15-yr
  28,126.86 → 19,489.11); P50, IRR and payback unchanged (D65). Nine-site table in D66.
- **D67, the published peak** (`fdc056a`). The bill-producing solve (`engine.py:1357`, `demo.py:443`) re-solves once for
  the lowest peak among value-optimal schedules. DIRECTION: demo "peak import after" DOWN 278.407 → 235.841
  kW (golden pin re-minted, owner: "OK to re-mint"); no £ moves; all nine real sites unchanged. LIMIT: on 7 of
  the 9 real sites the study-basis bill is a MILP (export-wash binary) and the pass does not run, so their
  published peak is still not certified minimal (D67 limit 2, open under WP-CORE-01). New certificate
  test T-P2 (`test_demo_published_peak_is_the_minimum_at_the_optimal_value`), about 50 s in the golden job.
  The pin is provisional until the Linux golden job reports 235.841.
- **D68, D-116 handled by labelling** (`8657325`). The availability leg is priced gross and every awarded quote says so in
  `quote.warnings` (`pipeline.py:487`); a performance ratio is applied only from a CONFIRMED, sourced library
  entry, and none exists. DIRECTION: no £ moves; one warning added on awarded quotes. The D-116 MUST stays
  open (data ask: the DNO's settlement statements, or a published delivery ratio).
- **Tests.** New: `tests/test_local_flex_gross_label.py` (T13 to T17), T10 to T12 in
  `test_finance_tenor_threading.py`, T-P2 in the golden module, T-P3 and T-P4. Every new test was
  mutation-checked red (M-T10, M-T12a/b, M-T13 to M-T17b, M-P1 to M-P5). No existing test was loosened.

## 2026-11-24 — Cooling-flex baseline method v1 revised for the owner's answers, hashed and tagged

- **Olly** (roadmap WP-THERM-04). Documents only: no source, test or priced number changed.
- **`docs/methods/cooling_flex_baseline_method_v1.md`** is revised in place, still v1, because
  the 2026-11-23 draft was never hashed or published. Its draft hash in the entry below is
  superseded. Pre-registration hash:
  `sha256:c164f04ce7c1058f41f205c263c0bef3e34c0108c8dda1a1eb615834b6f4e95a`, which is the
  SHA-256 of the file with its `Pre-registration hash:` line removed (method section 20.1:
  `grep -v '^Pre-registration hash:' <file> | shasum -a 256`). Tag:
  `methods/cooling-flex-baseline-v1`.
- **Owner's answers of 2026-11-23**: "Hash on secondary sources" (the 30 %, ±10 % and 50 %
  limits are marked secondary-sourced and a correction is v2); "Recompute only (as written)"
  (verifiers recompute and do not approve); "Allow a short tolerance" (up to 15 binding-band
  breach minutes in total, excluded from credit, with no tolerance outside the declared ASHRAE
  envelope, an SLA band or the declared rate of change); "Allow documented adjustments" (three
  measured types NRA-1 to NRA-3, a 10 % cap and a 60-day limit, with re-baselining still the
  only route back to an unadjusted baseline). Fable 5.2 set the numbers under the owner's
  delegation of 2026-11-23.
- Gate Closure (one hour before the Settlement Period) is now confirmed from BSC Annex X-1.
- Not yet registered: no pre-registration mechanism exists on this branch. Registration as an
  IC-4 1.1.0 `baseline_method` object with issuer role `neura_self_issued` becomes possible once
  WP-BO-06 merges, and is WP-LEDGER-04.

## 2026-11-24 — IC-4 EvidenceObject 1.1.0; IC-1, IC-2 and IC-11 to 1.1.0 (`gridq.contracts`)

- **Olly** (roadmap WP-BO-06; plan `docs/shared_work/wp_bo06_plan.md`, amended §11). No priced
  number moves, and nothing calls the new refusal functions from pricing, eligibility or a revenue
  stream (D57: a contract function, not a gate). Nothing outside `src/gridq/contracts/`,
  `tests/contracts/`, this entry and the HANDOFF_LOG changed. IC-13 is unchanged. All eight
  `v1.0.0` schemas are byte-identical to their tags; the six superseded ones are archived and
  pinned by SHA-256, and `--write` refuses to change one.
- **IC-4 1.1.0**: seventeen evidence types and six third-party issuer roles appended. Evidence is
  third-party by default (ADR-D10 option b): Neura may self-issue only baseline methods, trial
  records and its own decision records (`neura_self_issued`, on the wire), cannot verify its own
  record, and must name and date a decision. `require_third_party`, `resolve_ref` (per-type
  admission) and `assess` refuse a self-issued record unless the caller admits it. A 1.0.0 label
  holds only 1.0.0 members (the down-level rule).
- **IC-1, IC-2, IC-11 1.1.0**: version references accept the 1.x range; own version 1.1.0; IC-2
  and IC-11 embed the six new roles. Each refuses a 1.0.0-labelled instance that holds anything
  1.0.0 did not allow.
- `IC4_INVENTORY.md` maps the roadmap's evidence needs to types and roles (gaps G2 to G7, with
  owners). New 1.1.0 fixtures live in `tests/contracts/fixtures/v1_1_0/`; every 1.0.0 fixture and
  golden hash is unchanged.
- 39 guards are mutation-checked (was 15). The runner moves to `_contracts_support.run_mutant`.
- Git tags `contracts/{ic4-evidence-object,ic1-connection-agreement,ic2-curtailment-scenario-set,
  ic11-thermal-flex}/v1.1.0` are annotated and sit on the commit that completes WP-BO-06 on
  `roadmap/wp-bo06` (2026-11-25).
- 2026-11-25: `IC4_INVENTORY.md` gains row 37 (WP-BO-06's own "an assumption still cannot be
  serialised" line) and a corrected count (30 lines in 25 packages). A new test fails when a
  roadmap package whose acceptance says "evidence object" has no inventory row; it runs where the
  Atlas repo is checked out beside this one.

## 2026-11-23 — Cooling-flex baseline method v1 written for pre-registration (`docs/methods/`)

- **Olly** (roadmap WP-THERM-04, spec A4-04, B6-04, TF-05). Documents only: no source, test or
  priced number changed.
- **`docs/methods/cooling_flex_baseline_method_v1.md`** fixes, before any trial, the
  counterfactual for cooling flex measured against an IC-11 1.0.0 surface. Its identity:
  `sha256:a1142decabea81ee7a7803c3829ae6ebe8a555db9fb56ffeab60c9d075dd421e` (the file's exact
  bytes; check with `shasum -a 256`). The coordinator tags this commit after review; the tag
  must predate any trial data. Registration as a self-issued record (ADR-D10) is WP-LEDGER-04.
- **Method**: sub-metered cooling plant (IPMVP Option B); two-stage regression on IT load and
  ambient, fitted and hashed before each month; additive same-day adjustment capped at ±10 %;
  hourly CV(RMSE) ≤ 30 % and NMBE ≤ ±10 % gates; uncertainty measured on placebo windows, with
  an event verified only at FSU68 ≤ 50 %; delivered kW is the lowest 15-minute block, because a
  declared IC-11 kW is firm; rebound is measured and netted; unverified events are credited 0.
- **Quantities**: 15 metered (Q-01 to Q-15) and 15 BMS and controller (B-01 to B-15), each with
  point, resolution and accuracy class, for WP-LEDGER-04 to bind to telemetry fields.
- **Not sourced, and said so in the file**: ASHRAE Guideline 14 and IPMVP were not read (their
  values come through FEMP M&V Guidelines 4.0 and two other secondary sources); no formal Ofgem
  "2027 baselining review" was found, only the open letter of 3 November 2026; no wet-bulb
  formula is registered.

## 2026-11-23 — IC-11 thermal flex 1.0.0: pre-tag review fixes (`gridq.contracts.thermal_flex`)

- **Olly** (roadmap WP-THERM-02). Fixes from `docs/reviews/2026-11-23-ic11-thermal-flex-review.md`.
  The version stays 1.0.0 because it is still untagged. No priced number moves. Nothing outside
  `src/gridq/contracts/`, `tests/contracts/`, this entry and the HANDOFF_LOG changed. IC-1, IC-2,
  IC-4 and IC-13 schemas are byte-identical.
- **F1**: `check_instruction` reads the whole event `[start, end)` on the GB clock. It refuses
  an event that runs past `valid_until`, or into an `unavailable` or `unknown` cell.
- **F2**: the binding band and a known SLA band are measured at the ASHRAE band's point, so the
  IT-safety nesting always runs. `binding_source` is checked (`ashrae` = the ASHRAE band,
  `customer_sla` = the SLA band, `operator_margin` = inside both).
- **F3**: a declared kW is firm (owner, 2026-11-23). It is deliverable for its duration at any
  start in the cell, within the binding band, not a typical value.
- **F4**: the fixture's ASHRAE source names no edition and is marked unverified. The
  evidence-class floor rule is stated. The metered fixture no longer serves October to December
  as metered, and all six IC-11 golden hashes changed.
- 15 guards are mutation-checked (was 6).

## 2026-11-22 — IC-11 thermal flex frozen at 1.0.0 (`gridq.contracts.thermal_flex`)

- **Olly** (roadmap WP-THERM-02, NEW-IC-11). No priced number moves. Nothing outside
  `src/gridq/contracts/` and `tests/contracts/` changed except this entry and the HANDOFF_LOG.
  IC-1, IC-2, IC-4 and IC-13 are unchanged, and their schemas re-check byte-identical.
- **`ThermalFlexAvailability`**: kW holdable for a duration in minutes, by season and
  Europe/London time-of-day band (spec A4-03), with the rebound on every curve point, the
  declared IT-safety limits (ASHRAE class and envelope, customer SLA, binding band; A4-02) and
  the evidence class (modelled, trialled or metered; trialled and metered need IC-4 refs).
- **Zero flex is distinct from unavailable**: each cell is `flex`, `zero_flex`, `unavailable` or
  `unknown`, never null or missing (acceptance 2, tested).
- **`FlexInstruction` and `FlexAcknowledgement`** (TF-04): accepted, rejected with a reason
  code, or partially accepted with a kW, bound to the instruction by hash; UTC instants.
- Integers throughout (kW, kWh, minutes, tenths of a degree), as IC-2 uses exact rationals.
- Six synthetic fixtures (a modelled chilled-water site, a metered-trial site, one instruction
  and three acknowledgements); a fresh-interpreter consumer test runs on them with no plant
  model (acceptance 3); six guards are mutation-checked.
- Git tag `contracts/ic11-thermal-flex/v1.0.0` is created by the coordinating session after review.

## 2026-11-17 — IC-2 CurtailmentScenarioSet and IC-13 identity/audit frozen at 1.0.0 (`gridq.contracts`)

- **Olly** (roadmap WP-CONN-03, WP-PLAT-05). No priced number moves. Nothing outside
  `src/gridq/contracts/` and `tests/contracts/` changed except this entry and the HANDOFF_LOG.
  IC-1 and IC-4 are unchanged, and their schemas re-check byte-identical.
- **IC-2 `CurtailmentScenarioSet` 1.0.0** (`src/gridq/contracts/curtailment.py`).
  - Each scenario carries a half-hourly series, a minute series (or `NotApplicable`), its
    events and a weight. The set also carries the evidence class, signal basis and provenance.
  - Weights are exact rationals in lowest terms and must sum to **exactly** 1, with no tolerance.
  - Series are step-encoded with one encoding per series, so the hash is stable under
    re-serialisation (tested).
  - The minute series, half-hourly series and events are cross-checked against each other.
  - `expected_series` and `expected_energy_mwh` let consumers run without the engine; a
    fresh-interpreter test proves no engine, solver or pandas module is imported.
- **IC-13 identity claims and audit events 1.0.0** (`identity.py`, `audit.py`).
  - Roles: Neura ops, customer, lender read-only and verifier. The model enforces the actions
    each role may take.
  - RS256 JWTs are validated offline with the standard library, so no dependency is added.
    The tests interoperate with `cryptography` in both directions.
  - Hash-chained audit events cover customer-data reads and control writes, allowed and denied.
  - Committed TEST-ONLY fixture keys are refused unless `allow_fixture_keys=True`.
- **Real DNO fixtures are git-ignored.** UKPN DER4 (CC BY 4.0) and NPg DER-0000099 (NPg Open Data
  Licence v1.0) sets were built from responses recorded on 2026-11-17. They are kept out of git
  under the Atlas eyes-only gate (Huwise authorisation `not_recorded`). Only the hash and
  provenance manifest is committed. Committed IC-2 fixtures are synthetic-shape. See
  `tests/contracts/fixtures/real/README.md`.
- **Tests.** `tests/contracts`: 393 passed (was 220). Five mutations were each caught: weight
  tolerance, signature always accepted, unchecked `prev_hash`, a lender holding a write scope,
  and non-unique step encoding. ruff and mypy are clean, and `python -m gridq.contracts --check`
  passes.
- **Git tags to create on commit:** `contracts/ic2-curtailment-scenario-set/v1.0.0`,
  `contracts/ic13-identity-audit/v1.0.0`.

## 2026-11-16 — IC-4 EvidenceObject and IC-1 ConnectionAgreement frozen at 1.0.0 (new `gridq.contracts`)

- **Olly** (roadmap WP-BO-01, WP-CONN-01). No priced number moves. The new package is not yet
  called from the pricing path, and no existing module, test or schema was edited.
- **IC-4 `EvidenceObject` 1.0.0** (`src/gridq/contracts/evidence.py`). The model is frozen, strict
  and closed, and no field accepts `None`.
  - An expired document **validates** and reports `expired`. `assess()` returns `absent` only when
    nothing matches.
  - An assumption cannot become evidence:
    - assumption fields, assumption status tokens, `DerivedAssumption`/`Assumption` instances and
      their dumps are all refused;
    - a zero-byte document hash and a Neura/self issuer are refused;
    - `serialise_evidence` takes only an exact `EvidenceObject`, and a mypy test proves the static
      refusal.
- **IC-1 `ConnectionAgreement` 1.0.0** (`src/gridq/contracts/connection.py`).
  - Carries every spec A1-01 term plus ANM response time and signal protocol. `A1_01_FIELDS` maps
    each spec phrase to its field.
  - Unknowns are explicit `Unknown(reason)` values, and inapplicable terms are
    `NotApplicable(reason)`.
  - A real agreement references its IC-4 document by hash.
  - It replaces `anm_or_curtailment_notes` as the contract. Migrating `SiteModel` onto it is
    consumer work and is not done here.
- **Canonical hash.** sorted keys, compact separators, UTF-8, NFC strings, `-0.0` normalised.
  Golden hashes are pinned for every fixture, with tests for key order, whitespace, int/float
  spelling and `PYTHONHASHSEED`.
- **Committed JSON Schemas** under `src/gridq/contracts/schemas/`. `python -m gridq.contracts
  --check` fails on drift.
- **Fixtures.** Seven synthetic archetypes in `tests/contracts/fixtures/` (3 IC-1: firm, hybrid,
  fully non-firm; 4 IC-4). They are marked as synthetic, name no real party, and cite public GB
  sources for their structure.
- **Tests.** 220 tests in `tests/contracts/`, all passing, and they run in the CI fast tier.
  Four mutations were each caught: expired reported as absent, the assumption guard removed,
  unsorted keys, and a default `Unknown.kind`.
- **Git tags to create on commit:** `contracts/ic4-evidence-object/v1.0.0`,
  `contracts/ic1-connection-agreement/v1.0.0`.

## 2026-11-16 — WP-CORE-01/02: the 27 failures triaged by cause, and the findings register re-verified

- **Olly** (roadmap WP-CORE-01 / A10-02, WP-CORE-02 / A10-01). No priced number moves. The only
  `src/` edits are two docstrings (`sizing/deliverability.produce_gate_context`,
  `optimisation/schedule.priced_floor_terms_gbp`). No dependency pin changed.
- **The 27 pre-existing failures, classified** in `docs/shared_work/core01_failure_triage.md`. All
  27 fail identically on a clean export of HEAD `55b168c`. **25 are (a)**: a stale expectation
  superseded by a documented change. **2 are (b)**, a real defect. **0 are (c) or (d)**.
  **19 of the (a)s are re-pinned**, each with the new value recomputed by hand in a test comment
  and each mutation-checked (the superseding behaviour reverted in memory goes red). The causes:
  D57 item 4 (DFS in the objective, `205ed92`), D57 items 1–2 (`7405d4b`), D49 (g) with FIX 3
  (`f3aedd8`), FIX 5 (`05133e4`), D54, D56, and the `12033d0` "no system was selected" reword.
  **6 (a)s are left untouched** because DFS broke the fixture's premise and the new value is not
  determinate: the g9 tie-break, the zero-dispatch note (×3) and the windowed-levy pair. **The 2
  (b)s** are a regression from `339892c`: the local-flex availability INVOICE now bills the
  solved candidate while the dispatch RESERVATION still holds the search floor, breaking the FIX 1
  invariant the constraint builder's docstring still states. They are left red pending an owner
  decision.
- **Findings register re-verified against the call path**:
  `docs/reviews/2026-11-16-findings-register-triage.md`, with per-finding evidence and the
  call-path audit output in `docs/reviews/2026-11-16-findings-register-evidence/`. Every finding is
  closed with a production call site and guarding test, or triaged to a roadmap work package (or a
  named workstream where no package fits) with a reason. Closed and still listed open by the
  2026-08-12 register: N9, N10, N11, N26, N28, H-17, JSON-F-51. Latent findings now live: D-116
  (availability leg booked gross on every D54 site) and D-130 (award tenor never reaches finance).
  C-11 is closed (a docstring).
- **CLAUDE.md corrected where a claim was proven wrong.** Gate 3 is wired (`pipeline.py:367`), not
  "called from nowhere". N26 and MD-F-05(b) are fixed. Six of nine site records are metered, not
  only Harwell. The newest release is gb_v1_12. Gated streams move into `value_streams` when
  consented and priced. Five stale line pointers were corrected.
- **Environment, measured:** local `-n 8` runs hit `ENOSPC` with the system volume at 99–100%;
  every affected test passes on re-run with space free. The fast tier now collects 3,317 tests, of
  which 220 are new `tests/contracts/` from a concurrent session.

## 2026-11-16 — PHASE 1 FOUNDATIONS: the MPC reports infeasibility, CI exists, the venv runs again

- **Olly** (roadmap WP-SIZE-01, WP-PLAT-01, WP-CORE-01). No priced number moves: the annual MILP,
  every assumption release and every golden pin are untouched.
- **The MPC no longer reports `optimal` with zero volumes (WP-SIZE-01, spec A10-05/B2-02).**
  Measured at HEAD 55b168c on gb_v1_12 with the 7-day test fixture (`tests/_milp_support.py`,
  100 kW x 4 h): every window refused (7/7) and the result read `optimal`, 0.0 kWh, for a
  feasible problem and an infeasible one alike. Fixed in
  `optimisation/mpc_solver.py`: the loop starts at the release's usable SoE floor, not 0; a
  refused window ends the solve with that window's own status (`infeasible` or
  `insufficient_data`), its reason and no volumes (the old hold-and-continue branch is gone); the
  default simulator clips to the same SoC window the planner uses (`battery_model.py` gains
  `soc_max`, default 1.0). The window reason comes from the new
  `MilpDispatchSolver.solve_window_outcome`; `solve_window` keeps its bare-`None` contract. After the
  fix: the feasible problem → `optimal`, 98,767 kWh/yr annualised discharge; a genuinely
  infeasible one (an 800 kW load-service obligation on a 200 kW pack) → `infeasible` with no
  volumes, matching the annual MILP on the same problem. gb_v0_1 (no SoC window) is unchanged to the kWh.
  Regression suite: `tests/test_mpc_infeasibility_regression.py` (9 tests, fast tier).
  **The engine quarantine stays** (`MilpEngine(use_mpc=True)` still raises); only its text is
  corrected. Opening the path to priced runs is the owner's decision.
- **CI (WP-PLAT-01, spec E-05).** `.github/workflows/ci.yml` (PR + main: ruff, ruff format,
  mypy, the fast tier with a 10-minute step timeout, the golden pins, and a single `ci-gate` check
  for branch protection) and `.github/workflows/nightly.yml` (the full suite in parallel shards,
  with pass/fail counts on the run summary and in a 90-day artifact). Test tiers are defined once,
  by measured cost, in `.github/ci/run_tests.sh`: fast (3,097 tests; 151 s at 8 workers, 225 s at
  2 on an Apple M2), slow (22 modules each ≥60 s), and four modules that solve a demo quote at
  import and run alone. CI installs the deployment lock (`requirements.txt`) on Python 3.12 with
  the Dockerfile's determinism settings, plus two declared departures: **the lock omits
  `openpyxl`** (a declared runtime dependency; `.xlsx` ingestion fails on the lock, so the Docker
  image has the same gap), and **the golden pins need highspy 1.15.1, not the lock's 1.14.0**
  (isolated by swapping only highspy: on 1.14.0 the demo's `bill.peak_import_after_kw` is
  270.306 kW against the pinned 278.407). Both are pinned in CI to the `.venv` versions until the
  owner updates the lock or re-mints the pins.
- **Baseline (WP-CORE-01).** The `.venv` pointed at the old `Karim Project` path (every console
  script's shebang and the editable `gridq` install); rebuilt in place with no package versions
  changed. `ruff check .` was failing on two tracked analysis scripts in `docs/grundon_harwell/`;
  fixed by style-only edits with byte-identical output. Full suite on the working tree: 3,301
  tests, 3,273 pass, **27 fail**, 1 skipped; all 27 fail identically on a pristine export of
  HEAD 55b168c, so none is new. They are NOT re-pinned here: they assert values and premises from
  before D57/D58/D60, and each needs its cause proven before its pin moves.

## 2026-08-23 — CLIENT REPORTS: the basis notice moves to the assumptions, and section 1 says what it unlocks

- **Olly**, on owner feedback. Three edits to `scripts/build_client_report.py`, both reports.
- **The basis notice comes OFF the cover.** The seven-line amber block was the first thing a reader
  met; it now opens the assumptions section instead, cut to three lines: "A feasibility study, not
  an underwriting output and not an offer. Every revenue stream is modelled as open and every
  contract, consent, award and registration is assumed approved." **The LABEL did not move**, only
  the prose: `Study basis` and `Confidence band` still stamp the cover meta row and the footer,
  which is what D46 and D57 require of every artifact.
- **Section 1 now answers four questions under four headings**, so a reader who scans only the
  headings still gets the case: what a behind the meter battery is, what it unlocks for the
  business (cuts delivered cost, frees connection capacity, earns, and why the third one changes
  the character of the investment), what it unlocks for the system, and why now. The committed
  content and its references are kept intact; what was added is structure and the business half,
  which was previously one clause.
- **The method flowchart is cut to scannable length.** Every box was two or three lines of prose in
  a border; each is now one short line. The detail lives in section 3, where a reader who wants it
  looks.
- **Pre-existing lint debt cleared** while in the file: the `LOGO_B64` data URI carries `# noqa:
  E501` and the one long `.meta b` CSS declaration is wrapped. `ruff check` on this file was failing
  at HEAD with those two E501s; it passes now.

## 2026-08-21 — TWO CLIENT REPORTS: one Grundon estate, one Harwell, rendered from the run records

- **Olly.** New `scripts/build_client_report.py` renders two consultancy-style feasibility reports
  from published campaign runs: `deliverables/grundon_feasibility_report.html` (the five METERED
  Grundon sites) and `deliverables/harwell_feasibility_report.html`. Narrative order is what BTM is
  and why it matters (cited, one page), the method as a six-stage flowchart, the method in plain
  terms, per-site results, then a grouped assumptions section. Standalone HTML, no network requests,
  with a print stylesheet.
- **A RENDERER, NOT A SECOND PRICING PATH.** Every pound, percent and kW is read from `quote.json`
  or `site.json`. Revenue totals are the plain sum of the record's own per-stream values and are
  labelled as such on the line. Nothing is recomputed for presentation (CLAUDE.md section 8).
- **The 3D sizing figure draws ONLY solved candidates.** One column per evaluated size on a break
  even plane, at one fixed axonometric angle, with NO interpolated mesh between them: a smooth sheet
  would show returns at sizes the solver never evaluated. Full-year solves and coarse screening
  solves are drawn differently, because a proxy score and a full-year score are not the same number.
  The vertical axis is clipped below at twice the positive lobe, declared in the legend and caption.
- **Three record distinctions the first draft got wrong, and now states.** (a) Sites whose agreed
  capacity was never supplied show "Not supplied" plus what stood in for it, mirroring
  `report_charts.capacity_is_supplied`; Harwell's observed peak and Tanhouse's derived 500 kW no
  longer read as facts. (b) Tanhouse is behind a private wire, so no DUoS line, no red band shading
  and no red band claim are drawn for it. (c) Knowl Hill's arbitrage is a net cost and says so.
- **Invoice-anchored runs are excluded** from both documents: they share a `site_id` with their
  metered twin, so one document carrying both would put two answers under one site name.
- **Tests:** `tests/test_client_report_build.py`, seven cases, mutation-checked. Adding a filled data
  surface, dropping a candidate, or inflating a KPI each make it fail.
- **Study basis stated on both**, `all_streams_enabled_study_v1`, confidence band low. Not an
  underwriting output and not an offer (D46, D57).

## 2026-08-19 — DFS SCALED TO THE EVIDENCE: the observed period count, the site's own zone, and this battery

- **Olly**, on the owner's instruction, verbatim: "ok scale the DFS then to the evidence, scaling based
  on battery size and location to get a more accurate view." Decision **D60**. Release `gb_v1_12`.
  **NO GATE, THRESHOLD, CONSENT TEST OR ELIGIBILITY RULE IS ADDED ANYWHERE** - the 0.1 MW Registered
  Unit Capacity minimum, DFS registration, zone membership, the baseline methodology and stacking
  exclusivity with the BM and the response family all stay ASSUMED APPROVED (D57 limbs 1-3, 5b). Three
  dimensions changed, and all three are arithmetic.
- **Why DFS and why now.** It was the LARGEST single stream in the published campaign (GBP 60.85/kW/yr
  at Bishops Cleeve Ash Plant, GBP 75.83 at Harwell) and the softest number in it. An independent
  magnitude audit verified the RATE and the 407-settlement-period count from the raw file and left the
  ANNUALISATION as "plausible with a stated extrapolation", predicting that scaling to the observed
  periods would bring the site total to roughly GBP 135/kW/yr and account for GBP 39.7 of the gap to
  Modo's GBP 60-110/kW/yr grid-scale index on its own.
- **1. THE OBSERVED PERIODS, NOT AN ANNUALISATION OF THEM. DOWN, 3.16x on the count.** The mask booked
  zone 6's 407 accepted settlement periods multiplied by 365/127 to **1,170** called half-hours a year,
  so 65 pct of the modelled calls were an extrapolation of a spring-and-summer observation across autumn
  and winter. It now books what was OBSERVED, per zone: **370** for the campaign's zone 10 and **366**
  for zone 12. Published as a **MEASURED FLOOR** with both directions stated - it understates a full
  year by up to 2.9x if the event rate persists, and the extrapolation overstated it if the observed
  window was seasonally intense. The evidence settles neither; a full delivery year of the 2026/27
  service does not exist, because the redesign is four months old.
- **2. THE SITE'S OWN ZONE, resolved by POINT-IN-POLYGON.** Every site was priced in zone 6, the most
  favourable of the twelve. DFS is zonal and price and call count move in OPPOSITE directions across the
  zones (thin zones GBP 195-222/MWh on almost no volume, deep zones GBP 157-164/MWh on nearly all of
  it), so a national average is the one figure that is certainly wrong everywhere. Each site's zone now
  comes from its postcode-district centroid against NESO's published 12-zone GeoJSON, whose **zip sha256
  REPRODUCES the value the library already recorded** at
  `dfs_registered_unit_capacity_mw.zone_names.geojson_sha256`. The campaign spans **zone 10** (GL52,
  OX10, OX11, RG10) and **zone 12** (SL3). Reproducible: `scripts/derive_dfs_zonal_basis.py --zones`.
  An unresolved postcode substitutes the best-evidenced zone IN FULL and declares it (D57 limb 5b),
  never zero. **Tanhouse gains a postcode** (`SL3`, a labelled `derived_input` from the repo's own
  evidence that it shares one private wire with Colnbrook), so all nine site records now resolve their
  own zone and none takes the substitution.
- **3. SIZE, and the proxy path was the half that did not scale.** The engine path already measured DFS
  from the dispatch, so it already scaled with power and duration; the PROXY that ranks the coarse sweep
  did not. It paid a flat per-kW rate on the Registered Unit Capacity **TRUNCATED to one decimal place**
  - a 0.25 MW unit was paid on 0.2 MW, a 20 pct haircut delivered by a registration formality, which
  D57 limb 3 expressly forbids - and it had **no energy term at all**, so a 0.5 h battery was ranked as
  though it could serve a five-half-hour evening window at full power. It now prices the unit's own MW
  over `min(periods per event day x 0.5 h x rated kW, usable kWh) x event days`, every term measured,
  with the usable window from the library's own SoC bounds and NO invented haircut where they are
  absent. The engine path publishes the same function beside its figure with this candidate's own
  power-bounded ceiling and the percentage of it the dispatch reached.
- **TWO THINGS THE RE-DERIVATION FOUND THAT WERE NOT IN THE BRIEF.** (a) The window shape driving the
  mask was a count of Live **BID ROWS INCLUDING REJECTED ONES** (7,216 of them), not of procured
  settlement periods, though its docstring said otherwise: a rejected bid weights a clock slot by how
  hard providers tried, not by what NESO procured. The shape is now each zone's own accepted-period
  histogram, so the COUNT and the SHAPE are one measurement. (b) The `Event Type == Downwards` filter
  the engine's own measurement implies had not been applied; it moves zone 3 from 173 periods to 163
  and leaves the campaign's zones unchanged.
- **THE PACKS STOPPED CONTRADICTING THEMSELVES.** `dfs_unit_economics_notes` still told every reader the
  record showed GBP 0 "only because no engine branch prices this stream yet", and carried a note headed
  "WHY THE RECORD STILL READS GBP 0". That branch has existed since D57 item 4, so every pack since
  carried a measured DFS pound and, three notes later, the claim that nothing could measure it. NESO's
  per-MW scenarios are now labelled as a published cross-check on the superseded winter 2024/25
  pre-zonal basis, quoted beside the priced figure and never added to it. The campaign runner's
  DECLARED option-set price for DFS (GBP 93.41/kW/yr, zone 6, annualised) is now the site's own zone's
  measured figure, so the register and the objective cannot disagree.
- **Evidence, and how it is held.** NESO Data Portal, package `demand-flexibility`, resource "DFS
  Utilisation Report" (live 2026/27), resource_id `3ebf77d7-05df-466e-a023-dc45a90efeea`, fetched
  2026-08-18 and held at `data/fetched/neso/dfs_util_cur.csv` (sha256 `655bd616...`): 7,369 bid rows, 70
  Event IDs, 2026-04-14 to 2026-08-18, 11 participants, 80 DFS Units, all 12 zones populated. The
  12-zone table is TRANSCRIBED into `gb_streams` and into
  `ancillary_services.dfs_zonal_observed_2026_27` rather than read at solve time, because NESO grows
  that resource daily and `input_hash` pins no data content (F-06) - a re-fetch would move every DFS
  pound under an unchanged `run_id`. `scripts/derive_dfs_zonal_basis.py --basis` regenerates it and
  refuses to run if the sha256 has moved; `tests/test_dfs_zonal_basis.py` pins the constants to the
  file, the library copy to the constants, and both to all twelve zones.
- **Traceability.** The DFS stream now returns an assumption key
  (`ancillary_services.dfs_zonal_observed_2026_27`), so the D59 per-stream register cites a library key
  for the largest stream in the pack instead of printing a traceability gap.
- **MEASURED, ALL EIGHT SITES, AT THE SIZE THE PRE-CHANGE CAMPAIGN SELECTED** so the delta is
  size-controlled and is the DFS basis change alone. One MILP solve per site on its own metered year;
  the DFS pound is the dispatch's own objective term.

| site | kW | h | zone | DFS before | DFS after | DFS/kW before | DFS/kW after | delta /kW | power ceiling | pct of ceiling | site total /kW before | implied after |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bishops_cleeve_ash_plant | 9.300 | 2.00 | 10 | GBP 565.94 | GBP 186.54 | 60.85 | 20.06 | **-40.79** | GBP 282.16 | 66.1 pct | 174.76 | **133.97** |
| bishops_cleeve_mrf | 11.880 | 2.00 | 10 | GBP 844.92 | GBP 292.92 | 71.12 | 24.66 | **-46.46** | GBP 360.43 | 81.3 pct | 190.89 | **144.43** |
| knowl_hill | 11.360 | 0.67 | 10 | GBP 87.97 | GBP 32.13 | 7.74 | 2.83 | **-4.91** | GBP 344.66 | 9.3 pct | 118.96 | **114.05** |
| knowl_hill_invoice_anchored | 4.800 | 0.63 | 10 | GBP 35.51 | GBP 12.93 | 7.40 | 2.69 | **-4.71** | GBP 145.63 | 8.9 pct | 113.21 | **108.50** |
| ewelme | 7.270 | 2.00 | 10 | GBP 471.90 | GBP 172.21 | 64.91 | 23.69 | **-41.22** | GBP 220.57 | 78.1 pct | 196.42 | **155.20** |
| ewelme_invoice_anchored | 21.250 | 1.36 | 10 | GBP 1,241.44 | GBP 530.24 | 58.42 | 24.95 | **-33.47** | GBP 644.72 | 82.2 pct | 181.81 | **148.34** |
| tanhouse | 24.320 | 0.84 | 12 | GBP 1,464.57 | GBP 544.84 | 60.22 | 22.40 | **-37.82** | GBP 723.45 | 75.3 pct | 168.93 | **131.11** |
| harwell | 21.381 | 2.00 | 10 | GBP 1,621.22 | GBP 578.65 | 75.83 | 27.06 | **-48.77** | GBP 648.62 | 89.2 pct | 204.87 | **156.10** |

  **DFS falls from GBP 58-76/kW/yr to GBP 20-27/kW/yr on the six 1.36-2 h sites and from GBP 7.4-7.7
  to GBP 2.7-2.8 on the two sub-0.7 h ones**, and it stays NON-ZERO on all eight (D57). The eight-site
  mean delta is **-32.27 GBP/kW/yr** (range -4.71 to -48.77), against the audit's -39.7 at Bishops
  Cleeve Ash Plant, where the measurement is **-40.79**. Implied site totals move from GBP 113-205 to
  GBP **108-156**/kW/yr against Modo's GBP 60-110 (GBP 110 long-term) grid-scale index: DFS was worth
  GBP 39.7 of the gap, exactly as the audit said, and the remaining excess is other streams and is not
  this change's to close.
- **THE SIZE FUNCTION IS VISIBLE IN THE LAST COLUMN, AND IT IS THE ENERGY BOUND.** The percentage of
  the power-bounded ceiling each dispatch reached tracks DURATION, not power: **89.2 pct at Harwell
  (2.00 h), 81-82 pct at Bishops Cleeve MRF and Ewelme invoice-anchored, and 8.9-9.3 pct at the two
  Knowl Hill candidates (0.63-0.67 h)**. A 0.6 h battery cannot serve a five-to-eight-half-hour
  evening window, which is exactly why a flat GBP/kW/yr was the wrong shape for this stream and why
  the proxy path now carries an energy term.
- **LOCATION IS VISIBLE TOO:** Tanhouse and Colnbrook price in **zone 12** (GBP 162.533/MWh over 366
  observed periods) and the other seven records in **zone 10** (GBP 163.979/MWh over 370), each
  resolved from its own postcode.
- **THE TWO BASIS-CONSISTENT CAMPAIGN RE-RUNS, full sizing loop, same export study basis as the
  published set.** Harwell holds its size and Bishops Cleeve Ash Plant re-sizes, which is the sizing
  loop responding to a revenue curve that is no longer flat in duration:
  - **Harwell**, 21.381 kW x 2.00 h both sides (`quote_run_a3c79c4c252a7b6a` to
    `quote_run_57bce125c3a815d7`): DFS **GBP 1,621.22 to GBP 552.88** (75.83 to 25.86/kW/yr), site
    total **GBP 4,380.24 to GBP 3,624.38** (204.87 to **169.51**/kW/yr). The dispatch reallocated the
    evening half-hours it no longer sells to DFS: DUoS red-band avoidance rose GBP 360.23 to 440.04
    and tariff arbitrage GBP 322.66 to 352.19, which is the MILP choosing per half-hour and is the
    behaviour D58 limb 1 describes.
  - **Bishops Cleeve Ash Plant** re-sized from 9.300 kW x 2.00 h to **5.970 kW x 2.56 h**
    (`quote_run_8f72a9063ffc2a6a` to `quote_run_66d2dadeaa8e520d`): DFS **GBP 565.94 to GBP 147.62**
    (60.85 to 24.73/kW/yr), site total **GBP 1,625.29 to GBP 946.09** (174.76 to **158.47**/kW/yr).
  - **D57 acceptance holds on both:** the stream matrix scores **23 of 24 cells** on the two fresh
    runs, and the single failure is Harwell's WHOLESALE_P415 GBP 0, which failed identically on the
    published run. The pre-change eight-site baseline is 94 of 96 with the same two failures
    (Harwell WHOLESALE_P415, Tanhouse DUOS_RED_BAND_AVOIDANCE on a private wire). **No cell
    regressed, and DFS is priced on every site scored.**
  - **The remaining six sites' campaign re-runs are NOT in this entry.** Two things move a total that
    are not DFS and both landed the same day: the fixed capex term and the local-flex leg now billing
    the solved candidate rather than the search floor. Attribute per stream, not per total.
- **CAVEAT ON THE COMPARISON, stated because it is a real difference in method:** the BEFORE figures
  come from published campaign runs carrying the export study basis, and these AFTER figures come from
  a plain solve at the same size, so export is not competing for the same discharge in the after
  column. The 3.16x period cut dominates that difference by an order of magnitude, and the two
  campaign re-runs in flight (Bishops Cleeve Ash Plant and Harwell) are the basis-consistent check.
- **THE TWO GOLDEN PINS ARE RED, AND THE MEASUREMENT SAYS THEY WERE ALREADY RED BEFORE THIS CHANGE.**
  `tests/test_determinism_golden.py::test_demo_priced_outputs_match_the_pinned_values` and
  `::test_demo_stream_placement_and_firmness_are_pinned` both FAIL. The drift was measured in full
  (a plain-python run of the demo quote through the test module's own helpers, because three attempts
  with "pytest" on the command line were killed by a concurrent agent's periodic `pkill -f pytest`),
  and it attributes as follows.
  - **NOT THIS CHANGE, and provably so.** The placement pin moves on FOUR streams and D60 touches
    none of their gates: `CAPACITY_MARKET` conditional/conditional to **contracted/open**, `DFS`
    conditional/conditional to **contracted/open**, and `BALANCING_MECHANISM` and
    `ANCILLARY_DC_DM_DR` MIGRATING OUT OF `gated_upside` INTO THE PRICED FLOOR (the key set itself
    changed: `gated.*` lost, `floor.*` gained). Those are the D57 all-streams change set - the DFS
    engine branch (item 4), the retired CM commitment gate (items 6-8) and the streams D57 limb 1
    opened - and every one of them predates this work. The priced pin says the same thing: the two
    largest deltas are `floor.CAPACITY_MARKET` **GBP 0 to 14,167.08** and `floor.DFS` **GBP 0 to
    3,772.76**, and a pin of GBP 0 for DFS could only have passed while NO engine branch priced DFS,
    which stopped being true on 2026-08-18. **The pins were never re-minted when D57 landed.**
  - **WHAT D60 ADDS on top.** It changes the DFS magnitude on the demo (LE17 4XN resolves to zone 6,
    so the called-period count falls 1,170 to 407) and therefore the demo's dispatch and its selected
    size: 175.00 kW x 3.15 h to **157.94 kW x 4.00 h**, tariff arbitrage GBP 20,220.36 to 19,397.62,
    DUoS GBP 16,600.76 to 15,678.57, peak import after 244.19 to 278.407 kW, project IRR 8.4529 to
    **25.8359 pct**. The IRR and total-revenue movement is dominated by the CM and BM/ancillary
    additions above, not by DFS.
  - **NEITHER PIN IS RE-MINTED HERE, deliberately.** Three of the four placement changes and the two
    largest priced deltas belong to other lanes' change sets that landed today. Re-minting them from
    this seat would sign off on another lane's structural change - BM and DC/DM/DR entering the
    PRICED FLOOR is exactly the kind of migration this pin exists to surface - so the full measured
    drift is recorded here and handed over instead. The pins are the demo's own economics; no
    campaign figure depends on them.
- **Docs.** `docs/bess_constants_register.md` gains a "DFS 2026/27 ZONAL OUTTURN" section with the full
  12-zone table, the filters, the definitions and the zone allocation; the standing data ask "a current
  cleared DFS utilisation-price distribution by zone" is **CLOSED**; and the "why DFS loses on merit"
  row is annotated as SUPERSEDED AS A MAGNITUDE (its GBP 7.558 central case is the pre-zonal basis;
  the campaign's own zone pays GBP 30.34/kW/yr at full delivery, the same order as BM and DCL).

## 2026-08-19 — Seven ARITHMETIC fixes: the reported study-basis revenue falls from GBP 631/kW/yr toward the Modo band

- **Olly.** Every one of the seven is a **volume, an hours count, a unit conversion, a netting
  completeness or a label**. **No eligibility constraint is added anywhere** - no gate, minimum size,
  consent test, award requirement, tender-availability test or stacking prohibition - and every stream
  stays OPEN and PRICED on every site (D57 limbs 1-3, 5b; D58). Release `gb_v1_12`.
- **Why.** Both audited campaign runs reported **~GBP 631/kW/yr**, i.e. 7.9x Modo's GBP 80/kW/yr
  revenue-stacked GB grid-scale index for an ENTIRE megawatt battery and 5.7x its GBP 110/kW/yr
  long-term figure. An adversarial magnitude audit reproduced every stream from the record's own inputs
  and found five arithmetic errors explaining 94-100 pct of the excess, plus two completeness gaps.
- **1. LOCAL-FLEX VOLUME, 20.6x, DOWN, 93 pct of the excess.** `gb_streams` computed the deliverable-
  capability clamp, printed it in the same artifact, and clamped only a LOCAL COPY that became the
  `DispatchConstraintDescriptor`; `valuation._local_flex_value` re-read `site.local_flex_awards`
  UNCLAMPED. The dispatch reserved 10.372 kW at Harwell while the availability leg was invoiced on
  213.810 kW, the site's whole metered peak import. `gb_streams.clamped_local_flex_awards` is now the
  ONE definition both consume, so the invoiced MW IS the MW in the dispatch reservation at every
  candidate. Also corrects `7d0a731`, which bounded by `schedule.deliverable_capacity_mw` - a
  CAPACITY_MARKET reservation quantity, `None` on every campaign run.
- **2. LOCAL-FLEX HOURS, 1.32x, DOWN on SSEN, no-op on NGED.** The hours were re-derived from the
  award's clock times (180 h at Harwell) against the source row's own published **136 availability h**,
  which is the count that reconciles to SSEN's published GBP 8,200 tranche budget. The published count
  is now a structured library field (`awarded_zone_clears.<row>.availability_h_per_year`) and governs;
  the derivation stays as the fallback. NGED's Port Ham publishes 1,098 h and the derivation gives
  exactly 1,098, so nothing moves there.
- **3. A FIXED CAPEX TERM AT LAST, capex UP, returns DOWN, hardest at the small end.** Capex was purely
  linear in kW and kWh. `battery_capex.dno_assessment_and_design_one_off_gbp_per_site` (GBP 1,557,
  NGED South West's published minimum Assessment and Design charge) read "REFERENCE ONLY - NOT CONSUMED
  BY ANY CODE PATH TODAY, AND THE OMISSION FLATTERS EVERY RETURN"; it is consumed now, on BOTH capex
  shapes. With 83.9 pct of the revenue stack invariant to battery size, a purely linear cost made the
  sizing search walk to the bottom-left corner of its own grid and report a 170.45 pct IRR on GBP
  3,554.76 of capex - GBP 332.50/kW installed against the same library's GBP 618/kW grid-scale
  benchmark. Now GBP 5,111.76 at that point, PLUS 43.8 pct.
- **4. BM HAS A DURATION TERM, 1.70x, DOWN at 0.5 h.** GBP 17.47/kW/yr is the **2-hour row** of a table
  the same key publishes as {1h: 10.25, 2h: 17.47, 4h: 28.74} and it was paid in full to a 0.5-hour
  asset. `_assumptions.duration_rescaled_rate` moves it onto the candidate's own published node by
  FLOOR LOOKUP - never interpolation (D28/D35) - preserving the multiplier the quoted rate applied to
  the reference node. GBP 13.98 -> GBP 8.20 per kW/yr at 0.5 h and 1 h; GBP 23.00 at 4 h; unchanged at
  2 h. **ANCILLARY is deliberately NOT adjusted**: its library table is keyed by product and publishes
  no per-duration node, so there is nothing sourced to rescale onto and none is invented.
- **5. THE CM DE-RATING LANDS ONCE, 4.55x, UP - and EBITDA-NEUTRAL.** The consented GBP 4.77/kW/yr is
  already the cleared GBP 27.10/kW/yr times the 2 h Storage Duration-Limited bin, less the cut, and the
  volume was de-rated AGAIN at 0.5 h: 0.2198 x 0.0557 = 0.01225 against a correct single 0.0557. The
  reference de-rating is divided back out, leaving one factor at the candidate's own bin. GBP 0.2657 ->
  GBP 1.2088 per kW/yr. **Net effect on EBITDA is approximately zero** because D58 limb 2 nets
  `min(CM revenue, windowed levy credit)` off the reported credit, and this is stated on the record so
  nobody books a phantom uplift.
- **6. ONE WARRANTY BUDGET ACROSS ALL STREAMS - the eighth built-tested-never-called key gets a reader.**
  `ancillary_services.study_basis_stream_exclusivity_matrix` had ZERO callers in `src/`. Rule 2's
  budget is now enforced: the response service's own measured throughput (DCL, 58.1 kWh/kW/yr, the D56
  favourable end of 58.1-69.0) is deducted from the MILP's `cap_rhs` exactly as the load-service
  obligation already was. The audited runs spent **119-125 pct** of a cap the solver treats as binding.
  **Rules 1, 3, 4 and 7 stay UNWIRED** - D58 limb 1 settles them as waived - and a test fails if a later
  session enforces one. The ancillary revenue is unchanged; only the headroom the priced dispatch
  competes for moves.
- **7. THE AGREED-CAPACITY NETTING PROOF COVERS DFS.** `_floor_value` omitted
  `dfs_utilisation_value_gbp`, now 6.4-6.8 pct of revenue, so DFS given up to shave a peak was booked in
  both DFS and the capacity credit. Lifted to module level so the proof is directly assertable.
- **One label repaired after reading the published run.** The availability-leg note claimed
  "DERIVED FROM THE AWARD'S OWN SERVICE WINDOWS" in the same sentence whose parenthesis said
  "PUBLISHED BY THE AWARDED SERVICE ITSELF": the prefix was hardcoded before fix 2 gave the published
  count precedence, so it went stale the moment the ladder grew a rung. Each rung names itself now.
  No figure moves; rendered artifact text does.
- **VERIFIED on a published run** (`runs/quote_run_42f8eb76f29f8b74`, `bishops_cleeve_ash_plant`,
  `gb_v1_12`, against the same site's pre-fix run `runs/quote_run_fd7dd61d456ccf23`). At IDENTICAL
  sizing-grid coordinates the three rate/volume fixes land on the audit's own predicted multiples:
  LOCAL_FLEX_PICLO **23.364 -> 1.131** GBP/kW/yr (20.66x DOWN, audit predicted 20.6x),
  BALANCING_MECHANISM **13.981 -> 8.204** at 0.5 h (1.70x DOWN, exact) and unchanged at the 2 h
  reference node, CAPACITY_MARKET **0.268 -> 1.209** (4.51x UP; the audit's 4.55x differs by the
  library's own rounding of 5.96). Run level: project IRR **35.93 pct -> 12.58 pct**, payback
  **2.63 -> 6.16 yr**, and the selected system moves off the corner from **2.316 kW / 0.59 h** to
  **9.300 kW / 2 h**. All 12 stream-site cells PASS the D57 acceptance matrix. The D58 CM netting
  re-ran on the corrected figure and removes **GBP 44.36** from a GBP 346.30 credit, which is the
  EBITDA-neutrality proof for fix 5.
- **Reachability, per fix:** `valuation.py` (the clamp, the hours, the CM re-basing, the BM duration
  term), `finance/cashflows.py` (the fixed capex term, both shapes), `milp_solver.py` (the throughput
  deduction, `_floor_value`). Each has a test that turns RED when its production line is deleted; all
  eight mutations were run and the files restored byte-identically with md5 recorded in the commits.
- **Not fixed, and named rather than left silent:** the ancillary duration term (no sourced
  per-duration table exists); the Elexon MID timestamp join that fails on 61 pct of Harwell's
  half-hours, so WHOLESALE_P415 reads GBP 0 there for a data-join reason; the LOAD_SERVICE years-11-15
  re-contracting tail on a one-off avoided capex; the DFS summer-window annualisation label; the three
  mutually contradictory note pairs in one artifact; and install/commissioning, which has no sourced
  figure so the fixed capex term stays a FLOOR rather than an estimate.

## 2026-08-19 — D59: the reports run in the owner's order, and no rendered deliverable carries an em dash

- **Olly.** Structure only: no pricing or eligibility logic moved, and D57/D58 are untouched. Recorded as
  **D59** in `docs/decisions.md`.
- **All four artifacts** (`consulting_report.py`, `html_report.py`, `underwriting.py`, `proposal.py`) now
  run: the project IRR first, then revenue per stream, then the graphs; then the methodology; then the
  assumptions register; then the evidence sections. The consulting pack runs eight sections instead of
  seven and **nothing was deleted** to make room: Situation, Complication, Evidence and provenance and
  What would change this all render every exhibit they carried before, lower down.
- **New, on the view model so all four share one set of rows** (`viewmodel.StreamAssumptionRow`,
  `viewmodel.MethodologyView`): a per-stream assumption register (the keys THAT stream cited, resolved
  to value and source, plus the approvals assumed for it under D57 quoted verbatim) and a methodology
  section, which existed in none of the four. Neither function computes, so the provenance claim's
  exhaustive recomputed-at-render list stays true and its AST audit stays green.
- **New charts** (`report_charts.sizing_surface`, `report_charts.milp_selection`): the FULL evaluated
  sizing space as a table on every pack rather than as a fallback that never fired, and a ranking of the
  CONFIRMED candidates on the recorded objective with the selected bar marked and `selection_reason`
  reproduced verbatim. Proxy evaluations are excluded from the ranking by design and the figure says so.
- **Defect found and fixed while wiring it:** the selected-row match was a coordinate pair, and a
  two-stage search re-solves the same power and duration it proxy-scored, so BOTH rows came back marked
  SELECTED. Identity is now coordinates plus stage plus proxy (`report_charts._point_key`).
- **The em-dash exemption is withdrawn.** The memo and the HTML report were exempt so that
  `quote.warnings` stayed byte-for-byte; the persisted campaign packs carried **100 and 101** em dashes.
  All four are scrubbed at the document seam now, the record is untouched, and
  `test_every_warning_is_surfaced` compares `fmt.no_dashes(warning)` with its reasoning in the docstring.
- **Three standing invariants the new sections had walked into, found by an adversarial pass of the
  existing suite and fixed in favour of the invariant.** (1) The surface carries **no per-candidate
  pound**: a proxy candidate's revenue must never read as a measurement, and a candidate figure must
  not sit beside a real selected spec. (2) The per-stream register **cites** a statement several
  streams share by its numbered note rather than reproducing it, which is the anti-bloat rule the
  numbered notes exist for; `formatting.stream_notes_markdown` then gives the customer proposal the
  numbered notes the other three already carry, so the citation resolves inside that document too.
  (3) `viewmodel._stream_assumptions` is built from the **split**, not from `optimisation_result`, so
  it inherits the gated-citing rule: reading `gated_upside` directly had put Wholesale (P415) into an
  INDICATIVE memo, which the System Brief forbids.
- **Verified on the two persisted campaign runs** (`quote_run_5d6d272f07957414` harwell,
  `quote_run_92d091686f131fb5` grundon_tanhouse): the owner's eight-section order, 36 contiguous
  exhibits, 149 and 145 surface rows against 149 and 145 recorded evaluations, and **zero em and zero en
  dashes in all four artifacts of both**. Pinned by `tests/test_report_order_d59.py`, 17 assertions on
  rendered bytes from a record built in the test file; 11 of 11 mutations of the new production lines
  turn it RED.

## 2026-08-18 — D57 items 4-8: every revenue stream OPEN and PRICED on the loop site, and the one double-book that survives it netted

- **Olly.** Continues D57 items 1-3 (commit `7405d4b`). The acceptance test is the top-level
  `value_streams` of a published run — the layer a reader reads — scored per stream per site. On
  `bishops_cleeve_ash_plant` the baseline was **2 PASS / 10 FAIL**; it is now **11 PASS / 1 FAIL**,
  with the eight-site matrix baselined at 16 PASS / 80 FAIL of 96.
- **Item 5 (finished from the previous session's WIP).** The reservation envelope in
  `milp_solver._compile_commitments` was `min(power − aux, site load)`; the local-flex award bound was
  `min(declared, min in-window metered import, end-of-tenor capability)`. Both are consequences of
  export consent being ABSENT, which D57 assumes approved. The cap did not shrink local flex, it
  DELETED it: five of nine sites have zero minimum in-window metered import, and a zero-load in-window
  instant FORCED net charging of the committed kW, which made **138 of 139 candidates INFEASIBLE**.
  **The sign was also wrong, independently of D57:** NGED's Port Ham award is Demand Turn Up — it pays
  the asset to CHARGE — and turning demand up consumes import HEADROOM, which is largest exactly where
  metered import is smallest. The measured minimum is still reported; it no longer binds.
- **Item 5b.** The availability leg was structurally £0 on every run: the call site passed
  `contracted_mw=None, window_hours=None`. It now reads the award's own committed kW and derives its
  contracted hours from the windows the award declares — 6 h/day × 183 days = **1,098 h/yr** on
  Bishops Cleeve, exactly the figure NGED publishes for that window. The rate is the site's OWN zone's
  awarded clear (`local_flex_piclo.awarded_zone_clears`, whose note said "no code prices an award from
  this entry" — the refusal limb 5b retires). A row from a different licence area is never substituted.
  Measured: £0 → **£149.81/yr** (Bishops Cleeve) and £0 → **£5,887.70/yr** (Harwell).
- **Item 6.** `AGREED_CAPACITY_REDUCTION` returned CONDITIONAL, so it moved no P50, P90, DSCR, IRR or
  candidate selection. D53 chose that pending an owner attestation of the DNO renegotiation and **D57
  supplies it**: the gate is OPEN, the firmness CONTRACTED. The three facts the docstring argued from
  are now the CONFIRMATION ASK published beside a priced figure.
- **Item 7.** `battery_opex.parasitic_load_fraction_of_power` **0.01 → 0.005** in `gb_v1_12`. The two
  parasitic keys had been reconciled BY SCOPE, which left the dispatch and finance reading twice what
  the value stack read for one physical quantity — £7,869/yr on Harwell's 320.7 kW candidate. Both ends
  are cited to the same primary source (PNNL-28379), so D56 takes the favourable end. The release
  header's "NO EXISTING VALUE MOVES" and the standby key's "NEITHER VALUE MOVES" are both corrected to
  name this one exception.
- **Item 8.** The `JSON-F-30` Capacity Market downgrade is retired: on every campaign site the
  "uncompiled commitment" IS the absent CM agreement, which D57 assumes approved. The entire finding —
  nameplate × de-rating volume, no headroom reserved, the same kW still earning arbitrage — is written
  onto the stream instead. The 0.1 MW DFS Registered Unit Capacity floor is likewise assumed satisfied.
- **Item 4 — the DFS engine branch did not exist.** `grep MarketStream.DFS src/gridq/optimisation/`
  returned nothing: no objective term, no schedule field, no valuation branch, so the only direct NESO
  route open below 1 MW could not be earned by any dispatch at any price. Built as four pieces: a
  service-period mask, a rate, an objective term added to `price` exactly as the windowed levy is, and
  a valuation branch. **Reshaped to the CURRENT 2026/27 zonal service** on the data-sourcing agent's
  measurement of NESO's own event data (7,369 bid rows, 70 events, now in-repo at
  `data/fetched/neso/dfs_util_cur.csv`): the window is 11:00–23:00 with the mass at 17:00–22:00 and the
  peak at 19:00–19:30, the count is 1,170 called half-hours a year, the rate is zone 6's
  volume-weighted accepted £159.71/MWh. The winter 2024/25 basis in the library understates the call
  count by ~4×. The zonal PAIR is taken together because price and call count move in OPPOSITE
  directions across the 12 zones. `K = 1.0`, because the fleet's realised 0.3896 corrects OFFERED
  against SETTLED volume and cannot apply to a dispatch-MEASURED delivery.
- **`LOAD_SERVICE` had no mechanism at all.** Its gate returns CLOSED when a site declares no
  `provisioning_requirements`, so no pricing change could reach it. Nine site records now carry an
  assumed-approved resilience-provisioning contract whose counterfactual is DERIVED AND DECLARED
  (D45): avoided DNO reinforcement at Ofgem's published **average** of £430/kVA (the Demand HCC row
  reads "£1720 (four times the average cost of reinforcement)"), annualised over the declared 10-year
  tenor at the library's own 7.5% adequate-credit rate. The £1,720/kVA cap is expressly NOT used — it
  is a liability ceiling reached by 2.8% of offers. Ofgem's own Access SCR caution travels with the
  figure: reinforcement below the cap is socialised into DUoS, so the avoided capex is often £0 to the
  CUSTOMER and the real counterfactual is queue delay, which nobody publishes.
- **`WHOLESALE_P415` — a flat export price can never clear an export decision.** The study solver
  priced export at one annual mean. On these sites the delivered cost of a charged kWh plus its
  marginal degradation is ~£199/MWh and the mean is £87, so the optimiser rationally never exported and
  the stream read £0 — which sounds like "export is worthless" and only says "the average of a volatile
  series is below marginal cost". Export is now priced on the **real full-year half-hourly MID series**
  (17,521 half-hours, in-repo, sha256 matching the `gb_v1_12` key), where 64 half-hours a year clear
  that bar and 23 clear £250/MWh. The settlement-period convention was confirmed against the data
  rather than inherited (SP1 = 00:00 local; the dearest period of the year is SP38 = 18:30–19:00). A
  partial match is REFUSED rather than part-filled, with a bounded, disclosed 4-interval tolerance for
  the 46-period and 50-period clock-change days. The reported £ is now the objective's own export term,
  not the flat tariff re-multiplied onto volume the peaks earned.
- **The £0 gated-upside `WHOLESALE_P415` placeholder is dropped, not relabelled.** Its label was
  correct prose that a KEYED reader never saw: two entries carried the same `stream` id, so any
  consumer resolving the stream by name got whichever it read last and a priced CONTRACTED entry could
  be shadowed by a zero.
- **THE ONE DOUBLE-BOOK D57 DOES NOT RELAX, and it is the only figure here that goes DOWN.** The
  £63.00/kW/yr T-4 2026/27 clearing price is BOTH what a CMU earns and the reason the supplier's CM
  pass-through stepped to 0.30581 £/kWh, which the dispatch avoids through
  `tariff_stack.windowed_levies.CM_peak`. Same auction's money, opposite directions, one capability —
  and D57's preserved clause forbids a pound appearing in both a cost line and a revenue line.
  `docs/bess_constants_register.md` line 683 measured the overlap at ~£38/kW/yr on
  `bishops_cleeve_mrf`. `finance.cashflows` nets `min(CM revenue, credit)` off the REPORTED credit,
  keeps both streams enabled, and leaves the levy in the MILP objective so no dispatch decision
  changes. Seven of nine campaign sites carry the levy.
- **Limb 4 was not being met.** The assumptions page read only the site record's `DerivedAssumption`
  list and `quote.warnings`; every D57 approval the engine emits is written onto the
  `ValueStreamResult` it applies to, so none of them reached the page. `viewmodel._d57_rows` now
  harvests them from both `value_streams` and `gated_upside` into a new FIRST block, quoted verbatim,
  de-duplicated on the sentence with every stream that carries it named.
- **Architecture note, recorded because it was challenged.** The capacity-budget bypass (every stacking
  claim paid in full) stands: D57 limb 5b names stacking exclusivity as assumed satisfied and the brief
  directs that it be stated, not enforced. The PHYSICS is enforced where it belongs — one discharge
  variable, one state of energy, one annual warranty throughput budget, disjoint price arrays over the
  same half-hours, and DFS delivery paying the same £21.3/MWh marginal degradation as arbitrage — so
  the optimiser sells each half-hour ONCE. What D57 waives is the regulatory exclusivity.
- **Not wired, sourced and verified, deliberately out of scope:** the BM £32.35/kW/yr uplift basis, the
  six half-hourly reserve products (PQR £38.80, NQR £33.06/kW/yr), the 21 missing CM T-4 de-rating bins
  and seven library corrections. All are in the data-sourcing agent's report; none moves the acceptance
  matrix.

## 2026-08-18 — The BM / DC-DM-DR volume-basis guard armed on SIZE instead of BASIS, so no consented grid stream could earn a penny (D55)

- **Olly.** Eight tests had failed for days and were repeatedly dismissed as "pre-existing". They were
  the alarm. A consented, rated, size-eligible Balancing Mechanism stream was **absent from
  `value_streams` entirely**, and the cause was a deliberate refusal-to-price added on 2026-08-17:
  `optimisation/valuation.py`, in `_consented_gated_per_mw_yr_value`, returned **£0** for any
  ≥1 MW consented BM / DC-DM-DR stream unless a `CapacityMarketDeratingBasisAcknowledgement` was
  passed to `build_value_streams`. **No production caller passes one** (`milp_solver.py:2312`/`:2384`,
  `mpc_solver.py:298`/`:339`, `engine.py:2811`) and there is deliberately no default, so the refusal
  was unconditional on every real run. The placement branch then routed the £0 stream to
  `gated_upside` on its `annual_revenue_gbp > 0` limb — the symptom the tests reported.
- **The guard's premise was false on the branch the MILP actually takes.** It tested SIZE, assuming a
  size-eligible unit is necessarily priced on a CM-de-rated volume. But `_derated_deliverable_mw`
  returns `schedule.deliverable_capacity_mw` **verbatim** when the solver reserved a capability,
  reading no de-rating factor at all — the plain committed MW `milp_solver.py:1888` sums out of the
  compiled availability windows, which **is** the whole-MW deliverable-power basis D-102 and
  `gb_streams.sellable_volume_mw` prescribe. The note calling it "engine-reserved **de-rated**
  deliverable capability" was the misnomer that made the wrong premise look right; it is corrected.
  So the guard was withholding figures already priced on the CORRECT basis.
- **The guard now arms off provenance, not size**: `_CM_DERATE_KEY` appearing in the volume's
  `assumptions_used`, which is true only on the fallback branch that genuinely multiplies sized power
  by the CM duration factor. Every protection survives, relocated to that branch — the refusal, its
  teaching, its sourced threshold, the truncation pre-emption, the attributed opt-in, the determinism.
  **This is a narrowing of an overreaching guard, not a repeal of the owner's 2026-08-17 decision:**
  "cannot be priced silently" is a rule about the basis, and it is now enforced on the basis.
- **A second defect, on the only route a sub-MW site has.** The engine applied whole-MW sell-order
  truncation **route-blind**, while the sourced predicate says the opposite in terms — "Fractional
  unit MW is offerable inside the aggregate (that is what an aggregator is for), so `whole_mw_orders`
  does not truncate on this route" (`value_stack/gates.py`). So the engine emitted "the unit's own
  0.915 MW is offerable inside the aggregate" and then, in its next note, "truncates to 0 MW as a sell
  order": one number, two contradictory statements, and it disagreed with the proxy path, which
  consumes the route-aware `sellable_volume_mw`. Since **aggregation to the 1 MW registered-unit
  threshold is the only route any of the nine sub-MW campaign sites has** into these markets, this
  alone kept every £ of BM and response revenue out of every reported number. Truncation is now
  mirrored from the sourced predicate: standalone route only.
- **Direction of effect: revenue UP, in two places.** Measured on the real `colnbrook.json` record
  (915.077 kW, the largest campaign site), BM aggregated into a 12 MW portfolio moves **£0 →
  £27,452.31/yr into the priced floor**. On the synthetic 4 MW / 2 MW-reserved fixture, £0 → £60,000.
- **The 1 MW threshold is untouched and proven so.** `verdict.eligible` is unchanged; D-92 still
  refuses before any volume is read. Verified on all nine real campaign site records with consent
  GRANTED to both grid streams at £30,000/MW/yr and the unit sized at the whole connection: BM and
  DC/DM/DR = **£0** on every one, neither reaching the priced floor.
- Mutation-verified four ways (guard re-armed on size; guard deleted; truncation made route-blind;
  truncation removed), each RED, `valuation.py` restored byte-identically (md5
  `cc8334a261f769e54fc031296b2cdaa8` before and after all four). **Four tests added:** one per
  corrected half, one pinning that the guard's key cannot drift out from under it (the silent failure
  that would publish the 9×-understated figure the guard exists to withhold), and one guarding
  against over-correction — a standalone fractional unit is still truncated away.
- **STILL BLOCKED for the nine campaign sites, and this is the decision-relevant part.** Two pieces of
  plumbing, both outside this lane's file allowance, stand between D55 and a reported number. Measured
  on the real `colnbrook.json` record, aggregated into 12 MW, consented at £30,000/MW/yr:

  | precondition | £/yr | placement |
  |---|---|---|
  | a CM availability-window commitment reserved a capability (`deliverable_capacity_mw` = 0.915) | £27,452.31 | **priced floor** |
  | no commitment, so `deliverable_capacity_mw` is `None` | £0 | gated_upside, correctly refused |

  1. **`GateContext.portfolio_mw` is never populated anywhere in `src/`.** The only production
     construction is `sizing/deliverability.py:287` and it does not set it, so `verdict.route` can
     never be `"aggregated"` on a real run — the aggregation arithmetic is corrected but **inert**
     until a portfolio input is plumbed through to the gate context.
  2. **`deliverable_capacity_mw` is set only when a `CAPACITY_MARKET` availability-window commitment
     is compiled** (`milp_solver.py:1880-1888`). Without one the volume falls back to the CM duration
     de-rating and the guard refuses — correctly, on the corrected reading. So a site consenting to
     BM but not to the Capacity Market still earns £0. Resolving that means either giving BM/ancillary
     their own reservation descriptor or adopting `sellable_volume_mw`'s plain-`power_mw` fallback;
     both live in `optimisation/milp_solver.py`, and the second retires the 2026-08-17 mechanism
     outright, so it is the owner's call rather than a lane decision.

## 2026-08-18 — The pricing basis is detected from the RECORD, and every surface states it at the top (D46/D55)

- **Olly.** `outputs/viewmodel.py` detected the study basis by matching the literal
  `"STUDY BASIS (D46)"` against `QuoteRecord.warnings`. `AllStreamsEnabledStudyBasis.LABEL` begins
  `"STUDY BASIS (D55)"`, so **on the basis that prices every revenue stream into the priced floor the
  match failed**: `vm.study_basis` came back False, all four renderers dropped their banner, and the
  customer proposal called the run "the underwriting run" three times. The pack titled
  "# GridQ: Underwriting pack" stated its basis in one bullet at line 193 of 219, under "## Warnings".
  Measured and reproduced on a real bishops_cleeve_mrf record. Direction of effect: **UP**, and the
  worst direction available, because the run that prices the whole barred grid stack was the one
  presented as an underwriting output.
- **Detection is now structural.** `outputs.detect_study_basis` reads `QuoteRecord.study_basis_id`
  first and resolves the banner through `schemas.study_basis_label`, so the label is always the
  basis's **own** `LABEL` and a D55 run can never be described in D46's export-only words. The
  warnings-prefix match is **kept as the legacy fallback**: a record persisted before the field
  existed carries `study_basis_id=None` even when a study basis priced it, and `None` means "this
  record does not say", never "this was an underwriting run".
- **The id-only record is the case that discriminates the two designs.**
  `assemble_quote_record(..., study_basis=b)` persists the id and writes no prose note (the note is
  appended by `generate_quote`), so such a record was invisible to every renderer. It is now detected,
  and the surfaces that promised "the reason is reproduced verbatim in the warnings" say instead that
  the record names the basis by id and carries no prose statement, naming the id. A pointer to text
  that is not in the document is the same class of false claim as no banner at all.
- **Position, not just presence.** Assertions compare indexes: the basis sits above the bankable
  banner in the memo, above the first section heading in the proposal, in the consulting report's
  masthead and browser tab, and above the summary card in the HTML report. A banner that regresses
  into a footnote fails even though the string is still in the file.
- **The frontend stated the basis on NO screen, on either basis** (zero matches for `study_basis` in
  `app.py` or any template). It now does: a leading banner plus a chip in `_statusbar.html` (included
  by all four run screens), a **Pricing basis** column on the Projects listing, and a qualifier under
  the dashboard hero tiles, which headline the newest solved run's IRR and payback. All of it formats
  `_basis_display(detect_study_basis(...))` and decides nothing (CLAUDE.md §8). The no-basis wording
  is "NO STUDY BASIS RECORDED" rather than "underwriting basis", because the former is true of a
  legacy record too.
- **Tests:** `tests/test_study_basis_reaches_every_surface.py`, 28 cases over three record shapes
  (pipeline / id-only / legacy) x two bases x four artifacts x four screens, every record built in
  the file because `runs/` is gitignored and a test reading a real run directory passes vacuously on a
  fresh checkout. Mutation-verified three ways: blinding the id read reddens 12 (all id-path);
  restoring the old `"STUDY BASIS (D46)"` literal reddens 4 (all legacy-D55, the original defect);
  deleting the statusbar block reddens all 6 frontend cases.

## 2026-08-18 — D56 rule 4: one dedicated assumptions page, in all four packs, asserted on the rendered bytes

- **Olly.** Every pack now ends on a **dedicated assumptions page** enumerating every assumption the
  run rests on, on a page of its own (`break-before: page` in both HTML packs, a page-break div plus a
  rule in both Markdown ones). Six classes, never merged, because they carry very different weight:
  **optimistic selections (D56)**, **owner attestations and asserted commitments (D54/D55)**,
  **derived inputs (D45)**, **pending permissions**, **evidenced non-applicable components**, and the
  **per-key library register**. Each row carries the key, the value used, the source or published
  range, the status, the **direction of effect** and the **exact confirmation ask**.
- **The optimism is visible, not implied.** `tariff_stack.windowed_levies.CM_peak` prints
  **0.30581 GBP/kWh** beside its published range (**5.313 to 31.382 p/kWh**, npower's monthly series)
  and the **April 2025 comparator, 17.057 p/kWh**, so a reader can see which end of the cited range was
  taken. A row enters that class only when the record's own basis declares `DIRECTION OF EFFECT: UP`;
  `duos_red_rate` raises revenue too but claims no range, so it stays a derived input.
- **Direction of effect is read, then policied, then declined** — the record's own declaration first,
  a published per-key policy (printed with its reasoning) second, and `not established` where neither
  speaks. `tariff_stack.delivered_cost_reconciliation` prints *not established* because its basis says
  the direction of the error is unknown, which is the honest answer and not a gap.
- **The page reads the record and never the brief.** `QuoteRecord` carries no `study_basis` field and
  the gb_v1_11 campaign runs carry no study-basis warning, so the page says *"No named study basis is
  stamped on this record"* and then states that a run resting on an attestation and an optimistic pick
  is in-house feasibility work that must not be presented as an underwriting output. A run that DOES
  carry the label gets the record's own words verbatim.
- **Gate-emitted records are on the page.** Records the gates emit reach the quote as warnings and are
  deliberately never written back onto the site, so the page parses the pipeline's own disclosure
  format (`DerivedAssumption.statement`, split from the tail with `rpartition`) and dedupes against the
  structured site records. `tariff_stack.delivered_cost_reconciliation` appears for exactly this
  reason. An unparseable disclosure is rendered in full rather than dropped.
- **Empty is explicit.** An empty class renders *"None recorded"* plus the reason, and a render with no
  site record says *"NO SITE RECORD WAS SUPPLIED"* rather than showing an empty register: "no pending
  permission on this run" and "nobody looked" are different facts.
- **Four call sites, mutation-verified individually** (`underwriting.py`, `proposal.py`,
  `html_report.py`, `consulting_report.py`), because the classic version of this defect is a page wired
  into three packs out of four. 25 tests in `tests/test_assumptions_page.py`, all asserting on the
  bytes of packs rendered end to end from a `QuoteRecord` built in the test file; no skip, no xfail.
  Correct on a **no-spec** record, which is the shape every live campaign run is in.

## 2026-08-18 — D55: every revenue stream gets a sourced price and a dispatchable HH shape, on a named all-streams study basis

- **Olly.** Cut `assumptions/gb_v1_12.yaml`: **ten new keys** giving a sourced **price AND a
  dispatchable half-hourly shape** to every stream a sub-MW behind-the-meter asset is normally barred
  from — wholesale/P415 (a **full year** of real Elexon MID, 17,521 half-hours, as a 12×48
  month-by-settlement-period matrix), the Balancing Mechanism (a **full year** of real DISEBSP system
  prices plus NESO's published Stage-5 PSA skip rate, VLP route, 41.2%), DC/DM/DR availability by EFA
  block net of its own wear, the **forward** Capacity Market (£27.10 × de-rating = £5.96/kW/yr at 2 h,
  **not** the closed £63.00 headline), DFS, and the aggregator route to market. **Every gb_v1_11 value
  is carried forward byte-identical**; both underwriting refusals stay at zero; every new key is
  `needs_review` / low / URL-cited. `latest_version()` now resolves to `gb_v1_12` — the campaign pins
  `gb_v1_11` and is unaffected.
- **`gridq.schemas.AllStreamsEnabledStudyBasis`** — a subclass of D46's `StudyBasis` that prices all of
  it into the floor with **every award assumed granted**, where "assumed" means a
  **`pending_permission`** record per stream and never a claim that anyone agreed. Reachable through
  the **production** path — `quotes/pipeline.py::generate_quote` reads the basis off the engine's
  duck-typed `last_study_basis()` hook and forwards it into `input_hash` — and **mutation-verified**
  at three separate call sites (drop the forward, the detector, or the band cap and a named test goes
  red). 40 tests, no skip, no xfail.
- **Enabling everything is not adding it up.** Seven verbatim-sourced exclusivity rules plus two
  physical ones (one kW sold once per half-hour; one 730 kWh/kW/yr warranty budget shared by all
  streams) are landed as data and enforced by the basis. The naive sum, £90.23/kW/yr, is physically
  impossible — a test asserts it shows negative throughput headroom.
- **The Phase 1 parasitic "2× disagreement" is resolved by sourcing, with no value moved**: PNNL-28379
  measures auxiliary load at 10 kW and PCS switching at a further 10 kW on a 2,000 kW BESS, so
  `parasitic_load.standby_kw_per_mw` = 5.0 and `battery_opex.parasitic_load_fraction_of_power` = 0.01
  are two **scopes**, not a contradiction. Both move `placeholder` → `needs_review`.
- **The hypothesis test, reported as a finding rather than a target:** six of nine sites move from
  −11%/−16% project IRR to **+0.9% to +4.2%**; 8% needs £81.78/kW/yr of EBITDA against £50–64
  delivered. **The residual gap is opex (£41–45/kW/yr measured against £20 assumed), not market
  access.** Full working: `scratchpad/all_streams_enabled.md`.
  **Read those IRRs with three labels attached, none of them optional.** (a) They are **STUDY-BASIS**
  figures and **may never be presented as an underwriting output** (D46/D55). (b) They are
  **ANALYTIC, not a MILP output**: five of the six streams are priced in the library and are **not
  yet dispatched** — the hook that would read `option_set()` from the dispatch objective lives in
  `optimisation/milp_solver.py` and `value_stack/gb_streams.py`, both outside this change — so **no £
  from them reaches a reported number today**. (c) The calculation is an **annual aggregate that
  relaxes within-day timing**, so it is an **UPPER BOUND** and its direction of error is **UP**; it
  also omits the 20% perfect-foresight haircut, four downward ancillary haircuts and any P90 haircut.
- **Post-verification (same day): an adversarial review REFUTED the basis separation, and it was right
  about the one that mattered.** The renderers detected a study basis by the literal string
  `"STUDY BASIS (D46)"`, so a D55 run was **invisible to all four of them** — the customer proposal
  called it an "underwriting run" three times and the underwriting pack carried no basis banner.
  Since D46's own doctrine is that the labelling *is* the entire mitigation, this was the most serious
  defect in the change. Fixed basis-agnostically, with each renderer now printing the **record's own**
  label rather than its own hardcoded wording (a prefix widening alone would have described a
  six-stream basis in export-only words). Also fixed: nothing in `src/` read
  `study_basis.confidence_ceiling`, so the "capped at LOW" sentence was true only by coincidence —
  the pipeline now hands the record the weaker of the two ceilings (measured: medium → low). The
  review's third claim, that the two bases could share a `run_id`, is **refuted by measurement**: the
  basis already reached `input_hash` through the engine's prose warning, though it now does so through
  a structured field as well. Details in `docs/decisions.md` under D55.

## 2026-08-18 — D54 verified end-to-end on real runs: the firmness moves, the money does not, and the availability leg is why

- **LOCALFLEX-3 (Olly).** `local_flex_published_price_context` told every reader "the availability
  leg is priced at £0 **because no tender has been AWARDED to this site**" — unconditionally. On the
  nine sites that now carry an owner-attested award that sentence is **false**, and it contradicted
  the sibling sentence `_local_flex_conditional_reason` wrote onto the same record ("an award IS
  recorded"). This is LOCALFLEX-1 one layer along: a claim about the outside world the function
  never checked, upgraded to one its own input refutes. A site recording **no** award keeps the
  original sentence **byte-identically** (asserted by a hardcoded-verbatim test), so no pre-D54
  record moves. **No £ changes** — the leg was £0 before and after; what changes is that the run
  now states the real cause. Mutation-verified twice (branch neutered → RED; the `check_gates`
  emission deleted → RED; both restored, md5 identical).
- **The measured effect on the return is £0.00, and that is the headline.** A full `gridq run` on
  `colnbrook` (gb_v1_11) against the same run with the award stripped, 151 candidates each:
  `LOCAL_FLEX_PICLO` moves from `in_priced_floor` on **0/151** candidates to **151/151**
  (firmness conditional → **contracted**, gate open) — and **every other number is identical**.
  Best margin −£1,182.67/yr at 45.754 kW × 8.0 h on both arms, floor revenue £707.66 on both,
  0 positive-margin candidates, 0 bankable, nothing selectable, project IRR `None` on both.
  **Delta: £0.00/yr.** The chain is live; the money is not.
- **Why £0: the availability leg is unwired, not merely unpriced.** `optimisation/valuation.py:523`
  calls `local_flex_availability_value(contracted_mw=None, window_hours=None, product_type=None)`
  with literal `None`s. The attested award carries `committed_power_kw`, `windows` and
  `tenor_years` on the record, and **none of them reaches the leg**. Availability is the half an
  SAOU/VAOU award actually pays for, so an attested award can now sit inside the priced floor and
  contribute nothing. `valuation.py` is another lane's file — filed as the top follow-up, not
  touched here.
- **Bishops Cleeve MRF, the best-evidenced site, is numerically unchanged.** Its award is REFUSED
  on measured deliverability (zero metered import inside the published 10:00–16:00 Apr–Sep window),
  so the stream stays CONDITIONAL on **all 181** candidates. Best margin £429.34/yr, project IRR
  −16.03% at that point, 0 bankable — reproducing the pre-D54 baseline £429.34 exactly.
- **The per-site deliverable bounds reproduce, but not to the digit.** Measured today on gb_v1_11:
  Colnbrook **44.387 kW**, Tanhouse **23.591 kW**, Ewelme-invoice **19.637 kW**, Ewelme
  **3.361 kW**; the other five refuse at 0 kW. The D54 decision record states 45.5 / 24.2 / 20.1 /
  3.4 — same four sites, same five refusals, volumes ~2–3% lower than recorded. The bound is
  `floor_kw × (1 − parasitic) × (1 − fade)^tenor`; the decision's figures look pre-retention.
  **Reconcile the decision text against the measurement before either figure is quoted.**
- **Nine of nine site records audited independently** (schema-parse + label assertions): award
  present; `confirmed_by` "Olly (site owner), 2026-08-17"; basis attests owner attestation under
  D54 and **never** claims register verification; the five zoneless sites each name the donor
  (Wallingford), declare the substitution and cite D54-overrides-D30; the four own-zone sites claim
  no substitution; every site carries the `local_flex_awards.reference` ask as a
  `pending_permission` and a `confidence_ceiling: low`, which the band's weakest-fold
  (`quotes/confidence.py:555`) turns into a **LOW** band — observed on both live runs.

## 2026-08-17 — D54: owner-attested local-flex awards reach the priced floor, and the deliverability bound turns five of nine into an evidenced negative

- **The gap, and it was the classic one (Olly).** `AvailabilityCommitment` → constraint seam →
  `produce_gate_context` → `GateContext.local_flex_tender_open` → `LocalFlexPicloStream` OPEN →
  `Firmness.CONTRACTED` → priced floor has existed since W7-1/W7-4. **Nothing in production ever
  built the first link:** the only way onto the seam was `enumerate_sizing(extra_constraints=…)`,
  whose sole caller in the repo was a test. `_TENDER_WINDOW` carries no payload, so the solver
  logged it "declared, not contracted", `tender_window_discharge_mwh` was `None` on every run, and
  the utilisation leg was £0 whatever the gate said.
- **The bridge is `LocalFlexPicloStream.constraints(site, assumptions)`**, which puts the award on
  `dispatch_constraints_for` — the one function `sizer`, `evaluator`, `underwrite`, `two_stage`,
  `MilpEngine` and `demo` all call. New `SiteModel.local_flex_awards` (additive, pop-if-None hash
  stability); new optional `AvailabilityCommitment.confirmed_by` / `basis` so the asserter travels
  with the assertion. All five production lines are **mutation-verified**: break the line, the
  suite goes RED, restore, md5 matches.
- **`confirmed_by="contract-record"` was a false claim and is gone for attested awards.**
  `produce_gate_context` stamped every local-flex commitment as a sighted contract record. It now
  carries the award's own confirmer and basis, and falls back to the old sentence verbatim when a
  commitment names neither — so no pre-D54 record moves.
- **The measured result, and it is the important part.** Modelling the award at the site's
  connection scale made **138 of 139 candidates INFEASIBLE** on a real Bishops Cleeve MRF run: an
  availability window reserves `discharge − charge ≤ min(power − parasitic, load_t) − committed`,
  and the MILP refuses rather than relaxes. The volume is now bounded by the site's **minimum
  metered import inside the award's own service windows**. Where that is **zero there is nothing to
  turn down**, so no constraint is emitted and the stream stays CONDITIONAL with the reason stated.
  That is **five of nine sites — including both Bishops Cleeve sites**, the only two with a real,
  open, undersubscribed award: MRF imports zero for **16%** of the published 10:00–16:00 Apr–Sep
  window, Knowl Hill for **83%**. A high-confidence negative, not a data gap.
- **The published registers contradict the attestation at six of nine sites, and say so on every
  run.** Port Ham is awarded and 1.6% subscribed but names no Grundon entity; Wallingford is awarded
  and 99.7% full; five sites are in no tendered zone across six published SEPD postcode lists. The
  five zoneless sites' terms are a **declared cross-zone substitution** naming Wallingford as donor,
  with D54 overriding D30 for those sites only.
- **Two disclosed overstatement risks, both in another lane's file.** The utilisation leg prices off
  a **GB-wide Piclo median** (£135.67/MWh) that is **1.70×** Port Ham's published clear (£79.75);
  the **availability leg is £0** on the engine path (`_local_flex_value` passes
  `contracted_mw=None, window_hours=None`), which understates SAOU/VAOU awards. Both are named in
  the gate warnings. `local_flex_piclo.awarded_zone_clears` added to gb_v1_11 as sourced context —
  never a default, D30's empty home unchanged.
- **Direction:** P50/EBITDA/IRRs **UP** where an award models; P90 and the lender DSCR **unchanged at
  every site** (`LOCAL_FLEX_PICLO` ∈ `_NO_P90_IN_TERM`). Confidence stays **LOW** via a
  `pending_permission` register entry on `local_flex_awards.reference`. Magnitude is small — the
  whole stream is worth £434–£1,168/yr at Bishops Cleeve, where it does not model at all.

## 2026-08-17 — MICASC-3: the avoided agreed-capacity charge and the TCR band step are priced, and the audit that proposed them is refuted in both directions

- **The defect, confirmed by reading the code (Olly).** `valuation.build_value_streams` returned
  `MIC_ASC_HEADROOM = £0` **unconditionally**, on the rationale that "the connection-headroom value is
  realised through the connection-bounded arbitrage; pricing it separately would double-count". That
  rationale is sound for the **arbitrage** reading of headroom and only for it. Two capacity-priced
  amounts fall when the peak falls and neither was modelled anywhere: the avoided **agreed-capacity
  (ASC/MIC)** charge and the avoided **TCR fixed residual band**. Both were structurally zero because
  `milp_solver.py` and `engine.py` compute the capacity line as
  `capacity_charge × (asc_kw or mic_kw) × 365.25` — a **battery-invariant constant**.
- **Priced on a new stream, `MarketStream.AGREED_CAPACITY_REDUCTION`, deliberately NOT on
  `MIC_ASC_HEADROOM`.** That stream is a `FloorStream` (OPEN ⇒ FIRM) and this money is conditional, so
  sharing the id would have put conditional money inside the priced floor; the two are also gated on
  different facts (MIC/ASC headroom needs the connection to BIND, this needs a shaveable PEAK, and
  those are different sites — Harwell has **0.00 kW** of headroom and earns here).
- **CONDITIONAL, argued explicitly, and the consequence stated: this moves NO IRR and NO selection.**
  FIRM was considered and rejected on three grounds: the charge falls only when the customer applies
  to the DNO to change the connection agreement (a permission under D45, never written as granted);
  the band leg follows DCUSA Schedule 32 re-banding on a **24-month average MIC** basis and so cannot
  arrive in year 1; and a reduced capacity that is later breached costs excess-capacity charges at the
  same £/kVA rate, so the saving is contingent on the battery performing at every annual peak. The one
  genuinely FIRM variant — avoided **excess-capacity** charges on a site already breaching — is **£0
  across the whole portfolio** (no site's metered peak exceeds its agreed capacity). Bending the
  firmness to make a negative IRR look better is the failure this decision exists to prevent.
- **THE AUDIT'S £3,055/yr ILLUSTRATION IS REFUTED IN BOTH DIRECTIONS.** (a) It costed the band step at
  the **DUoS banded fixed line alone** (£329/yr for band_2→band_1 at Bishops Cleeve MRF) and missed the
  **TNUoS non-locational demand residual**, which is banded on the *same* agreed capacity and is ~6x
  larger (£2,087/yr on the same step) — the band leg is understated **7.3x**. (b) Far more importantly,
  it credited the battery with capacity the customer can release **today, with no battery at all**:
  £15,039/yr at Bishops Cleeve MRF, £18,632/yr at Knowl Hill (invoice-anchored). The model now takes
  the **tightest capacity the site's own metered peak already supports** as the counterfactual and
  credits the battery only with the increment below it.
- **An OVERLAY solve, because pricing it off the floor schedule would have reported £0 at every size
  the sizing grid explores.** MEASURED: the existing `_EPS_PEAK` tie-break shaves **0.000 kW** at
  7.4 kW and at 25 kW on Bishops Cleeve MRF — shifting discharge onto the annual peak costs real
  red-band money and an epsilon cannot pay for it. The overlay attaches the site's own £/kVA/day rate
  to the existing residual-peak epigraph. The **floor solve is untouched and byte-identical** (the
  coefficient stays `_EPS_PEAK` when no price is passed): putting the capacity price in the floor
  objective would let the optimiser sell FIRM bankable revenue to buy CONDITIONAL unbankable revenue.
- **No double count, arithmetically rather than by assertion.** Whatever firm revenue the overlay
  dispatch gives up to buy its lower peak is measured on both schedules and **subtracted**.
- **Bounded by the ANNUAL WORST CASE, never an average.** A reduced capacity is breached by one
  half-hour, so the new capacity clears the highest net import in **every** settlement period of the
  metered year, plus a **10%-of-rated-power security margin** — a **labelled modelling convention, not
  a published rate** (none exists; none was invented), direction **DOWN**. A typical-period proxy solve
  and a part-year meter both **refuse to claim** rather than extrapolate.
- **Register caveat corrected.** `docs/bess_constants_register.md` said of the TNUoS residual "entirely
  unavoidable by dispatch. A BTM battery cannot reduce this." That is **wrong**: the band is allocated
  on agreed capacity, not consumption. Corrected in place with the measured step sizes.
- **Reachability, the rule this repo has broken eleven times.** `tests/test_agreed_capacity_reduction_priced.py`
  drives `MilpDispatchSolver.solve` — the entry point `gridq run` uses — and reads the stream off the
  returned `DispatchResult`. **Eleven mutations verified RED and restored byte-identically** (md5
  checked), covering the overlay call, the objective coefficient, the `_solved` hand-off, the valuation
  branch, the registry entry, the counterfactual, the netting, the TNUoS leg, the firmness, the margin
  and the part-year refusal. One assertion that stayed GREEN under mutation (a note-text-only check on
  the netting) was rewritten to check the subtraction — the hollow-test failure mode, caught here.
- **MEASURED EFFECT ON A REAL RUN, and the invariance is the design working.** Bishops Cleeve MRF,
  gb_v1_11, before vs after: best project IRR **−4.1268% → −4.1268%**, best annual margin
  **£429.34 → £429.34**, positive-margin candidates **57 → 57**, selected candidate **none → none**
  — byte-identical. The new stream is live on **30 of 31** confirmed candidates (£272–£4,004/yr) and
  £0 on all 150 typical-period proxy points, which refuse to claim. Knowl Hill (invoice-anchored):
  live on **27 of 27** confirmed, £21–£3,961/yr against a £4,106/yr priced floor at the best point;
  no IRR resolves on that site at any size, before or after. **No candidate becomes selectable
  through this lane, by construction** — CONDITIONAL revenue is outside `_PRICED_FIRMNESS`, and it
  must be, because nobody has applied to the DNO. The lane's output is gated upside plus a
  commercial finding, not a better IRR.
- **The twelfth built-tested-never-called defect was hit INSIDE this change, and caught by running
  the CLI rather than by the suite.** The overlay was first wired into `MilpDispatchSolver.solve`;
  every direct-solve test passed and a real `gridq run` reported **£0 on all 181 grid points**,
  because the sizing search and confirm stage drive the engine through `PreloadedDispatchSolver`,
  which calls `_solve_milp`/`_solved` directly. Fixed by making `capacity_reduction` an **explicit
  argument** on `_solved` — so an omission is a signature error, not a silent zero — and by covering
  both entry points in the suite. One earlier assertion that stayed GREEN under mutation (a
  note-text-only check on the netting) was rewritten to check the subtraction.
- **Runtime cost, disclosed:** the overlay is a second full-year LP — **3.98 s → 9.65 s (2.43×)** per
  full-series solve — so a run's confirm stage roughly doubles. Proxy solves refuse before solving,
  so the sizing search is unaffected. Reducing it to one overlay on the selected candidate is filed
  as a follow-up.
- **Site-data defect found, logged, not fixed:** `harwell.json` carries the LV**4** TNUoS residual
  (38.180103 £/day) against a 213.81 kVA MIC, which the published thresholds put in **band 3**
  (14.381374). Harwell's standing charge is overstated by ~£8,690/yr.

## 2026-08-17 — gb_v1_11: the two unsourced flat costs are refuted, the capex that cancels them is corrected, and the private-wire DUoS absence finally reaches the branch that reads it

- **`assumptions/gb_v1_11.yaml` is cut (Olly, decision D49).** Six values move from gb_v1_10, one
  reference key is added, nothing is dropped or renamed, and three keys keep their VALUE while their
  evidence is rewritten. Proved mechanically rather than asserted:
  `tests/test_assumptions_gb_v1_11.py::test_gb_v1_11_priors_identical_to_gb_v1_10_except_the_enumerated_drift`
  fails if any other number moves in either release.
- **`metering_idno.annual_gbp_per_site` 1200 → 0 · DIRECTION DOWN, −£1,200/site/yr at EVERY size.**
  The recurring **battery-incremental** metering cost is zero: a no-export BTM battery adds no
  boundary Metering System, it re-shapes the host's existing import MPAN. Elexon P375 exists
  *because* settlement behind the boundary point otherwise cannot happen, and its trigger (BM/VLP)
  is off the priced-floor basis; DESNZ's own storage taxonomy (EINAS 2025, p.31) puts metering in
  **capex** and has no annual metering line at all. The sourced host-site magnitude is
  **~£400/meter/yr** (LASER Energy, MHHS page, updated 29/08/2025) — one third of what the key
  carried — and it is recorded on the key as the conservative alternative with its confirmation ask.
- **`dno_costs.annual_gbp_per_site` 1500 → 0 recurring · DIRECTION DOWN, −£1,500/site/yr at EVERY
  size.** Contradicted by the verbatim operative text of two DNO charging statements. NGED South
  West, June 2026, clause 6.4: *"The cost of future operations, repairs and maintenance of the
  connection assets and our Distribution System is normally recovered through the on-going charges
  for use of system…"*; clause 5.12 makes any above-Minimum-Scheme O&M a **one-off**. So the £1,500
  was either a double count of DUoS (already priced in the `duos` section) or a one-off in an annual
  field — and the published one-off A&D charge for the sites' own category (>50 kVA LV generation) is
  **£1,557**, within 4% of it. Corroborated by Northern Powergrid, February 2024.
- **Both of those FLATTER every site, so the correction that cancels them landed in the same
  release.** `battery_capex.installed_cost` **500,000 → 700,000 GBP/MW** (+40%), with the
  decomposition scaled uniformly (`installed_cost_gbp_per_kwh` 175 → 245,
  `installed_cost_gbp_per_mw_power_component` 150,000 → 210,000) so it still reconciles at the 2 h
  reference. £500/kW at 2 h was **19% below** the GB grid-scale 2 h benchmark (£618/kW, Modo 2025,
  already in this repo) and **29% below** the only attributable GB C&I figure (£350/kWh, Gridcog,
  28 January 2025). Sub-MW BTM costs **more** per kW than grid scale, not less.
- **Measured, not estimated — through `finance.cashflows.estimate_capex` and `build_cost_lines` on
  both releases** (the capital charge is an illustrative annualisation of the capex delta at the
  register's CEPA/DESNZ 8.9% mid hurdle over 10 years, CRF 0.155135):

  | Size (2 h) | capex | fixed opex/yr | Δ fixed opex | Δ capital charge | **NET** |
  |---|---|---|---|---|---|
  | 25 kW | £12,500 → £17,500 | £3,262.50 → £587.50 | −£2,675.00 | +£775.67 | **−£1,899.33 better** |
  | 50 kW | £25,000 → £35,000 | £3,825.00 → £1,175.00 | −£2,650.00 | +£1,551.35 | **−£1,098.65 better** |
  | 100 kW | £50,000 → £70,000 | £4,950.00 → £2,350.00 | −£2,600.00 | +£3,102.70 | **+£502.70 worse** |
  | 200 kW | £100,000 → £140,000 | £7,200.00 → £4,700.00 | −£2,500.00 | +£6,205.40 | **+£3,705.40 worse** |
  | 300 kW | £150,000 → £210,000 | £9,450.00 → £7,050.00 | −£2,400.00 | +£9,308.09 | **+£6,908.09 worse** |
  | 750 kW | £375,000 → £525,000 | £19,575.00 → £17,625.00 | −£1,950.00 | +£23,270.24 | **+£21,320.24 worse** |

  The two deleted lines are **size-invariant** (`build_cost_lines` charges them with no `power_mw`
  multiplier) while capex scales, so the release helps small sites and hurts large ones, crossing
  over at **84.3 kW at 2 h** — inside the campaign's own 25–300 kW candidate range. Stated as a
  range rather than one number for that reason.
- **The anti-cherry-pick property is a test, not a promise.**
  `test_gb_v1_11_net_effect_is_worse_at_the_reference_size` requires the net at 100 kW to be
  **positive**. Mutation-verified: reverting the consumed capex decomposition to 175/150,000 (i.e.
  keeping only the favourable half) turns it and four other tests RED. **The capex magnitude is
  single-sourced on a commercial blog** and says so on the key at `needs_review`/low confidence; the
  confirmation ask is one real installer quote for a 100–300 kW GB commercial system, which settles
  the largest open number in the model.
- **`financing.debt_gearing_pct` 60 → 50**, the CEPA/DESNZ published notional replacing an explicit
  placeholder. **No priced figure moves:** non-recourse project finance does not exist at this ticket
  size (smallest observed GB facility £42.7m against £17.5k–£525k assets; CEPA received *no* investor
  responses on lithium-BESS return on debt; merchant tenors 1–3 years against a 10-year asset), so
  these sites underwrite **unlevered** per `CLAUDE.md` §2. Corrected for correctness, never to make a
  DSCR appear.
- **Three keys keep their value and gain their evidence.** `business_rates.annual_gbp_per_mw` stays
  £8,000/MW/yr because the VOA publishes no £/MW RV scheme — but the **liability** is now resolved on
  the law: SI 2022/405 exempts storage only where the sources are *"mainly or exclusively"* renewable
  (or for EVCP), to 31 March 2035, so a **grid-charged** battery is rateable and the placeholder
  stands. Small Business Rate Relief is **dead** for these multi-site hosts and stops being treated
  as a mitigant. **Knowl Hill is flagged, not modelled:** 92.1% landfill-gas supply, and biogas is
  limb (iii) — a rating-surveyor question recorded as a per-site DATA ASK.
  `insurance.annual_pct_of_capex` keeps 0.5% with the failed search recorded (direction UP,
  unquantified; the £ **line** still rises 40% because capex did). `battery_opex.annual_fixed_opex`
  keeps 12,000 with its two-source bracket arithmetic re-stated against the new fixed stack
  (separately-booked lines at 1 MW now £11,500/MW/yr; all-in £23,500 against Gresham House's £24,600).
- **What the release deliberately does NOT do, recorded so the omission is not read as a judgement.**
  The **Capacity Market peak-period levy** is not a library key and is not added here. npower Business
  Solutions publishes a *"Capacity Mechanism Peak Rate"* of **5.313–30.581 p/kWh** on 16:00–19:00
  working-day Nov–Feb import, against a best modelled DUoS red-to-green spread of 6.415 p/kWh at these
  sites, and it cannot be carried in a flat `levies` entry at all — that would charge the peak rate on
  **every** half-hour of charging. **Observed in the working tree at the time of this entry:** the
  seven grid-supplied site records now carry `tariff_stack.windowed_levies.CM_peak` = **0.30581
  £/kWh** (months 11/12/1/2, 16:00–19:00 weekdays) from the concurrent windowed-levy engine work, and
  the two private-wire records carry none. **One caveat on that figure, because its direction is
  upward on revenue: 30.581 p/kWh is the MAXIMUM of the published range and an extraction-read cell
  (April 2026).** The April 2025 cell read 17.057 p/kWh and the range floor is 5.313 p/kWh, so the
  choice of cell spans a factor of 5.8 on the avoided-cost value; the live table needs re-reading by
  eye, on a reconciled rather than an initial billing rate. Three site-level levy values are also
  flagged rather than changed (they live in `examples/real/sites/*.json`, not the library): **BSUoS
  0.013115** matches no published tariff period, **CfD_ILR 0.01** is round-number-shaped,
  **NuclearRAB 0.004683** has no source found — and **FiT disagrees between records** (`harwell.json`
  carries `FiT: 0.008`, the other six `FiT_UNCONFIRMED: 0.0049`).
- **A one-off cost is recorded as a key that nothing reads, and it says so.**
  `battery_capex.dno_assessment_and_design_one_off_gbp_per_site` = **£1,557** (NGED South West,
  Section 7 Table D, June 2026). `estimate_capex` prices per MW and per kWh only, so this does not
  enter capex today — **direction of the omission: capex UNDERSTATED by £1,557 per site, which
  flatters every return**, worst at the small sizes (12.5% of a 25 kW battery's corrected capex). The
  wiring is filed as an open item against `finance/cashflows.py`, **not claimed as landed**:
  `test_gb_v1_11_the_unconsumed_one_off_says_it_is_unconsumed` pins the label.
- **The withdrawn capex range, recorded because rejecting it mattered more than the +40%.** The
  £445–1,315/kWh range from Seward/Qadrdan/Jenkins (PSCC 2022) is **domestic** retail pricing for a
  10 kW / 20 kWh school, referenced to one installer's 2021 website; transferring it would have been
  a 7.5× error. `docs/bess_constants_register.md` paraphrased it as *"domestic/**commercial**"* — the
  word "commercial" was added downstream — and **that row is corrected in this change**.
- **R1 REFUTATION CLOSED (MAJOR): the private-wire DUoS branch was unreachable, and the fix is in the
  DATA.** `value_stack/gb_streams.py:946` states the structural truth for a 100% private-wire supply
  — £0 red-band avoidance because no band applies, **no data ask made** — keyed on
  `gates.evidenced_absences(site).get("tariff_stack.duos_red_rate")` returning a `not_applicable`
  record. **No persisted site record carried a `not_applicable` entry at all**: Tanhouse and
  Colnbrook each carried a valueless `derived_input`, which that guard deliberately does not match.
  So Tanhouse's pack still printed `tariff_stack.duos_red_rate (declared, reads NULL …)` — a request
  to a private-wire customer for a CDCM rate no supplier could ever issue them. Both site files now
  record **all three** bands as `not_applicable` (all three, because the guard matches the exact
  dotted key and one record may never vouch for a neighbour). Measured before and after on the real
  records: the ask becomes *"does not apply to this site, evidenced — CONFIRM the absence"*, the
  guard's closing sentence becomes *"Every null above is an EVIDENCED ABSENCE, so there is nothing to
  source"*, and **the £ does not move — 0.00 either way, gate CLOSED, firmness EXCLUDED, out of the
  priced floor.** `tests/test_private_wire_duos_absence_on_real_sites.py` asserts all of it on the
  shipped records through `optimisation.valuation.build_value_streams`, plus a scope guard that the
  other seven site files carry all three band rates and can never take this branch.
- **`examples/real/sites/*.json` are hand-maintained, not generated** —
  `scripts/extract_site_data.py` writes only to `data/sites/` (`harwell_summary.json`,
  `grundon_monthly.json`, `grundon_invoices_2025.json`) — so there is no generator to re-fix and no
  silent reversion path. Verified by inspection of every `real/sites` reference in `scripts/`.
- **Run identity, stated so it is not mistaken for drift.** Every `run_id` changes, because
  `assumption_version` is part of it and the inputs genuinely changed; and Tanhouse's and
  Colnbrook's `input_hash` change again on top of that, because derived records enter the canonical
  hash input (`quotes/assembly.py:86` pops the field only when empty). Packs published on gb_v1_10
  are **superseded, never deleted**.
- **The D47 demo pins are unaffected**: `tests/test_determinism_golden.py` pins
  `ASSUMPTION_VERSION = "gb_v1_4"` explicitly, so no re-mint was needed and none was done. The app's
  release-resolution tests compare against `latest_version()` and now exercise gb_v1_11.
- **One cross-lane interaction the release exposes, reported and deliberately NOT patched.**
  `tests/test_windowed_levy_capacity_market.py::test_windowed_levy_raises_year_one_cashflow_end_to_end`
  (the concurrent windowed-levy lane's own test) passes on gb_v1_10 and fails on gb_v1_11 — measured
  A/B with the live tree and an assumptions directory with and without the new release. **The cause
  is the sizing selection, not the levy.** On the test's synthetic flat-tariff site the sizer picks
  **200.0 kW / 4 h (capex £170,000) with the levy and 200.0 kW / 8 h (£310,000) without it** on
  gb_v1_10; on gb_v1_11's corrected capex it picks **10.0 kW / 4 h (£11,900) with** and **18.05 kW /
  8 h (£39,168.50) without**. Year-1 P50 cash then compares a 10 kW asset against an 18 kW one, so it
  inverts (£3,765.33 against £5,289.81) — while every return measure still moves the way the feature
  claims: **IRR 28.31% with the levy against 7.66% without**, capital recovery 390.44% against
  164.68%. So the levy still pays; the test's invariant is only sound at a **fixed spec**, and a free
  sizing search makes it a comparison of two different assets. Left for the owning lane to re-state
  (fix the spec, or assert IRR/NPV instead of year-1 cash) rather than patched from here, and the
  release is **not** tuned to make it pass.
- **Flagged, NOT fixed — the private-wire non-commodity residual (a bigger single number than
  anything above).** Tanhouse carries `private_wire_non_commodity_residual` = **13.13958 p/kWh** and
  Colnbrook **4.13135 p/kWh** *inside* `levies`, so the MILP objective prices it on a **marginal
  kWh** of charging. Both records admit the composition is unknown (a residual back-solved as the
  arithmetic difference between two invoice figures). GB regulated **residuals have been fixed
  £/site/day since April 2023** (TCR; already in the register), and a fixed £/day charge has **zero**
  marginal cost per kWh. These are private-wire contracts, so the GB literature cannot settle it —
  but Tanhouse's whole "declines to cycle at any size" verdict rests on this one number (break-even
  4.788 p/kWh against the 3.525 p/kWh its commodity rates offer), and the grid-supplied sites in the
  same portfolio correctly keep their fixed elements out of the per-kWh sum. **The ask is one full
  private-wire invoice per site with every line item.** Direction if part of it turns out to be
  fixed: charging cost DOWN, break-even DOWN, both sites' modelled revenue **UP** — which is why it
  needs the owner's decision and the dispatch-objective work, not a unilateral edit. Full write-up
  in `docs/bess_constants_register.md`.

## 2026-08-17 — the PACK says what the best candidate earned, and a null case stops reading as a verdict

- **The figures were on the record and not in the pack (Olly).** Sizing grid `0.2.0` put each
  candidate's measured floor on the record and the `CANDIDATE_REVENUE` disclosure stated it in
  `quote.warnings`. Measured on the real Bishops Cleeve Ash Plant run, that sentence landed **42% of
  the way down a 110 KB `consulting_report.html`, inside the Complication warnings block**, and
  **nowhere at all in `proposal.md`**. The stream register a reader actually looks at still printed
  `£0.00` on every row with "Not measured" beside it and no cross-reference. So a client still read
  "no value here".
- **The measurement is now a first-class block in all four documents.**
  `outputs/viewmodel.py:865` (`_best_candidate`, wired at `viewmodel.py:1296` in `build_view_model`)
  reads `QuoteRecord.sizing_grid` once and every renderer inherits it:
  `consulting_report.py:1327` (`_best_candidate_exhibit`, called at `:1721`),
  `html_report.py:261` (`_best_candidate`, called at `:107`),
  `underwriting.py:148` (`_best_candidate`, called at `:53`), and
  `proposal.py:259` / `:89`. Each states the candidate's kW, duration, priced-floor revenue, both
  recorded cost lines, the subtraction, the count of confirmed candidates it is the best of, and the
  per-stream floor / held-out split.
- **Labelled so it cannot be read as a specification.** One shared clause,
  `viewmodel.BEST_CANDIDATE_HEADING = "Best evaluated candidate, not a recommendation"`, plus
  `BEST_CANDIDATE_CAVEAT`, in every document. A **lead sentence above the all-zero stream register**
  (`consulting_report.py:1564`, `html_report.py:561`, `underwriting.py:298`, from
  `viewmodel.py:408`) states why every row reads `£0.00` and points at the figures. The customer proposal's
  false claim — *"no value has been calculated for any of these streams"*, on records that measured
  it — is replaced by the measurement stated as the **reason** for the do-not-proceed verdict.
- **The block is scoped to no-spec runs** and to **confirmed (non-proxy)** candidates, with the same
  ranking key and tie-break as `disclosures.candidate_revenue_disclosure`, pinned equal on published
  bytes so two pickers can never name two different sizes on one page.
- **A null `financial_case` is no longer presented as an economic verdict.** Three claims made from
  `selection_status` alone, on records with no case and no computed return for any candidate, are
  gone: "none is economically viable on the priced value floor" (`report.html`), "none produces a
  positive return on the priced value floor" (`memo.md`), and a returns panel captioned "the returns
  below stand alone" over a panel holding no returns (`consulting_report.html`). Each now states the
  recorded outcome ("no system was selected"), attributes the reason to the engine verbatim, and
  where the case is absent says so via `viewmodel.NO_FINANCIAL_CASE_REASON`.
- **Established, on real runs, that the two absences are different facts.** A no-spec run persists
  `financial_case = None` (Bishops Cleeve Ash Plant, gb_v1_10). An **unlevered** selection does not:
  a real `gridq run` of the demo site with `counterparty.credit_status = "unknown"` selected
  **166.25 kW** at `sizing_grid.selection_basis = "unlevered"` and persisted `irr = 3.9146`,
  `equity_irr = null`, `dscr_summary.min_dscr = null`, band LOW. That is also the **first real-run
  demonstration of Outcome 1's unlevered branch firing in production**, which its verifiers recorded
  as outstanding.
- **D45 at the render layer.** An unrecorded candidate cost line now renders as
  `NO_CANDIDATE_COST_REASON` ("an absent figure and not a cost of zero") in all four documents and
  the revenue-vs-cost comparison is withheld. `report.html` previously printed a bare `n/a` there,
  the one cell an absence and a zero could share on a page whose subject is telling them apart.
- **Zero em and en dashes** in every string added, in all four documents (`report.html` and
  `memo.md` are not scrubbed at document level).
- Tests: `tests/test_best_candidate_reaches_the_pack.py` (40), all assertions on **bytes read off
  disk** from `quotes.persistence.publish_run`, with the record built in the test and assembled
  through the production `assemble_quote_record`. **14 mutations run, 14 RED** after one that came
  back GREEN was caught and the test strengthened; every restore md5-identical.

## 2026-08-17 — a no-spec run now says what its best candidate EARNED (sizing grid `0.2.0`)

- **A sizing grid point carried no £ at all (Olly).** It held `power_kw`, `duration_hours`,
  `energy_kwh`, `project_irr_pct`, `levered_return_pct`, `min_p90_dscr`, `passes_covenant`,
  `priceable`, `bankable`, `solver_status`, `stage`, `proxy`, `end_of_life_deliverable` — every ratio
  the objective ranks on, and **no revenue field**. So a run that selected no system persisted 170
  evaluated candidates and could not state what any of them earned, while the top-level record's
  streams all read `0.00` because it reports no candidate. The only reading a pack offered was
  *"this site earns nothing"* — on runs whose own recorded stream note said the dispatch had
  DISCHARGED 30,133.0 kWh/yr, and while `optimisation/valuation.py` was already telling readers "the
  per-candidate figures are in the sizing grid" about figures the grid did not carry.
  `SizingGridPoint` now carries `annual_revenue_gbp` (the **priced floor**, copied verbatim from
  `CashflowModel.total_annual_revenue_gbp`), `annual_operating_cost_gbp`,
  `annual_dispatch_cost_gbp`, and `revenue_streams` — a new `SizingGridStreamRevenue` per stream
  with its £, firmness, gate state and floor membership. Written at
  `sizing/sizer.py:308` (`_grid_point`) and `sizing/sizer.py:255` (`_grid_streams`), reached from
  `cli.py:278` → `generate_quote` → `MilpEngine._run_search` → either search → `sizing_grid()` →
  `quotes/pipeline.py:423`.
- **The pack says it.** `quotes/disclosures.py:285` (`candidate_revenue_disclosure`, wired into
  `record_disclosures` at `disclosures.py:442`) states the best **full-series** candidate's measured floor, its recorded cost
  stack and the subtraction between them, with the per-stream split. Verified on a real
  `gridq run`: the sentence appears in `consulting_report.html`, `report.html` and `memo.md`.
- **D45 — the three zeros stay apart on the record.** An unpriceable candidate carries `None`, never
  `0.0` (no cashflow model was built, so no floor figure exists). A floor stream at `0.0` was priced
  and declined — `in_priced_floor=True`. A held-out stream is `in_priced_floor=False` with its
  `gate_status`, and its £ is excluded from every total, in both directions: a conditional stream may
  carry a real non-zero figure that is upside, not floor. The reader refuses to compare revenue
  against a cost line that was not recorded, rather than reading the absence as zero.
- **One definition of the priced floor.** `schemas/market.py` gains `PRICED_FLOOR_FIRMNESS` and
  `is_priced_floor`; `finance/cashflows.py::_is_priced` delegates to it (behaviour byte-identical).
  Two copies of the firmness set would have been two definitions of the floor, and the grid's
  breakdown could have drifted from the finance total it is supposed to decompose.
- **Versioned locally, deliberately: `SIZING_GRID_SCHEMA_VERSION = "0.2.0"` — and no `run_id`
  moves.** The task brief said `SizingGrid` is in CLAUDE.md §7's frozen list and feeds `input_hash`;
  **both limbs are false at the code**, and unchanged run identities are therefore correct rather
  than a missed step. §7's list is `SiteModel` · `OptimisationResult` · acceptance/conformance tests ·
  assumption conventions, and `quotes/assembly.py` (the `assemble_quote_record` docstring, confirmed
  by `_canonical_run_input`) excludes `sizing_grid` from the canonical hash input by design. The
  global `SCHEMA_VERSION` stays `0.1.0` — `engine/conformance.py:261` pins it to
  `OptimisationResult.schema_version`, so bumping it would re-mint every identity in the repo to
  version an additive output projection. See **D52** for the full reasoning and the five alternatives
  rejected. `SizingRun.fingerprint()` also gains the per-candidate floor.
- **Direction of effect: no priced number moves; what is CLAIMED moves upward.** Every new field is a
  verbatim copy of a value the deterministic record already held — zero new pricing arithmetic. The
  upward move (a no-spec run now states a revenue figure where it stated nothing) is guarded by the
  `None`-not-zero rule, by the floor total being finance's own figure rather than a re-summed one,
  and by the reader filtering proxy points: a proxy reads ~8% high (£66,651.91 against £61,889.20
  measured on the demo site). **Record size grows and it is stated, not hidden:** the demo's
  `quote.json` went ~132 KB → 907 KB (320 points × 8 streams, ~1.4 KB/point); the rendered pack is
  unaffected at 129 KB.

## 2026-08-17 — the sizing search selects UNLEVERED when no debt resolves, and says so

- **A financing-data absence stopped wearing an economic verdict (Olly).** `SizingObjective.feasible`
  returns `False` the instant `min_p90_dscr` is `None`, and the sizing loop is handed no `DebtTerms`
  at all — `debt_terms` appears nowhere in `optimisation/engine.py`, and
  `build_debt_terms_from_credit` is called only at `finance/financial_model.py:154`, after sizing has
  already chosen. So on every default run EVERY candidate was infeasible, `sizer._select` returned no
  selection, and "no viable configuration" was published for a reason `validation_requirements.md` §3
  and CLAUDE.md §2 both forbid in the same words: *"the absence of a credit assessment or debt
  structure means the site underwrites on an **unlevered** basis with the confidence band adjusted,
  not that the site fails"*. `_select` now has a third outcome. When no candidate is covenant-feasible
  **and the covenant gate never ran on any of them** (`_covenant_gate_ran`: no covenant to test, or no
  candidate carrying a measured DSCR), it selects the best candidate by the objective's own
  `rank_key` on the unlevered project IRR and returns `basis="unlevered"`. Threaded at both call
  sites — the only two — `sizing/sizer.py:525` (`enumerate_sizing`) and
  `optimisation/two_stage.py:744` (the path the 2026-08-15 campaign actually ran).
- **Nothing that never met a covenant is stamped `covenant`.** `SizingRun.sizing_grid()` hard-coded
  `selection_basis = "covenant"` for any run with a non-`None` `selected`, so an unlevered pick would
  have persisted onto the `QuoteRecord` — and into the pack — as a covenant-feasible optimum. The
  basis is now recorded on the run by `_select` and **copied**, never inferred, and it rides
  `SizingRun.fingerprint()` so a determinism golden can see it change. `SizingGrid.selection_basis`
  gains `"unlevered"` as a documented fourth value.
- **No placeholder gearing was threaded into selection, and none may be.** `gb_v1_10` carries
  `financing.debt_gearing_pct = 60.0` with `status: placeholder`. Using it to manufacture a DSCR
  would invent the one number the covenant test exists to measure, so the unlevered basis is the
  road: the selection is explicitly *not* bankable, it has **neither passed nor failed** a covenant,
  and the reason string says both.
- **The covenant gate is untouched where debt IS resolved.** The unlevered branch is keyed on the gate
  never having run, not on "no feasible candidate" — so a measured covenant failure stays an
  evidenced finding and can never be relabelled as a data absence. `candidate_viable` still gates the
  pool (the 2026-07-07 red-team fix: an unlevered basis ranks candidates that return capital, it is
  not a licence to recommend the least-bad loss-maker), and an explicit
  `end_of_life_deliverable is False` verdict is rejected here exactly as `feasible()` rejects it.
- **A FALSE FACTUAL CLAIM removed from the verdict prose.** `_covenant_account` published *"no debt
  structure was resolved (no injected DebtTerms, **and the assumption library carries no gearing**)"*
  from a branch keyed **only** on "no candidate carries a DSCR". It had never opened a library, and
  the claim is false for every release from `gb_v1_2` to `gb_v1_10`. `_no_debt_cause` now states what
  the call actually knows — what the sizing search was handed — and says in terms that no claim is
  being made about any assumption release. The hard-won framing stays verbatim: **"THE COVENANT GATE
  DID NOT RUN"** and **"NOTHING HERE IS EVIDENCE THAT ANY SIZE FAILED THE COVENANT"**.
- **§3's "with the confidence band adjusted" made real and binding.** New `_financing_basis_factor`
  in `quotes/confidence.py` caps at **LOW** any record whose reported size carries **no P90 DSCR**,
  and carries the confirmation ask (credit assessment + debt term sheet). It fires on the persisted
  `sizing_grid.selected.min_p90_dscr` being null — the absence itself — not on the basis *label*,
  because keying it on `selection_basis == "unlevered"` left a hole: `MilpEngine._choose`'s indicative
  fallback can also report a size no DSCR was computed for (when the only viable candidates carry an
  explicit non-deliverable verdict), and that record would have carried a HIGH financing factor with
  no debt structure anywhere in it. The label is quoted in the reason, never trusted as the trigger.
  Without this factor the pack would tell a reader the size was chosen with no debt
  structure while the band beside it reported covenant-tested strength. `_indicative_reason`
  (`optimisation/engine.py`) also lost its now-false "no candidate met the P90-DSCR covenant
  (typically no debt structure is resolvable)" opener: the gate clause is read off the chosen
  candidate's own `min_p90_dscr`.
- **Direction of effect.** No £ figure moves; no revenue, capex, opex, DSCR or IRR is recomputed. What
  moves is that a run which previously persisted `selected=None` + `selection_basis="none"` +
  `SelectionStatus.NO_VIABLE_CONFIGURATION` now persists a selected size labelled `unlevered` with a
  LOW-capped band — **upward in what is claimed, downward in the band that qualifies it**. The engine
  already picked the same candidate via `MilpEngine._choose`'s indicative fallback, so the
  recommended kW is unchanged wherever a spec was already produced; the change is which layer chose
  it and what the record calls the choice. One behavioural narrowing: where the only viable candidate
  carries `end_of_life_deliverable is False`, the search now declines it (the fallback did not).
- **Tests:** `tests/test_unlevered_selection_basis.py` (11). Both production searches driven for
  real; assertions land on the **persisted `SizingGrid`** and on the **rendered report bytes**, with
  the grid inside the rendered record produced by the real `enumerate_sizing` loop rather than
  hand-built. Mutation-verified against five separate production lines (the `_select` branch, the
  `sizing_grid()` copy, the two-stage `selection_basis=` thread, the confidence-factor call site, the
  pack's render branch) — each deletion confirmed RED, each file restored md5-identical.
- **This does not by itself change Bishops Cleeve.** With annual revenue at £0 no candidate has a
  positive return, so nothing is selectable on any basis and the run keeps its honest no-selection
  reason. That is the division of labour with the zero-revenue defect, not a gap in this fix.

## 2026-08-16 — the ninth, tenth and eleventh instances, and a test that had never run

- **The e2e module had NEVER run, on any host (Olly).** `tests/test_e2e_examples_real.py` skipped all
  nine tests on one gate naming a prefill file with **no git history at all** — never committed
  anywhere. Nine skips, zero guarded, since the day it was written, and because a skip is not a
  failure nothing ever flagged it. Its stated reason had also been false since 2026-08-11, when
  `examples/real/` was deliberately un-ignored and committed. The facts it was to carry are tracked
  in `docs/redteam/EXPECTED_RESULT.md`, so the request body was rebuilt in the test file, each line
  citing its source, leaving blank every fact the sources do not state. **It ran, and immediately
  caught commit `cc36f04` — mine, from the same day — which reworded a customer-facing verdict
  sentence with zero test coverage precisely because this module was skipping.** New alongside it:
  `test_real_sites_milp_runs.py`, the **only** test anywhere that runs the MILP over a real site's
  bytes (Gate 1 was previously verified on synthetic fixtures only). Its floor assertion is the
  sharp part: `checked == len(SITE_FILES)` **and** `len(SITE_FILES) >= 9`, because
  checked-equals-available alone passes vacuously at `0 == 0`.

- **Ninth instance: a no-spec record claimed no dispatch was solved.** All eight packs said "no
  dispatch solved, value unavailable" — false on every one, in **two** different ways. Tanhouse and
  Colnbrook: candidates solved, every one discharged nothing, so the £0 is a measured decision with a
  margin. The other six: candidates solved **and discharged 6,227 to 155,602 kWh/yr** and simply were
  not selected. It took three attempts, and the reason is the useful part: the note was first written
  where the code **could not observe the fact it asserted** — the no-spec call site receives only
  `(None, site, assumptions, registry)`, so "nothing was solved" and "162 solved, none selected"
  reach it identically. The agent **stopped rather than write a sentence the code could not
  evidence**, which is the opposite of the failure being fixed.

- **Tenth instance: the stream notes never reached the rendered pack.** A reader saw eleven £0.00
  rows with no basis while the record held a full account of each. My framing was wrong about which
  link broke: `StreamRow.warnings` already existed and was populated, and all **three** stream tables
  ignored it. **The fix nearly introduced the defect it was fixing** — the zero-dispatch label is
  stamped on every stream of an unselected run including gated ones, so classifying by warning *prose*
  would have printed "Priced, then declined" against a Capacity Market row the record says was never
  eligible. It is keyed on the structured `gate_status` instead. Deduplicated: 32 warning entries
  collapse to 21 notes and the shared 250-word paragraph appears **once instead of eleven times**.

- **Eleventh instance, caught before shipping by its own author.** Reviewing its own first draft, that
  agent found the memo's basis cell was built-but-unpinned (deleting it left everything green) and its
  dash test was **vacuous** — it searched for a needle it had itself constructed dash-free, so it
  could never fail. Both now fail correctly. First time in this drive the loop closed without a human
  or an adversarial reviewer finding it.

- **The verdict stopped asserting an IRR comparison that never happened.** `engine.py`'s no-viable
  string claimed "no candidate produces a positive project IRR" while **0 of 162** grid points hold
  one. This is the same false claim corrected earlier the same day — I fixed the sizer's reason, the
  changelog and the handoff log, and **left the engine string that was the actual source**, so it came
  straight back inside the new note that quotes it.

- **In-term local flex and DFS take their P90 from the published lower tail.** One scalar was
  haircutting two structurally different contracted streams: 15% is conservative for a Capacity Market
  availability payment and **optimistic** for in-term flex, where D-139 wants P90 at or near zero.
  **Zero here is sourced, not chosen:** GridQ prices local flex as utilisation revenue only, and
  D-1410 publishes the P90 utilisation rate as **0.00 to 0.02** against a P50 of 0.05 (NGED) and 0.22
  (UKPN). Every movement is more conservative, verified fix-versus-revert across penalty caps of 0,
  100, 1000 and 5000%.

- **Capacity conventions documented, and a live D45 violation found.**
  `docs/capacity_conventions.md`. The framing was wrong in three places: kW vs kVA is a **ratified
  convention**, not a confusion; MIC/ASC/MEC is **five resolution idioms**, three of them deliberate;
  and `milp_solver.py:728`'s `or` chain is behaviourally **safe**. The axis missed entirely was the
  **provenance class** of a field — `connection.mic_kw` carries six and only one leaves a
  machine-readable record. **The live find:** `app/app.py:554-555`, on production `POST /api/run`,
  sets `asc_kw = mic_kw` with no record at all. ASC feeds a priced capacity charge.

## 2026-08-15 (latest) — send-blockers RESOLVED: the zero is real, and it is a tariff-structure finding

- **Tanhouse's £0 is CORRECT, and it is arithmetic rather than a gate (Olly).** The MILP solves to
  `optimal` with **literally zero dispatch** — 0.000 kWh charged and discharged across 17,518
  intervals — so every dispatch-derived stream is £0 because the battery never cycles. Per kWh
  charged: energy gain **+0.0234**, levy leakage **−0.0158**, cycle wear **−0.0187**, DUoS avoided
  **0.0000** (private wire), net **−0.0111 £/kWh**. A cycle must clear **4.79 p/kWh** and the site
  offers **3.53 p**. The killer is that round-trip losses are bought at the **full delivered
  19.49 p/kWh** (commodity plus the 13.14 p flat residual), which costs more than the whole commodity
  spread earns. **The leading hypothesis was half right and the wrong half mattered:** the null DUoS
  bands *do* correctly close `DUOS_RED_BAND_AVOIDANCE`, but that is not what zeroes the site.
  `TARIFF_ARBITRAGE` has an open gate, a real spread and no null anywhere, and still earns nothing.

- **The sweep, which is the more useful output.** Only **two of eight** sites dispatch nothing, and
  both are private-wire EfW:

  | site | spread | needed | margin | discharge |
  |---|---|---|---|---|
  | colnbrook | 0.03086 | 0.03549 | **−0.004071** | **0 kWh** |
  | tanhouse | 0.03525 | 0.04788 | **−0.011113** | **0 kWh** |
  | bishops_cleeve_ash_plant | 0.10511 | 0.04281 | +0.054826 | 18,792 kWh |
  | bishops_cleeve_mrf | 0.09927 | 0.04012 | +0.052050 | 39,625 kWh |
  | ewelme | 0.09406 | 0.04044 | +0.047188 | 8,332 kWh |
  | ewelme_invoice_anchored | 0.09523 | 0.04047 | +0.048191 | 155,602 kWh |
  | harwell | 0.09523 | 0.04089 | +0.047819 | 85,973 kWh |
  | knowl_hill | 0.05443 | 0.05243 | **+0.001763** | 6,227 kWh |

  **The margin has no size term** — it compares the best and worst half-hour of the site's own priced
  series against a per-kWh hurdle — so the two refusers decline at *every* size, which is why the
  24-candidate sweep and the 10-candidate cover both returned zero. Six of eight **do** earn revenue;
  they simply do not earn enough. Knowl Hill clears by 0.0018 with **net-negative** arbitrage
  (−£130.87): DUoS carries its cycle.

- **Ewelme's size-invariance is saturation, not a defect.** £708.25/yr identical at 50, 150 and
  400 kW because at 50 kW the battery already serves **100%** of the site's red-band load
  (1,566 of 1,566 half-hours pinned to load). Two mechanisms, not one, and the discriminator in both
  is the DUoS red band: Tanhouse has none so nothing clears the hurdle; Ewelme has one, clears it,
  then runs out of load.

- **The verdict is robust; the £0.00 is knife-edge.** Degradation cost is the live lever: break-even
  is £8.67/MWh and the *previous* release carried £5.0/MWh, which flips the sign. But **even at
  c_deg = 0** — a bound, not a case — the best achievable saving is ~£8,110/yr against £37,476/yr of
  opex, a £29,366 shortfall before a penny of the £1.16m capex. So "not viable" survives every
  favourable assumption; only the exact zero is fragile.

- **Two corrections to the record itself, not the renderer.** `examples/real/sites/tanhouse.json`
  claimed the residual sits in `levies` "which the MILP does not price into the dispatch objective" —
  **false since F-03**, and that leakage is precisely what zeroes the site, so the file asserted the
  opposite of the mechanism producing its own verdict. And `TARIFF_ARBITRAGE`'s £0 note described the
  **computation** rather than the **decision**, which is why this took a full investigation: a reader
  could not distinguish a hard-cycling battery whose spread netted to zero from an optimiser that
  declined to run. It now states the decision and carries the arithmetic, so the verdict is checkable
  in seconds. Same class as the D45 null-versus-measured-zero distinction, one level up.

- **Seven renderer fixes** from the adversarial review (313 tests, 15/15 mutations caught). The agent
  **overruled the review twice, correctly**: the prescribed capacity fix would have shipped a *new*
  false sentence ("the recorded MIC is the site's own peak" — also untrue, since 500 kW is the
  smallest standard rating *above* the 486.35 kW peak), and the claim that `ewelme_metered` never
  mentions the workshop was wrong (it does, four times, just buried). **Filed not fixed:**
  `engine.py:2592` hardcodes the bill basis as "priced on its own tariff stack", false for Harwell,
  whose tariff is taken by analogy from Ewelme — same defect class, one layer deeper.

## 2026-08-15 (later) — CORRECTION, and eight packs SEND-BLOCKED

- **The claim "27 candidates solved and priced, none producing a positive project IRR" is FALSE
  (Olly).** It appears in commit `ff5def4` and in a HANDOFF entry, both mine. An adversarial pack
  review caught it and I verified it directly: the Tanhouse sizing grid holds **162 points and zero
  carry a `project_irr_pct`, a `levered_return_pct` or a `min_p90_dscr`**, and `financial_case` is
  `None` on **all eight** runs with the warning "Financial model skipped, no positive sizing to
  price". **Nothing was ever priced to an IRR**, so no candidate was compared against zero.
  **The mechanism matters more than the error:** `consulting_report.py:321-326` hardcodes "no
  configuration is economically viable on the priced value floor" keyed on `selection_status`
  **alone**, while the same pack prints, in its own Complication section, "any statement in this pack
  that no candidate produced a POSITIVE return is not a comparison this record evidences". The
  disclosure machinery caught the error and printed the correction; the headline was never updated to
  match, and the headline is what got read and repeated.

- **Open question, now top priority: are the eight verdicts DATA ARTEFACTS?** The record's real
  `selection_reason` is "no bankable candidate: no size meets the P90 DSCR covenant (**DSCR is
  unavailable without injected debt terms** and a real dispatch, or every candidate fails the
  covenant)" — and the campaign passed no `debt_terms`. If the covenant cannot be evaluated without
  them and every candidate therefore fails, `no_viable_configuration` is being returned for a **data**
  reason, which CLAUDE.md §2 and `validation_requirements.md:211` explicitly forbid: missing
  credit/debt data means underwriting **unlevered** with the band adjusted, never a refusal. Under
  investigation by decisive experiment (re-run one site with debt terms and compare).

- **Adversarial review verdict: DO NOT SEND**, on three counts beyond the above. `ewelme_metered`
  captions **£33,837** as "the customer's delivered cost today" when that site's own invoice says
  **£185,862** (the pack covers one of the site's two MPANs; the other is a 750 kVA vehicle workshop
  carrying 82% of site energy). `harwell` prints **£186,498** as "delivered cost today" on a tariff
  **borrowed from Ewelme** and never compared to any bill. `knowl_hill_metered` tells a
  grid-import-avoidance story about a site that is **92.1% self-supplied by landfill gas**, mentioning
  the LFG exactly once, buried in a blocking ask. What the review found **clean**: every numeric token
  in all eight packs traces to its record, the bill arithmetic is exact to the penny, no sentence
  implies granted consent, and em/en dashes are zero.

- **Unaffected by all of this:** the structural finding on the four small Grundon sites. It was
  measured directly from red-band saturation against their own meters — a 25 kW battery at Bishops
  Cleeve Ash Plant captures **100%** of available red-band energy, so everything above it is dead
  capital by construction — and never depended on the MILP verdict or on any IRR. Also unaffected:
  the suite at 187/187, the metered data conversion, Gate 3b firing on all eight, and the Knowl Hill
  landfill-gas discovery.

## 2026-08-15

- **First clean full-suite run on this branch, all seven failures closed, and the SEVENTH
  never-called subsystem wired (Olly).** Baseline established on a settled tree: **176/176 test
  files, zero timeouts, seven failures in three files**, cross-validated by an independent
  single-process run reproducing exactly the same seven. All seven are now closed, and **every fix
  was mutation-verified rather than accepted as green** — the rule added to
  `validation_requirements.md` §3 in this same session. **(1) `test_value_stack_aggregator_cut` (4
  failures) — stale test, code correct.** Every expectation was low by exactly 20x because the file
  hard-coded `GROSS = price x 100 MWh`, silently encoding a 100% call rate; the F-19 parity fix
  routes both the proxy and the engine through `local_flex_call_rate`, so payable = delivered x
  call_rate and 100/5 = 20. **This CLOSES the open 20x proxy/engine divergence: the factor is the
  defect being fixed, not one being introduced.** The cut logic was provably intact throughout (all
  four observed values shared one base; ratios exact at 0.80/0.50/0.90). Re-anchored on the resolver
  rather than re-pinned to new literals, plus a parametrized ratio test against a *measured*
  reference and an F-19 guard; monkeypatching the rate to 1.0 fails the guard. **(2)
  `test_register_moves_no_priced_number` (2) — the guard was RIGHT to fire, and is narrowed, not
  deleted.** `value_stack/gates.py` reads the register to resolve a D45-**declared** capacity, which
  is D45's mechanism, and the repo already conceded the register is input-bearing in the allowlist's
  own comment on `quotes/assembly.py`. Narrowed rule: **a `pending_permission` may never move a £; a
  `derived_input` may, deliberately, since it enters `input_hash` and caps the band.** A new reader
  still fails until someone writes down why. Backed by a two-layer behavioural guard (the schema
  refuses a valued `pending_permission`, AND the gate's `kind` filter refuses one smuggled past
  validation via `model_construct`, because schema constraints drift and `gates.py` is the last
  line); deleting the filter yields `CapacityEvidence(mw=5.0, basis='declared')` — a permission
  manufacturing 5 MW. **(3) `test_priced_floor_arithmetic` F-03 (1) — fixture had gone inert, and
  its own inert-fixture guard caught it, not its assertions.** The 1.5p literal assumed `c_deg = 0`;
  DEG-01 put a marginal cycle cost in the objective and `gb_v0_1` carries no degradation key, so the
  solver falls back to the C-34 default of £21.3/MWh, ~15x the fixture's 0.14p margin. Spread now
  derived from the same release the solver reads via
  `m(levy) = day - night/RTE - c_deg - levy x (1/RTE - 1)`, placed at the midpoint of
  `0 < m(0) < levy x (1/RTE - 1)`; measured RTE 0.88, c_deg 21.30/MWh, window 2.727/MWh, derived
  spread 3.630p (was 1.500p), margins landing at +1.3636 and -1.3636/MWh — exactly symmetric, so
  the solver confirms the algebra independently.

- **MPC dispatch path QUARANTINED — it fails closed-but-silent (Olly).** `MilpEngine(use_mpc=True)`
  now raises unconditionally, and deliberately not gated on the assumption release: there is no
  release on which the path is trustworthy. It does not fail loudly — **every rolling window refuses
  while the assembled result still reports `status="optimal"` with 0.0 volumes**, so a caller cannot
  distinguish "no dispatch was possible" from "dispatch was worth nothing", which under D45 is a
  confidently wrong number and worse than a blocked run. Four faults: a `current_soe = 0.0` seed
  against a minimum-SoE floor; the infeasible branch carrying unchanged state forward as an
  **absorbing fixed point** (measured 14/14 windows refused); `mpc_solver.py:317` hard-coding
  `status="optimal"`; and `milp_solver.solve_window` (~:770) swallowing the refusal text. The last
  two are why it must be unreachable rather than discouraged: a path that cannot report its own
  failure must not be callable from a run that prices anything. **NOT AFFECTED: the default annual
  path**, verified three ways (the refusal is guarded by `initial_soe_kwh is not None` and the annual
  path passes `None`; measured optimal and non-zero at 1/2/4/8h on gb_v1_8; an existing test asserts
  it) — **all six published site packs used the annual path and none of their numbers is
  implicated**. Scoped to the ENGINE, not `MpcDispatchSolver`, since quarantining the class would
  break the three test modules needed to repair it. The now-unreachable `study_basis + use_mpc` check
  is kept but marked unreachable, and its assertion was **removed rather than left to pass by
  catching the quarantine instead** — it would have proven nothing about the study basis.

- **Gate 3b wired: the bill reconciliation was the SEVENTH built-tested-never-called subsystem
  (Olly).** `reconcile_bill_against_invoice`, `bill_reconciliation_records`,
  `invoice_evidence_from_mapping` and four models had **zero references anywhere in `src/` outside
  their own file** — built, tested to 42 tests, inert, and found *mechanically* by the
  reachability-guard prototype, which is a fair validation of that guard since it is a subsystem
  commissioned earlier in the same session and never wired. Gate 3 proves the modelled LOAD matches
  the measured series; it says nothing about whether the tariff stack turns that load into the right
  number of pounds, and **the delivered cost is the base every avoided-cost revenue line is a
  fraction of**. Call site: `quotes/pipeline._gate_3b`, from `generate_quote` AFTER the solve (it
  needs `CustomerBill`), comparing `annual_bill_before_gbp` — the BEFORE side, dispatch-invariant, so
  it tests the tariff stack and not the battery. Per-site only: the module's own warning is
  load-bearing, since the Grundon portfolio reconciles to +0.7% while no individual site is within
  ±7%. **Absence is a first-class answer, never a skip** — a missing file, unreadable JSON or unknown
  site all produce an explicit no-invoice result with the reason recorded, because returning `None`
  would make a site nobody checked indistinguishable from one that reconciled; the `_basis` metadata
  key is refused as a site. **Real behaviour change:** an un-run Gate 3b caps confidence at MEDIUM,
  mirroring Gate 3's `reconciles=None`, so three tests that reached HIGH now require a reconciling
  bill alongside `reconciles=True`; a new test pins this rather than letting it be absorbed, and the
  HIGH fixture is built by RUNNING the real gate so no test can assert a pass the gate would not
  give. **Trap avoided:** the register-disclosure loop runs BEFORE Gate 3b, so its records would have
  reached the band and the quote record while silently missing the report's
  assumptions-requiring-confirmation section — instance-4 shaped, a call path down which only part of
  the value flows. Verified to the new rule: deleting the call site fails two of four new tests.
  **Known gap, disclosed not fixed:** the invoice evidence content does not enter `input_hash`
  (existing F-06/F-09).

- **Process rule: nothing is FIXED until it is reachable (Olly).** Added to
  `docs/validation_requirements.md` §3 and `CLAUDE.md` §4, with the table of all seven instances,
  because the pattern is the point rather than any one of them. **No finding may be marked FIXED
  without naming (a) the production call site, `file:line`, reachable from `cli.py` / `app/app.py` /
  `generate_quote` without test-only code, AND (b) the test that fails if that call site is
  deleted.** Clause (b) is the load-bearing one: **all seven instances had a passing test**; what
  none had was a test that would notice the wiring disappearing, because they called the new function
  directly. Instance 4 is recorded as the warning against over-trusting clause (a) — it *had* a call
  path and was still inert, since a call path proves reachability and never that a value flows.

## 2026-07-20

- **Revenue-Data Drive slice 4 — perimeter data fetchers + the `gb_v1_6` fetcher-backed release (Olly; D43).** Built three off-the-pricing-path perimeter fetchers, ran them LIVE, and landed a new assumption release transcribing the real fetched numbers — no value invented, every changed value cites a hashed source. **4a — fetchers (`gridq.dev`):** `_fetch_common.py` (a shared `sha256_hex`, a `ProvenanceSidecar` dataclass `{source_url, dataset_id, fetched_utc, licence, sha256, size_bytes, fetcher, fetcher_args, record_count}` + deterministic JSON writer, and a single `http_get_bytes` network seam); `fetch_neso_ckan.py` (keyless NESO Data Portal CKAN — `--resource cm-derating|cm-clearing|eac-results`, pure transforms picking the latest T-4 de-rating, deriving £/kW/yr clearing = ForecastCost/(CapacityAwarded×1000), and the register §10 DC/DM/DR mean×8760); `fetch_piclo.py` (public Piclo Data Hub S3 — `--dataset competitions|bids|lcm`, competitions parsed via pandas for the GB-wide utilisation median, bids/lcm hash+row-count only). Each writes a gitignored artifact under `data/fetched/` + a committed provenance sidecar and PRINTS a suggested YAML block (never edits `assumptions/`). Mirrors the 018 `fetch_prices` pattern: a single monkeypatched seam, pure deterministic transforms, "OFF the pricing path / never fabricates" docstrings, CLI entry; tests do ZERO live HTTP (captured fixtures under `tests/fixtures/`). **4b — the `gb_v1_6` release (fetched LIVE 2026-07-20):** copy of `gb_v1_5`, priors carried forward UNCHANGED, four leaves re-sourced from live fetches (each `needs_review`, source citing URL + sha256(12) + fetched-date + the committed sidecar path under `assumptions/provenance/gb_v1_6/`): `capacity_market.clearing_price_gbp_per_kw_yr` 30.0→**27.1** (latest T-4 = DY 2029/30; T-1 = £5.0/kW/yr in notes), `capacity_market.de_rating_factor_by_duration_h` →**{1:0.1099, 2:0.2198, 4:0.4396}** (primary NESO CKAN de-rating REPLACES the gb_v1_5 modoenergy secondary citation), `ancillary_services.dc_dm_dr_value_gbp_per_mw_yr` 0→**28296.9** (mean 3.2302 £/MW/h across DC/DM/DR × 8760; NONZERO but still GATED/indicative, never FIRM), `local_flex_piclo.utilisation_price_gbp_per_mwh` 150.0→**135.67** (GB-wide median of 224,535 Piclo utilisation guide prices). `wholesale_p415` + `balancing_mechanism` values STAY exactly 0 (source/notes rewritten to state the real £ path — MID overlay + consented export tariff for wholesale; paid Modo index / full BOALF×BOD for BM). **ALL entries stay `needs_review`/`placeholder` — 0 confirmed repo-wide; the not-bankable posture is untouched.** **Golden UNCHANGED:** the app stays pinned to `gb_v1_4` (`app.py` untouched), so `GOLDEN_QUOTE_HASH` + every demo pin are unchanged and this release is invisible to the golden. New tests: `tests/test_dev_fetch_neso_ckan.py`, `tests/test_dev_fetch_piclo.py` (transforms, provenance byte-determinism under a fixed `fetched_utc`, failure⇒no artifact, no live HTTP), `tests/test_assumptions_gb_v1_6.py` (loads/validates, entry+status counts locked at 40/10-needs_review/30-placeholder/0-confirmed, every changed entry's provenance, wholesale+BM exactly 0, priors == gb_v1_5, a behavioural check that the nonzero ancillary value stays gated/indicative and can never be FIRM). ruff/format/mypy/review_gates/pytest green (except the known C034 macOS golden skip).

- **Revenue-Data Drive slice 3 — the customer declares per-stream consent + agreed rates at intake (Olly; D43).** The intake end of the gate→price→floor→proposal chain wired in 2a/2b/2c: the customer can now authorise each revenue stream (and enter any rate already agreed) in the app, mapped onto `SiteModel.stream_consents`. **UI:** a shared partial `templates/_stream_consents.html` (included by BOTH the guided wizard's consents pane and the real-prospect form) lists all ten revenue streams, each with a consent checkbox (`consent_<stream>`); the three streams whose agreed rate has no dedicated `ExportArrangement` home — Capacity Market (£/kW/yr), Balancing Mechanism and Ancillary (£/MW/yr) — also get an optional agreed-rate input (`consent_rate_<stream>`), while the export-tariff / aggregator-cut / local-flex rates keep their existing fields and are not duplicated. Helper copy states plainly that consent authorises us to pursue the revenue, it is not a guarantee. Design-system clean (tokens only, no raw colours, no em dashes; guard green). **Wizard parity:** the wizard gains the four real-form fields it lacked — supplier_consent, asc_kw, export_counterparty and the local-flex agreed rate — so it can now produce the same `SiteModel` as the real form (those already mapped in `_site_from_form`; only the inputs were missing). **Mapping (`app.py:_site_from_form`):** builds `stream_consents: tuple[RevenueStreamConsent, ...] | None` from the ticked boxes (+ agreed_rate_gbp from the per-stream rate inputs where present); the schema validator enforces the canonical/duplicate rules; nothing ticked ⇒ `None`, which `quotes.assembly._canonical_run_input` POPS so a no-consent submission keeps a byte-stable input hash (mirror of the blank-export-arrangement guarantee). **Determinism / no regression:** this slice touches only the wizard/real-form path + `_site_from_form`, never the JSON demo `load_demo_site` (the wizard demo=1 path loads the JSON, so form changes cannot reach it) — the demo golden `GOLDEN_QUOTE_HASH` and the `test_app_demo` pins are UNCHANGED (verified: 6 passed, 1 skipped = the known C034 macOS golden skip, no re-mint). New/extended tests in `tests/test_app_wizard_consents.py`: both forms render the per-stream controls + helper text, the wizard carries the parity fields, `_site_from_form` maps a full consent submission (streams, consented, agreed_rate_gbp) + populates ConsentState/ConnectionDetails/ExportArrangement, a no-consent submission yields `stream_consents=None` + a hash-stable canonical dump, and a fully-consented intake flows into pricing end-to-end (a consented CM stream prices at the customer-agreed £50/kW/yr = £10,000 and carries consent_status="given"). ruff/format/mypy/review_gates green.

## 2026-07-19

- **Revenue-Data Drive slice 2c — the customer-facing outputs say "Consent given" per consented stream (Olly; D43).** The last leg of the consent spine reaches the artifacts, record-driven end to end: the wording originates from the record's `ValueStreamResult.consent_status` (carried verbatim onto a new `StreamRow.consent_status` in `outputs/viewmodel.py`) — never a site lookup or a number authored in a template. `_split` now cites a gated stream in **every** mode (including the fast/banded indicative path) iff `consent_status == "given"`, so a consented-but-still-gated stream is never hidden; `_GATED_CITING_MODES` continues to govern only UNCONSENTED gated rows. An additive categorical `ContractedMerchantSplit.has_consented` (a bool, not a number) lets the number-free prose add a consent clause. **Renderers** (`proposal.py`, `underwriting.py`, `html_report.py`, `prose.py`): the stream tables' unlock-condition cell reads "Consent given" (or "Consent given · <residual unlock>") for a consented row while reserving the conditional/unlock wording for rows without consent; the proposal's "What's secured vs upside" annotates each consented stream label "(consent given)"; the memo/HTML consents section lists the customer-authorised streams; `firmness_explainer`/`next_steps` gain an optional `has_consented_streams` flag (default off) that appends a truthful clause — **"consent given" means the customer authorised it, not that revenue is guaranteed**: a consented CONTRACTED stream is still shown under contracted (not firm) with its haircut context. **Determinism / no regression:** the additions are gated on a consent basis existing, so an UNCONSENTED quote's three artifacts are byte-identical to HEAD; the demo declares no `stream_consents`, so `GOLDEN_QUOTE_HASH`, the double-render byte-identity + numbers-match guards and `test_app_demo` are UNCHANGED. `test_outputs_indicative_gated.py` relaxed (a consented gated stream may be cited in indicative mode) with an explicit regression that an unconsented gated stream is still omitted; new `tests/test_outputs_consent_given.py` pins the wording across all three artifacts + the unconsented control. ruff/format/mypy/review_gates green.

- **Revenue-Data Drive slice 2b — consent + agreed rate now DRIVE PRICING + PLACEMENT (Olly; D43).** Consent stops being label-only: a stream the customer consented to, with the rate it needs, is priced into the floor as **CONTRACTED** (never FIRM) so finance underwrites it with `p90_revenue_haircut_contracted_pct` + the contracted-basis DSCR. **Estimation ladder (locked):** real contracted > agreed rate (`site.agreed_rate_for` / the `ExportArrangement` rate home) > sourced/assumption > £0; **volumes are always engine-measured, never invented**; a consented stream WITHOUT its needed rate stays £0 with its "consent given" label. Engine path (`optimisation/valuation.py`): CM prefers the consented agreed £/kW/yr over the assumption clearing price (deliverability physics untouched — the gate still holds CM out of the floor until confirmed); **local flex now nets the aggregator cut** (per-site `ExportArrangement.aggregator_cut_pct` → assumption default → 0.0), fixing a long-standing engine-path cut-ignored bug (parity with the proxy); **BM + ancillary** price `agreed £/MW/yr × de-rated deliverable MW` (parity with CM's volume basis) only when consented WITH a rate, else £0 CONDITIONAL. **Placement:** a consented + priced (CONTRACTED, £>0) gated stream moves from `gated_upside` into `value_streams`; unconsented / consented-but-unpriced streams stay `gated_upside`. **Wholesale contracted mode:** `wholesale_contracted_stream_result` prices `agreed export tariff (£/kWh) × overlay export MWh` as a CONTRACTED floor entry (injected in `MilpEngine._final_result` / `DemoEngine._final_demo_result` where the overlay export volume is known); the Elexon-MID perfect-foresight delta stays a SEPARATE indicative CONDITIONAL annotation in `gated_upside`, never contracted, never in P50/P90/DSCR. Proxy parity (`value_stack/gb_streams.py`): CM/BM/ancillary `_price` prefer the consented agreed rate ahead of the assumption (labelled not-bankable — a commercially-agreed rate, not a market-cleared price); wholesale notes that its contracted export-tariff value is engine-path only (the proxy carries no export volume). **Frozen-contract change (deliberate, flagged for schema-reviewer):** two teeth that assumed gated streams live ONLY in `gated_upside` are now **consent-relaxed** — `DispatchResult._checks` "leaked" and conformance `firmness.gated_excluded_from_priced_floor` permit a gated stream in the priced floor ONLY when it carries a `consent_status` basis (an UNCONSENTED gated stream in the floor is still rejected; `gated_never_firm` still bites everywhere). **Determinism:** the demo declares NO `stream_consents` and NO export tariff, so the demo golden `GOLDEN_QUOTE_HASH` and the `test_app_demo` pins are UNCHANGED (verified — no demo stream now prices); an unconsented site is behaviourally identical to 2a; a fully-consented site prices its streams and runs twice byte-identical. New tests: `tests/test_valuation_consent_pricing.py` (per-stream engine pricing + placement + cut-netting), proxy-parity cases in `tests/test_value_stack_value_functions.py`, a consented-CONTRACTED finance case in `tests/test_finance_firmness_p90.py`, both-placements-valid + unconsented-still-rejected in `tests/test_optimisation_contract_conformance.py`, and the contracted export-tariff mode in `tests/test_optimisation_export.py`. ruff/format/mypy/review_gates green.

## 2026-07-18

- **Revenue-Data Drive slice 2a — consent-aware gating, no £ change (Olly; D44).** Per-stream customer consent (`SiteModel.stream_consents`, the D43 spine) now flows into the gate layer as **authorisation that admits CONTRACTED, never FIRM**. All four never-FIRM layers stay intact; the only relaxation is `GatedUpsideStream`'s never-OPEN rule → **never-OPEN-without-an-explicit-consent-basis** (`open_firmness` CONDITIONAL→CONTRACTED; `_gate` refuses an OPEN gate unless a `consent_status` basis is supplied — OPEN without consent still raises). New pure predicates in `value_stack/gates.py`: `stream_consented(site, stream)` (reads `site.stream_consents` directly, like `has_export_consent`) and `agreed_rate(site, stream)` (generic `stream_consents` carrier → the stream's dedicated `ExportArrangement` rate home → `None`, never guessed). Consent-aware `check_gates` across all ten GB streams: a consented gated stream reaches OPEN⇒CONTRACTED (wholesale/P415 also needs `export_dispatch_enabled`; BM/ancillary need only consent), while **CM keeps its deliverability physics — consent can never bypass `deliverability_confirmed`**; every stream stamps the orthogonal `consent_status="given"` label when consented, regardless of whether its gate opened. Two additive, optional fields carry the label through the seam: `GateResult.consent_status` and `ValueStreamResult.consent_status` (`str | None`, default `None`); `valuation._result` and the value-stack proxy copy the gate's label onto the result. **No £ change and no placement change this slice:** consent is authorisation, not value — a consented gated stream still prices £0 today and stays in `gated_upside` (pricing overrides + the CONTRACTED→`value_streams` move are slice 2b; the proposal "consent given" copy is 2c). **Determinism:** an UNCONSENTED site's quote is behaviourally identical to HEAD except the new `consent_status: null` field — verified by stripping the field from the demo quote dump and recovering the pre-change normalised hash byte-for-byte. The demo golden `GOLDEN_QUOTE_HASH` is re-pinned for the additive `null` field only (Linux-minted per C034; the exact-hash check skips on macOS — the Linux value must be re-minted on the CI/deploy box). New/migrated tests in `tests/test_value_stack_gates.py` + `tests/test_value_stack_interface.py`; `test_schemas.py`'s gated-cannot-be-FIRM stays green. ruff/format/mypy/review_gates green.

## 2026-07-17

- **Issue 018 — consent-gated wholesale EXPORT in the dispatch, valued as gated upside (Olly; A2/A3 + B2, D43/C036).** The export capability from the blueprint `docs/issues/018-A2A3-export-design.md`, architecture (b): a SEPARATE gated-upside overlay solve, never the priced floor. **(B2)** a pure alignment helper `gridq.optimisation.export_prices.align_export_prices` maps a loaded `MarketForecast` onto the metered load's half-hourly timestamps → a per-instant £/kWh array (÷1000 from £/MWh), or `None` + reason if ANY metered stamp is uncovered (full-coverage-or-skip; NEVER interpolated; tz-awareness mismatch refused, not guessed). **(A2)** `MilpDispatchSolver._solve_milp` gains keyword `export_price_gbp_per_kwh` — behind `is not None` ONLY (no aligned series ⇒ NOT ONE new variable/constraint ⇒ the floor model matrix is byte-identical, structural not tolerance): an `export` variable bounded `0..min(power, MEC)` (the STRUCTURAL export_limit), draining SoE via `+(discharge+export)·dt`, sharing the inverter `discharge+export ≤ power−prov_unmet`, counted in the warranty throughput and the `availability_reserve_aggregate` (envelope UNCHANGED at `min(power−aux, load)` — conservative), paid `+Σ export_price·export·dt` with `_EPS` extended to `(charge+discharge+export)` (`_EPS_PEAK`/`_EPS_AGE` untouched); the buy-retail/sell-wholesale wash is blocked by SURGICAL `is_charging` binaries only where `wholesale·rte > price+tol` (elsewhere `_EPS` kills a loss-making wash; IPM/crossover dropped when a binary is present). New `solve_with_export(problem, market_forecast)` reuses the `solve()` preamble, aligns via B2, and does NOT touch `_last_schedule`. `"export_limit"` moved DEFERRED→STRUCTURAL (behaviour-neutral). **(A3)** `MilpEngine._export_upside` mirrors `_provisioning_opportunity_cost` (fresh solvers, guard on `export_dispatch_enabled(site)` = G99∧G100 approved ∧ MEC>0 ∧ export_mpan present, AND a supplied price series, AND a MilpDispatchSolver): `delta = max((overlay floor-terms + export revenue) − floor floor-terms, 0)` — ≥0 by construction (export=0 recovers the floor optimum), auto-nets retail energy bought + floor arbitrage cannibalised, £0 when export never pays; reported under `WHOLESALE_P415` in `gated_upside`, firmness CONDITIONAL (schema enforces never-FIRM), NEVER in the priced floor / P50/P90 / DSCR. Threaded additively: `MilpEngine(market_forecast=…)` (engine config, like the progress sink; default `None` = byte-identical) + `gridq run --forecast` wires the raw forecast through. **Determinism proof:** export OFF ⇒ the demo quote is byte-identical (normalised hash unchanged; DemoEngine carries no export logic and the demo has no price series — spec 165.25 kW × 3.15 h, IRR/equity/payback unchanged), and the RTF E2E is unaffected (G99 unknown gates export). Hand-verified positive case on the shared 7-day fixture at 500 £/MWh: 146 MWh/yr exported → £73,000 gross, floor arbitrage £37,637.66 cannibalised → **honest delta £18,771.43** (gross would double-count). No frozen-contract schema change — `gated_upside` is already the slot. 23 new tests (`tests/test_optimisation_export.py`, `tests/test_optimisation_export_prices.py`); governance filed as C036/G1 + decision D43 (owner-authorized, Olly review = amendments). Still to come (018): the Robert Thomas end-to-end run with a real Elexon price series (D7) + the README.

- **Issue 018 meeting-readiness drive — foundational slices + a frontend-declared consents feature (Olly).** Toward a full Robert Thomas run + platform demo. Landed and green: **(A1)** dispatch diagnosis recorded as F-013 — the MILP is correct and DOES discharge; on an identical tariff the demo earns ~£14.4k/yr at 100 kW·2h while Robert Thomas earns only ~£4.2k, because RT concentrates 68% of load in the cheapest overnight band and just 6.4% in the red band, so its `no_viable_configuration` is genuine load-shape economics, not a bug (its viability lives in the export/flex/wholesale streams, not the behind-the-meter floor). **(D1/D4)** HH ingestion now accepts a bare `kwh`/`energy_kwh` column and Excel (`.xlsx`/`.xls`, single-sheet; a multi-sheet workbook is rejected, never guessed; `openpyxl` pinned). **(A4)** additive `ExportArrangement` on `SiteModel` (route, counterparty, `aggregator_cut_pct`, export tariff, local-flex agreed rate) + `ExportRoute` enum, hash-stable when absent. **(B1)** `gridq.dev.fetch_prices` — historical GB half-hourly wholesale prices from Elexon's Insights **MID** dataset (free, **no API key**), off the pricing path, deterministic, writing a versioned CSV the existing forecast loader already reads. **(C1/C2)** new release `gb_v1_5` adds `local_flex_piclo.aggregator_cut_pct` (0.20 placeholder); local flex now prices **net of the aggregator cut** (per-site `ExportArrangement` override → assumption default → 0.0 no-op; releases without the key, incl. gb_v0_1, are unchanged). **(C034)** the Linux-anchored demo golden-hash assertion now skips on non-Linux (C034's own skip-marker option) while the two determinism tests still guard reproducibility everywhere — verified these changes move NO priced value (demo still 165.25 kW × 3.15 h, IRR 9.6768, equity 10.3484, payback 7.0937). **(D8 — owner steer) consents are a frontend-declared input, never a backend block:** the sole ERROR-severity export-coherence check is now a WARNING (a MEC>0 + G99/G100 `not_required` site loads and runs everywhere, surfaced as a ranked data-ask, not rejected — genuine structural guards like missing HH/MIC are untouched); a new "Consents & grid arrangements" step in the guided wizard (now 6 steps) AND the real-prospect form capture G98/G99/G100, DNO application state, import/export MPAN, MEC, export route and the agreed rates (export tariff, aggregator cut %, local-flex rate), all optional and mapped through `_site_from_form` onto ConsentState/ConnectionDetails/SupplyBoundary/ExportArrangement (blank ⇒ `export_arrangement` None, hash-stable); the demo fixture declares full consent with MEC=MIC (export-eligible, priced £ unchanged). New tests across ingestion, value-stack, app-wizard and site-JSON; ruff/format/mypy/review_gates green. **Still to come (tracked in `docs/issues/018-RESUME.md`):** the export overlay solve itself (A2/A3 — designed, blueprint `018-A2A3-export-design.md`), the half-hourly price-series input path that feeds it (B2/B3), the Robert Thomas end-to-end run with export (D2/D7), and the new README. Commit notes: `src/gridq/schemas/export_arrangement.py` is untracked (needs `git add`); the demo forced-consent change means the **Linux golden pin must be re-minted on CI/deploy**.

- **The run/loading screen shows REAL, forward-only progress (Olly; additive hooks into the optimisation lane, C035).** The web app's run overlay bar was a client-side fake: a `setInterval` ramp computed its width as `Math.min(88, 12 + visual*22 + Math.random()*6)`, so the `Math.random()` recomputed each tick made it jitter BACKWARD, and there was no percentage and no phase/iteration detail — the long dispatch+sizing phase looked crashed. Replaced with genuine backend progress carried over the existing `_RUN_JOBS` + `/api/run/{job_id}` transport. A single primitive side-channel sink — `progress(phase, done, total)`, default `None` at every hop — threads additively through the stack: keyword-only `progress` on `enumerate_sizing` (emits `("sizing", i, N)`) and `two_stage_sizing` (one emit per ACTUAL solve at the four real sites — coarse/refine/confirm/polish, floor-skips and memo-hits excluded); a duck-typed `set_progress_sink` on `MilpEngine`/`DemoEngine` (engine config, not per-solve state) that forwards into the search and emits one `"dispatch"` before the post-sizing re-dispatch; a `progress` kwarg on `generate_quote` that pushes the sink into the engine and emits `optimise`/`finance`/`finalise`; and `api_run_start`'s `_work` writing the facts into the job dict via `_set_job`. **Determinism-safe by construction:** every emit is wrapped so a raising/None sink is swallowed and can never perturb the solve; `None` at every hop is byte-identical to before (goldens/pins/conformance unchanged). The client (`static/run_progress.js`) now owns the percent (presentation only): an ordered phase→band map, strictly monotonic `pct = max(prev, target)` with a numeric `NN%` label, a live `#gq-run-detail` narration naming the phase and iteration ("Confirming shortlisted systems on the full year - 3 of 10"), the track promoted to `role="progressbar"` with `aria-valuenow`, and a gentle sub-band creep so a long single solve never looks frozen — the `Math.random` ramp is gone. Public API (`window.GridQRun.run`), the error path, the 900-poll timeout, and the real-completion invariant (100% + navigate ONLY on `status==="done"` with a quote id) are all preserved. Seam filed for Olly as **C035**. New tests: sizer/two-stage recording-sink coverage (phases, counts, totals, byte-neutrality), engine sink→dispatch, pipeline three-phase + byte-neutral, `tests/test_app_run_progress.py` (status endpoint reflects live stage + terminal states), and a design-system guard (`run_progress.js` has no `Math.random`, has `progressbar`).

## 2026-07-15

- **Native advisor tool enabled: Opus 4.8 executor + Fable 5 advisor, team-wide (Olly).** Complements the model policy adopted earlier today (CLAUDE.md §2a — Fable advises, Opus executes). Set `"advisorModel": "fable"` in the committed `.claude/settings.json` so every contributor's interactive session (running on Opus 4.8 via `/model`) consults a Fable 5 advisor at decision points, per Anthropic's [advisor tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool) (beta `advisor-tool-2026-03-01`; experimental, Anthropic-API only, needs Claude Code ≥ 2.1.170). This is distinct from — and complementary to — the pinned-subagent split (which delegates a whole subtask to one model); the advisor consults mid-session. Overridable per-session with `/advisor <model>` / `--advisor`. Note: a Fable advisor returns encrypted/server-side advice (not shown in the transcript); use an Opus advisor to read the advice text. CLAUDE.md §2a documents it; `GRIDQ_CONTRIBUTOR` stays machine-local in the (gitignored) `settings.local.json`.

## 2026-07-11

- **Power provisioning goes live end-to-end — issue 017 slices B–E (Karim, D42).** The battery as load-serving capacity is now real through the whole engine: **(B)** the declared shortfall compiles from `SiteModel` into the dispatch LP as a CONSTANT forced draw — SoE balance, inverter headroom, warranty throughput and the summed reservation aggregate all see it, the objective cannot (no import-displacement value for load the wire could never deliver); declared additions shrink charge headroom in-window so recharge happens only through real gaps; pre-window SoE floors hold the worst observed occurrence; absent requirements are byte-identical (goldens preserved). **(C)** `search_floors()` puts the obligation under the sizing search as a hard feasibility floor (combined shortfall, end-of-tenor fade); recharge infeasibility or an empty floored domain is the D39 `NO_VIABLE_CONFIGURATION` verdict with the connection-reinforcement reason. **(D)** `MarketStream.LOAD_SERVICE` (registry 9→10): a `ContractedStream` valued ONLY from the customer's declared counterfactual with basis; CONDITIONAL (out of the floor) until the contract `reference` exists (W7-4); measured forced delivery rides as evidence. **(E)** the selected spec's obligation cost is engine-measured (one extra solve without the obligations; priced-floor delta excl. the stream itself) and persists additively as `QuoteRecord.provisioning_opportunity_cost_gbp`; validated end-to-end on a hand-derived **synthetic HGV-depot scenario** (500 kVA connection + a managed 350 kW evening charger bay via the D42 manual daily curve: 170 kW / 310 kWh worst window → a realistic depot-scale battery is selected, the obligation is served, the £38k declared counterfactual prices CONTRACTED in the memo's floor table, the bill stays metered-basis, everything deterministic). 20 new tests across solver/engine/e2e.

## 2026-07-10

- **Provisioning stages 1–2 land: the requirement schema + the pure derivation layer (Karim).** Issue-017's first two slices, both additive (SiteModel untouched — the wiring stays C033-gated). Stage 1: `gridq.schemas.provisioning.ProvisioningRequirement` — windows reuse the W7-1 `AvailabilityWindow` calendar verbatim; validators enforce that a requirement must demand something (declared addition and/or metered shortfall) and that the customer's declared counterfactual cannot exist without its verbatim basis (a £ figure with no basis is an invented number). Stage 2: `gridq.sizing.provisioning` — derived unmet series (`max(0, load+aux+add − cap)` in windows, never declared wholesale), per-occurrence stats, `max_observed` exceedance levels, end-of-tenor floors via the W7-3 closed form **in the solver's own SoE terms** (losses booked on the charge side — the issue doc's §4 formula corrected accordingly), the prefix-balance recharge scan (a necessary condition whose failure names connection reinforcement as the honest recommendation), and the `assess_provisioning` orchestrator with data-ask warnings for every gap (no cap → unassessable; no fade → day-one floors only; no RTE → recharge unassessed). `window_mask` is parity-pinned by test against the solver's `_commitment_mask` across naive/UTC/Europe-London indices (the F-003/C031-#3 tz bug class); stage 3 should hoist the helper to one shared home. 29 new tests. Verification finding along the way (**C034** filed): the pinned demo golden is architecture-scoped — it passes on x86 Linux (confirmed in the live server venv) but fails on ARM macOS even in a fresh py3.12 venv built exactly from `requirements.txt` (HiGHS tie-breaking at degenerate optima differs per float path). Determinism per platform is intact; the cross-platform qualifier was undocumented. This slice was verified on the locked 3.12 env with that one test deselected; pin policy is Olly's call (per-platform pins / platform marker + self-consistency test / value-level asserts).

## 2026-07-09

- **Power provisioning designed — issue 017 + seam filing C033 (Karim).** The battery as load-serving capacity: a site whose desired demand exceeds the connection's deliverable import in a window (e.g. 18:00–19:00) gets a battery that guarantees to serve the unmet load, recharging only through the same connection in the gaps — and the sizing search treats that obligation as a hard feasibility floor. Design doc [docs/issues/017-power-provisioning.md](issues/017-power-provisioning.md): the load-exogeneity inversion; the five hard problems (declared-not-metered unmet load, worst-case exceedance vs perfect-foresight trace, recharge feasibility through the constrained pipe, golden-hour double-counting vs CM/flex, valuation without invention — engine-measured opportunity cost + customer-declared counterfactual); a 6-stage plan (schema → pure derivation layer → `load_service` solver kind → sizing floors + D39 no-viable reason → evidence-gated `LOAD_SERVICE` stream → outputs); and the operating-principles map. Nothing built — the `SiteModel`/enum/solver seams are filed as **C033** for Olly, to be decided as one package with C028/C029. Enabler acknowledged: Olly's C031-#2 fix (aggregated reservations) makes the customer-side/grid-side double-sell impossible.

## 2026-07-08

- **gb_v1_4: CM de-rating corrected to NESO scaled-EFC (D41) (Olly).** The assumption register's literature sweep (docs/redteam/ASSUMPTION_REGISTER.md — every gb_v1_3 value verified against UK sources, adversarially re-checked) found exactly one CONTRADICTED library value: the CM duration de-rating factors overstated payable capacity ~2.3-4x vs NESO's published scaled-EFC values. New frozen release gb_v1_4 corrects them ({1h 0.1047, 2h 0.2094, 4h 0.4174}, needs_review, per-auction tables referenced not stored); app + golden + E2E now price on it. Demo delta verified line-by-line: ONLY the conditional CAPACITY_MARKET stream moved (3,222 → 1,038 £/yr); selection, bills, IRR/DSCR identical — independently proving conditional revenue never sat in the underwritten floor. Register highlights otherwise: CCL 0.775p exact (with legislated 2026/27 steps recorded), the 8.76 p/kVA/day availability charge an EXACT match to NGED EM's HV 2025/26 rate, the 20% perfect-foresight haircut exactly matching Modo's GB-validated 0.80 calibration factor, the red 16:00-19:00 DUoS window confirmed against the NGED EM statement, and one honest NO_SOURCE_FOUND (debt pricing — relabelled an unverified market estimate).

- **Improve panel carries the full stored site (Olly).** Red-team catch: the improve form's hidden fields never included `import_rates` — an improve re-run silently dropped the whole time-of-use tariff (and with it the arbitrage value), and would also have dropped the new levy/capacity lines. All tariff, connection and counterparty fields now travel (dict fields serialized to the same "label:rate" strings the intake parses); verified live.

## 2026-07-07

- **Accounts + per-user projects (D40) (Olly).** Google sign-in via Authlib (env-activated: `GRIDQ_GOOGLE_CLIENT_ID/SECRET` + `GRIDQ_OAUTH_REDIRECT_BASE`), with an always-on anonymous per-browser workspace fallback (itsdangerous-signed session cookie) so isolation works on deployments without OAuth credentials — `/auth/login` states that constraint honestly (503) instead of pretending. Runs carry an `owner.json` sidecar in the store (never a `QuoteRecord` field — hashes stay identity-free); the projects tab, dashboard, review picker, hero tiles and WebGL hero all read the visitor-scoped list (own + legacy/shared runs); another identity's run pages 404 (no existence leak); the curated demo record stays deliberately shared. Sidebar gains the account chip / sign-in state; `deploy/.env.example` documents the vars; `tests/test_app_auth_scoping.py` pins the scoping rules.

- **Landing rebuilt: the pipeline, in the open (Olly).** The 3-step scrollytelling became a 7-stage pinned walkthrough of the actual module sequence (intake → data grading → value-stack gating → dispatch → sizing → finance → the stamped quote), each stage showing IN / WHAT HAPPENS / OUT — including the honest no-viable verdict as a first-class outcome of the sizing stage. Untruthful copy swept from the hero, features and the run overlay ("25-year bankable cashflow", "NPV", "solar simulation", "sizes … inverter" — none of which the engine does); reduced-motion, no-JS and small screens now get a static vertical layout instead of overlapping absolute panels (pre-existing a11y defect); markets section is UK-only. The real form now submits through the streaming job (`/api/run`, same machinery as the wizard) so the multi-minute sizing search never blocks a request; the synchronous `/run/real` stays as the honest no-JS fallback (no profile modelling, stated on the form).

- **The examples/real E2E now returns the CORRECT result: a first-class NO-VIABLE-CONFIGURATION verdict (D39) (Olly).** Red-team trace of the Robert Thomas Farms run against a hand-derived expected result (`docs/redteam/EXPECTED_RESULT.md`) found five divergences, all fixed: the all-unviable sizing pool degenerated the ranking to its smallest-system tie-break and shipped a manufactured 4 kW "optimum" (now: `OptimisationResult.selection_status = no_viable_configuration`, no spec, verdict rendered first-class in the app + all three artifacts, run saved as complete); the invoice's CCL levy and capacity charge never reached the model (now: `TariffStack.capacity_charge_gbp_per_kva_day`, `levies` + capacity fields on both intake forms, standing/capacity as battery-invariant lines on the customer bill); the modelled profile used a daytime archetype clamped to 79.6 kW against documented facts of an overnight-heavy 421 kW site (now: `generate_constrained_hh_csv` builds from the transcribed constraints — annual, day/night split and peak hit exactly, every choice disclosed; silent peak misses warn loudly on all synthetic paths; `/run/real` synthesises like `/api/run`); the £1.06/kWh price-to-target rendered bare on a ~£0.21/kWh site (now: `PriceRecommendation.exceeds_counterfactual` + not-an-offer rendering); and no automated proof existed (now: `tests/test_e2e_examples_real.py`, AC-1..AC-9 incl. a determinism double-run; runbook `docs/redteam/RUNBOOK.md`). Demo golden re-pinned once for the bill's standing line (both sides, savings identical).

- **Dispatch LP placement ties now break toward the firming deliverable: peak-aware tie-break `_EPS_PEAK` + `_EPS_AGE` (D38, supersedes the unlanded D37 `_EPS_SOE`) — Olly.** Within a flat-price band the LP optimum was degenerate (any 4 of the 6 red half-hours discharged 200 kWh for the same £) and HiGHS returned an arbitrary vertex. D37's SoE-holding lean was drafted, then found during verification to *raise* the residual peak import (charge-late pushed charging into the morning ramp) — reversed before it ever landed. Instead the objective gains an epigraph tie-break minimising the residual peak net import (`peak_import ≥ load + charge − discharge` at every t — the exact quantity `customer_bill.peak_import_after_kw` reports, C029) plus a strictly-subordinate discharge-only earliness lean. On the demo: peak after battery 315.4 → 288.14 kW, verified field-by-field that **zero £ figures moved** vs the committed baseline (only dispatch placement, the representative-day index, the schedule ref and the peak). G9 rewritten: pins the levelled 200/3 kW spread across all six red half-hours on every weekday, the exact 253.3 kW residual peak, and the hand-computed closed-form £; the tight-MIC binding test drops to 150 kW (at 250 the levelled charge honestly never touches the cap). Golden re-pinned (`afabcd53…`) — and the verification surfaced that the old pin was Linux-minted and never reproduced on macOS (per-platform degenerate vertex, the exact disease D38 treats; sub-peak placement remains machine-deterministic — see D38). MPC weights-on branch untouched.

## 2026-07-06

- **Landing aurora-glass refresh finished + repo consolidated onto a single `main` (Olly).** The immersive landing gains the aurora-glass front-door layer (Olly's in-progress work, finished and shipped): a living violet/amber/teal aurora (`.gq-aurora` drifting orbs, transform/opacity only) behind frosted-glass consoles (`--glass-*` tokens), iridescent `--grad-iris` panel edges, and a holographic `--grad-holo` hero headline. Finish added here: the headline gradient slowly pans (`gq-holo-pan`) so "battery system" reads as living light, composed after the one-shot `gq-rise` so the entrance stagger is preserved; frozen by the global reduced-motion reset. Decorative only: violet/teal are atmosphere and never encode state (amber/green/red keep their meaning); scoped to `body.immersive`, the data console stays near-black. All colours live in `tokens.css`; `test_app_design_system.py` + the no-arithmetic guard stay green; browser-verified at 1440/375 + markets with zero console errors. Canonical spec: `design-system/gridq/MASTER.md` "Aurora glass layer"; pointer added to CLAUDE.md 6a. **Consolidation:** `main` fast-forwarded to `agent/dispatch-mpc` (the integrated superset - cinematic redesign, north-star drive Waves 6-9, both loop branches all already merged into it); the stale `agent/finance` draft commit was excluded (its finance/credit work is already in mainline in a newer form). No numeric logic touched (UI formats, never computes).
- **"Upload spec sheet" operationalised: users extend the equipment catalogue (Olly).** The Equipment page's "Upload spec sheet" button was a dead link (`/equipment/upload` had no route, so it 404'd) and the catalogue was a static hardcoded list. Now the full flow works: `GET /equipment/upload` renders a category-aware form (`equipment_upload.html`), `POST /equipment/upload` validates and persists the entry, and the Equipment page + wizard picker both show it (merged via `equipment.catalogue()`, user rows tagged `source="user"` with a "Yours" badge and a datasheet link). Honest by construction: the human types every figure and an optional datasheet (PDF/PNG/JPG, <=10 MB) is stored **only as provenance, never parsed for numbers** (the deterministic-core / never-invent-a-number invariant); the catalogue stays display-only and does not feed the optimiser. User entries + datasheets persist under the **gitignored** run store (`$GRIDQ_RUN_STORE/equipment/`, content-addressed), so real product/pricing data is never committed; the serving route (`GET /equipment/datasheet/{name}`) is safe-id checked against path traversal. A bad submission re-renders the form with a message, never a 500. New guard suite `tests/test_app_equipment_upload.py` (21 cases: render, valid add per category, invalid re-render, datasheet store/serve/traversal, re-add overwrite). Frontend-only; MILP untouched. Verified end-to-end in a browser (add a battery + datasheet -> banner -> "Yours" row -> datasheet serves -> appears in the wizard) and against the design-system + no-arithmetic guard tests.

## 2026-07-04

- **Design-system codified + UI anti-pattern sweep (ui-ux-pro-max pass, on `goal/cinematic-redesign`) - Olly.** The design system now has a canonical written spec: `design-system/gridq/MASTER.md` (generated with the ui-ux-pro-max skill, then reconciled - the generator's green CTA, CDN fonts and shadow elevation were rejected with reasons recorded in the file; its anti-pattern list + pre-delivery checklist adopted wholesale) with page overrides under `design-system/gridq/pages/` and a pointer from CLAUDE.md 6a. Screen sweep against that checklist: every unicode-glyph/emoji icon replaced with inline Lucide SVGs (sidebar nav's four glyphs, the 🇬🇧 footer chip -> globe, all three upload dropzones incl. the 📄 bill zone - `currentColor`, `aria-hidden`, sized via CSS); the wizard's Leaflet map goes dark cartography (tile-pane-only invert filter so the amber pin + controls stay true; controls + attribution restyled to tokens) - the last light-mode island on a dark page; `/review` no longer horizontally scrolls at 375px (card heads wrap; long dotted parameter paths in todo items break with `overflow-wrap: anywhere`); dead `.markets/.market` grid CSS removed (the sidebar chip that borrowed its look is now styled by its own scoped rule, and no longer hover-lifts - it is not clickable). Responsive re-verified at 375/768/1024/1440 with zero horizontal scroll and zero console errors; app-layer suites + statics green (21st.dev components consulted as reference only - the stack is server-rendered Jinja, nothing installed). The skill itself lives at user level (`~/.claude/skills/`), not in the repo.

## 2026-07-03

- **Cinematic frontend redesign (branch `goal/cinematic-redesign`, staged commits, unmerged) - Olly (goal session).** The whole app surface moves to one dark design system: `static/tokens.css` is the single token source (near-black surfaces, hairline elevation, ONE amber accent, semantic green/red reserved for pass/fail + firm, self-hosted Inter/JetBrains Mono variable fonts, every number in the mono stack), consumed by app.css AND by charts/wizard JS at runtime via `static/theme.js` - the hand-mirrored chart palette that had drifted is structurally gone. The landing hero is now the engine's own artifact: a Three.js (vendored, r170) WebGL lift of the newest solved run's persisted sizing surface with a pulsing beacon at the real optimum, guarded four-deep (no island / <880px / software GL / reduced motion = the static SVG stays - nothing invented). Run panels became live consoles (hairline header + status dot + "engine · solved" mono readout); the cashflow chart is a draw-on-scroll area with the record's payback year marked; the value stack grows to its record values; key figures count up and land verbatim on the server-formatted string. `static/motion.js` is the single IntersectionObserver for all reveals/counts/chart triggers (once-only, reduced-motion instant, no-JS visible). Verified: full suite green (PYTEST-EXIT captured), screenshots 390/768/1440 + reduced-motion, zero console errors, WCAG AA contrast, Lighthouse desktop 99 / mobile 96 / a11y 98 / BP 100 / CLS 0, landing JS 174KB gz (budget 250KB). GZipMiddleware added (53KB landing HTML -> 19KB over the wire). Audit + full report: docs/goal/redesign-audit.md, docs/goal/redesign-report.md; CLAUDE.md gains the Frontend Design System section.
- **Confirm stage widened + full-series polish - the search now lands the true peak (D34 addendum follow-up) - Olly.** The two-stage confirm stage takes a DIVERSE top-10 (near-clones within 2% on both axes skipped - the old top-5 was a cluster of one point's neighbours), confirms ALL of them (the first-feasible-wins early stop removed: the BEST feasible confirmed candidate wins), and a bounded deterministic compass polish (+/-5% per axis, halving steps, <=10 probes, full-series solves) recovers the peak the typical-day proxy misranks. Measured on the demo: selection improved from 9.94% levered @ 174 kW x 3.0h to **10.35% @ 165.3 kW x 3.15h** in 73.5s total - 0.18pp from the adaptive-refiner reference at ~2.2x less wall time. Golden re-pinned (lineage in the comment); docs (dispatch_engine §6.1, manual ch10, D34 addendum) updated. Also: **C032 signed off by Olly (M5 owner)** - the D36 cost composition is accepted as the M5 shape going forward.
- **Finance charges each physical cost once (D36, F-012) - the demo turns honestly viable - Olly (goal loop 03, after Olly's challenge).** Investigation of the demo's negative economics found a real cost-stack incoherence, not honest thinness: `levies_on_charge` priced levies on GROSS charge while the dispatch objective and the customer bill both use net import (a discharge displaces levied site import - the true levy cost is the round-trip losses: £622 vs £5,180 at the demo optimum), and degradation was charged three ways at once (revenue decay + a fade x capex replacement proxy + the D33 £/MWh wear line) under the model's no-augmentation convention. D36 fixes both: levies on max(0, charge - discharge) with either volume derivable via RTE; degradation charged exactly once via the revenue decay with the warranty throughput cap as the hard cycling limit (the two cost lines removed; library keys stay as unconsumed homes pending M7). ~£8.3k/yr of double-counted cost corrected: the demo optimum now prices project IRR +9.8% / levered +10.6% / P90 DSCR 2.23 - covenant PASSES. Bake-off re-run per D29's finance-change rule; goldens + covenant pins re-based; C032 filed for Oli's M5 review; docs/financial_model.md amended.
- **The wider sizing search GOES LIVE: C025 resolved, D34 engine swap, D35 conservative caps, W7 commitment fixes (goal loop 03) - Olly.** (1) **C025 resolved:** `assumptions/gb_v1_3.yaml` decomposes installed capex into a power component (£150k/MW) + an ENERGY component (£175/kWh) - research-grounded (BNEF ESS Cost Survey 2025 / NREL 2025, recorded in `docs/reviews/2026-07-03-capex-shape-research.md`), labelled placeholder, reconciling exactly to the sourced Neura-plan all-in at the 2h reference; `estimate_capex` prices the decomposed shape release-gated (old releases byte-identical; energy-less callers warned fallback). Duration finally costs real capital: 1 MW at 2h £500k / 4h £850k / 8h £1.55M. (2) **D34:** the engine + demo default swaps to the two-stage continuous search after the D29-mandated bake-off re-ran on the non-degenerate surface - two-stage confirmed best (-3.27% project IRR @ 179 kW x 3.08h) BEAT dense 12x12 (-4.02), adaptive (-3.92) and PSO (-3.80) at ~4x less wall time (65s vs 230-280s, 281 evals); the dense exact grid stays the certification instrument, never the per-request path. The app prices on gb_v1_3. HONEST HEADLINE: with every honesty line live, the demo-scale site prices negative returns and no covenant-feasible size - reported as such (indicative, not bankable), never flattered; the 23.5%-era numbers were per-MW-capex artifacts. (3) **D35 (resolves F-002/C030):** duration-keyed lookups take the conservative NEIGHBOUR - warranty caps ceiling between nodes (`ceiling_value_for_duration`, milp + mpc), CM keeps floor; nothing interpolated; direction pinned by test incl. a real off-node solve. (4) **W7 commitment fixes (C026 resolved):** availability reservations enforce the per-instant deliverable envelope min(power - parasitic, site load); overlapping commitments reserve their SUM via one aggregate constraint (kW never double-sold); `end_time = 00:00` = end-of-day (overnight pre-midnight half representable); flex utilisation accounting capped at committed kW; metered-gap + commitment co-occurrence disclosed. Plus the element-wise P90<=P50 invariant pinned across firmness compositions (review W8-2). Goldens/pins re-based deliberately (release + search + caps move together; lineage in the test comments). C025/C026/C030 resolved, C027-C029 final design positions filed; resolution appendix added to the adoption review.

- **WS4 UI verification pass: the app row closes the goal matrix (F-011) - Olly (goal session).** The sizing surface now consumes every persisted cloud shape as-persisted: the legacy `enumerate-v1` grid, the stage-labelled `m4-two-stage-v2` cloud (mesh from the coarse lattice; refine/confirm as markers; a confirmed value beats a proxy value on a shared cell per D32; proxy points render faint as "guidance" and are never shown covenant-feasible) and the `w6-*-v1` dense reference surfaces - with an honest provenance caption naming the search and the confirmed/proxy split (`view.surface_island`, charts.js, `_surface.html`, 2 new unit tests). The merged customer bill (W7-5) renders from the persisted record on the inspect screen. Every em dash swept from rendered UI copy (93 across templates + app JS, plus the rendered python strings incl. the persisted DEMO_LABEL - one deliberate copy-only golden re-pin, lineage in the test comment) with a new guard test. **F-011 found + fixed:** the landing hero tiles and scrollytelling chips carried figures transcribed from the reference-site benchmark capture (16.0% IRR / 6.8 yr payback / 68.9% bill savings) that no run of this engine produced - they now read off the newest genuinely-solved stored run via the view model with disclosure microcopy, and render an honest n/a empty state when the store holds no solved run. Playwright-verified at desktop 1440 + mobile 390 with zero console errors. Goal STATE: app row verified (fixed); WS1 + WS4 complete.

## 2026-07-02

- **MERGED: karim/north-star-drive (Waves 6-9) into agent/dispatch-mpc — Olly.** The two same-day drives are one tree, worked through the adoption review's 10-item collision register (`docs/reviews/2026-07-02-north-star-adoption-review.md` §4). Beyond textual resolution: both additive QuoteRecord fields kept (`scenario_sensitivities` + `customer_bill`) with record-persisted sensitivities taking precedence over generation-time rows; the customer-bill hook rides the split-solver architecture (honest `None` under MPC, which also warns loudly when commitment payloads are present); commitment windows converted to Europe/London like the DUoS bands (review W7-3); the availability reservation nets off the parasitic draw (F-007) and colliding commitment ids are uniquified + warned, never dropped; infeasible solves keep their compile warnings + HiGHS termination; the perfect-foresight deduction can no longer push the FIRM base negative (P90 <= P50 guard, review W8-1) and the CM "capability-checked" note follows the actual deliverability verdict (review W7-4); `ContinuousSizingEvaluator` prefers `MODEL_VERSION` (F-008) and w6 surfaces persist as `search_version="w6-<method>-v1"`; weighted solves weight the bill/tender aggregates; **gb_v1_2 re-cut as a strict superset of gb_v1_1** (availability/penalties/tnuos verbatim). Demo golden re-pinned once for the persisted bill (selection unchanged, 124.25 kW x 4 h legacy grid; lineage in the test comment). Joint items stay open: C030, the C026 constraint-shape amendments, C025, C027-C029.
- **North-star drive adopted; goal-session work aligned to it + the branch critiqued (Olly; Olly's direction).** `karim/north-star-drive` @ b13ec56 (north-star adoption D27, Waves 6-9) is authoritative: its D27-D30 imported into `docs/decisions.md`, the goal session's same-day decisions renumbered **D31/D32/D33** everywhere, `docs/north_star.md` + the deep-review record + issue 016 copied into the tree. Alignment applied: the two-stage sizing search demoted from engine/demo default to `MilpEngine(use_two_stage_search=True)` **opt-in** per the C025 capex gate (north-star D29) — demo and the legacy 9-point grid pins restored, golden re-pinned to the legacy baseline (moved only by the F-008 model_version stamp; lineage in the test comment); the warranty interpolation policy **withdrawn** per north-star D28 (`duration_policy.py` deleted, call sites back on `value_for_duration`; F-002 re-opened and flagged as **C030** — flooring a DECREASING table is anti-conservative, independently confirmed against the branch's own continuous domain); the goal release renamed **gb_v1_1 → gb_v1_2** (the branch's Wave-8 gb_v1_1 owns the name; the merge-time superset re-cut is recorded in the file header). Per Olly's instruction the adopted implementation was critiqued before merge (three read-only review lenses): headline findings include a P90>P50 ordering inversion in the W8 firmness/perfect-foresight composition, undeliverable + double-sold committed kW and tz-naive window mapping in the W7 solver compilation, and a 10-item semantic merge-collision register — full record `docs/reviews/2026-07-02-north-star-adoption-review.md`, filed as **C031**; the branch's C025-C029 imported into OPEN_COMMENTS with Olly responses.
- **M4 sizing rework: two-stage deterministic continuous search + persisted sizing cloud (D32) - Olly (goal session).** *(Amended same-day in the north-star adoption: OPT-IN behind the C025 gate, never the default; the duration-interpolation half withdrawn — see the adoption entry above.)* `MilpEngine(use_two_stage_search=True)` sizes via `gridq.optimisation.two_stage`: a coarse sweep (10 labelled power fractions spanning 5-150% of the site's binding capacity x 8 durations 0.5-8 h) scored on a typical-period proxy (one medoid day per month x weekday/weekend stratum of the site's own metered series, weighted; complete gap-free days only), then a seeded in-house differential-evolution refinement (~200-evaluation budget, memoised, deterministic), then full-series confirmation of the top candidates - only confirmed, never-proxy evaluations can be selected or reported, and proxy results carry loud provenance warnings. The full evaluated cloud (coarse + refine + confirm, stage-labelled) persists onto the QuoteRecord as a true continuous sizing surface (`SizingGridPoint.stage/proxy`, `SizingGrid.search_version = "m4-two-stage-v2"`, additive + hash-excluded). Weighted proxy dispatch runs through the same MILP via a new `interval_weights` parameter (objective, annualisation and warranty cap; `None` = byte-identical unweighted model) and weighted schedule aggregates, so valuation + finance consume proxy solves unchanged. Duration-keyed tables keep the conservative-flooring `value_for_duration` lookup per the adopted north-star D28 (the interpolation policy originally built here was withdrawn; the anti-conservative warranty direction stays flagged — F-002/C030). One CSV read serves the whole search (`PreloadedDispatchSolver`). The legacy 3x3 enumeration is the engine default and the honest no-series/T0 fallback. Tests: `tests/test_two_stage_sizing.py` (run-twice-identical-sizing - the goal exit criterion - staged provenance, never-proxy selection, covenant gate, grid projection) + `tests/test_sizing_search_components.py` (14 component tests). `docs/dispatch_engine.md` + manual ch. 10 amended.
- **HH ingestion + tariff calendar: timezone-correct, DST-honest timestamp handling (F-003) - Olly (goal session).** `load_half_hourly_csv` now accepts all three real source classes deterministically: naive timestamps stay naive (documented as local-clock period-start), a uniform UTC offset stays timezone-aware, and mixed offsets (the +00:00/+01:00 UK export style across a clock change) are re-parsed onto a single timezone-aware UTC axis with no row dropped; unparseable values still raise, never guessed. `tariff_calendar` converts a timezone-aware index to Europe/London before banding (GB clock content, per its module docstring), so a UTC-labelled summer 15:00 half-hour correctly lands in the 16:00 BST red band; naive input is unchanged. The dispatch MILP's gap segmentation is verified timezone-safe: a timezone-aware series is physically continuous across a DST clock change (no false gap), while a naive local series keeps one conservative segment boundary at the spring-forward label jump. Documented in `docs/input_data_formats.md` ("Timestamps and timezones") and a caution box in manual ch. 4. New invariant tests `tests/test_goal_invariants_hh_tz.py` (naive series across both 2025 DST transitions, UTC-labelled banding equivalence, mixed-offset CSV end-to-end solve).
- **Dispatch MILP: state of energy segmented at metered-series gaps (D31) — Olly (goal session).** The SoE recurrence in `MilpDispatchSolver` now applies only between genuinely adjacent half-hours: any timestamp discontinuity (missing intervals, duplicate stamp, irregular step) starts a new segment, and each contiguous segment gets its own cyclic wrap — SoE is **never chained across a gap**, so the optimiser can no longer charge before a data hole and discharge after it as if no time passed (a crafted gappy series previously yielded ~45 MWh/yr of phantom discharge; it now correctly does nothing). Implemented as a predecessor permutation that reduces *exactly* to the original global cyclic constraint on gapless data — the pinned golden hash is unchanged. Unordered rows are stably sorted by timestamp (warned); segmentation is surfaced as a solver warning. MPC windows anchor their first segment to `initial_soe_kwh`; later in-window segments keep the conservative wrap. New invariant tests `tests/test_goal_invariants_dispatch.py` (gap segmentation, min(MIC,ASC) charge cap, warranty-cycle cap, bill-reduction partition); manual ch. 9 + `docs/dispatch_engine.md` amended (both were silent on gap behaviour). Goal-session tracking docs added under `docs/goal/` (STATE, FINDINGS, DECISIONS, spec extracts).
- **Finance: vacuous deferred capabilities implemented behind versioned assumptions (D33, F-005/F-006) - Olly (goal session).** New assumption release `assumptions/gb_v1_2.yaml` (gb_v1_0 carried forward unchanged, frozen) adds three labelled placeholders: `financing.debt_gearing_pct` (illustrative, pending Sim's portfolio finance, B001), `financing.credit_rate_tenor_policy` (the D19 fold-in from gb_v0_1) and `battery_opex.degradation_marginal_cost_gbp_per_mwh`. In real (non-injected-debt) mode on gb_v1_2 the M9 credit mapping + library gearing now assemble `DebtTerms`, DSCR is available and the P90-DSCR covenant gate in the sizing objective genuinely fires (previously unfireable: no release carried gearing) - every case on the placeholders stays not bankable. Debt sculpting is now real: `DebtTerms.sculpting_basis` defaults to `ANNUITY` (the honest name for the historical level-payment behaviour; all existing constructions unchanged) and `P90_DSCR` sculpts the service to the P90 CFADS profile in closed form (flat target DSCR x with PV(CFADS/x) = principal; zero-rate handled; non-positive CFADS year or short horizon degrades honestly to the annuity with a warning; the case records the basis actually applied). Finance cashflows gain a release-gated degradation marginal-cost line (rate x annual discharge MWh, only when the key exists - old releases byte-identical), and a negative P90 haircut is now clamped to zero with a warning (P90 never exceeds P50). New invariant tests `tests/test_goal_invariants_finance.py` (22); manual ch. 12 + `docs/financial_model.md` amended. Findings F-005 (DSCR gate vacuous in real mode) and F-006 (sculpting decorative) recorded as FIXED in `docs/goal/FINDINGS.md`.

- **Sizing-vs-underwriting fingerprint identity restored after D32 (F-008) - Olly (goal session).** The WS3 trace test caught a silent break in consistency contract #1: the two-stage confirm evaluation stamped `model_version` from the solver's stable `MODEL_VERSION` while `enumerate_sizing`/`underwrite_spec` stamped the class name, so the canonical finance `input_hash` differed between the sizing selection and the standalone reprice of the same spec (all computed numbers were identical). Both legacy call sites now prefer the declared `MODEL_VERSION` (class-name fallback for solvers without one, so placeholder-solver fingerprints are unchanged on both sides); the demo golden did not move. New end-to-end traces in `tests/test_goal_ws3_pipeline.py`: fingerprint identity on a full-year series with injected debt, a real `gridq run` persisting the m4-two-stage-v2 cloud, and the T0 honest degrade.
- **Parasitic (auxiliary) load implemented, release-gated (F-007) - Olly (goal session).** New labelled placeholder `battery_opex.parasitic_load_fraction_of_power` in `assumptions/gb_v1_2.yaml` (0.01 x rated power, illustrative). Dispatch half: the MILP reserves the auxiliary draw against the connection charge headroom (`min(MIC, ASC) - site load - aux kW`) with a disclosed solver warning. Finance half: a `parasitic_load` cost line = fraction x rated power x 8760 h x the mean of the site's own import rates (a disclosed blended-rate approximation; no import rates = line unavailable with warning, omitted not estimated). Key absent (gb_v0_1/gb_v1_0) = byte-identical behaviour on both halves. Tests: headroom reservation + release gating in `tests/test_goal_invariants_dispatch.py`, cost-line trio in `tests/test_goal_invariants_finance.py`. Also this entry: `docs/dispatch_engine.md` section 6 + manual ch. 10 were rewritten for the two-stage search (chapter rebuilt clean, zero em dashes). *(The 9-point-grid pin updates and the two-stage golden re-pin originally in this entry were reverted in the same-day north-star adoption — the engine default stays on the legacy grid per the C025 gate; see the adoption entry.)*

## 2026-07-01

- **W6-7 slice 1 (issue 016 stages 7–8): surface persistence + the real-site validation — and finding C025 — Karim (`agent/sizing-search`, /loop iteration 3).** **(a) Stage 7:** `search_result_to_sizing_grid` projects any `SizingSearchResult` onto the frozen `SizingGrid` contract — the wider continuous surface rides the existing D23 additive `QuoteRecord` seam with **zero schema change**; `selected` is the covenant winner only (honest `None` otherwise), `bankable` keeps the full SizingRun conjunction (never softened by the projection); 2 new tests incl. JSON round-trip. Engine wiring deliberately deferred to the stage-9 production-method decision. **(b) Stage 8 real-site validation** (demo site, real `MilpDispatchSolver`): legacy 3×3 → 124 kW × 4 h @ 21.21% project-IRR; dense 12×12 (67 in-band pts, 52.7 s) → 162.7 kW × 7.79 h @ 23.48%; adaptive (117 evals, 9 rounds, 103.6 s) → 165.2 kW × 7.99 h @ **23.50%** (+0.08% over the dense reference — sub-grid precision on real data). **The wider scope changes the recommendation: +10.8% objective vs the shipped 3×3** — issue 016's hypothesis confirmed. **(c) Finding C025 (filed):** the optimum pins to the 8 h duration-band edge because `battery_capex.installed_cost` is per-MW **only** — energy is capex-free on the placeholder shape, making long durations artificially attractive (the 3×3's 4 h cap was hiding it). An energy (£/kWh) capex component must land in the assumption library before wide-duration recommendations are meaningful; **the engine default must not swap to the wider search before C025 resolves.** Honesty intact throughout: all runs scored on the labelled `project_irr_pct` fallback (no debt), grid persisted with `selected=None`/`bankable=False`. **Whole tree green: 602 tests; `ruff`/`ruff format`/`mypy`(111)/review_gates clean.**
- **W6-6 complete (issue 016 stages 5b–6): the adaptive zoom refiner + convergence budget — Karim (`agent/sizing-search`, /loop iteration 2).** `gridq.sizing.search.adaptive_refine_search`: coarse in-band grid + seeded LHS + **explicit duration-key step-line probes** (the D28 step-awareness measure — points sampled ON the {1,2,4}h lines from `DEFAULT_DURATION_HOURS`, and **no surrogate/gradient anywhere**, pure zoom sampling, so the piecewise-constant surface cannot mislead it), then successive sub-box zoom (`zoom_factor` per round) around a **feasibility-first incumbent** (covenant-feasible points always outrank infeasible — the bankable objective governs when debt is live, honest fallback otherwise), de-duplicated, budgeted (`max_evaluations`, deterministic truncation + warning), stopping on Δ<ε over `patience` rounds; the convergence curve is logged per round (`RefinementRound` on `SizingSearchResult.rounds`). Certification tests: on a sharply-peaked stub surface the refiner **matches-or-beats the 9×9 dense reference and lands nearer the true peak** (sub-grid precision proven, not lucky); deterministic evaluations+rounds; budget respected; monotone incumbent; feasibility-aware winner on the levered metric under injected debt. 6 new tests. **Whole tree green: 600 tests; `ruff`/`ruff format`/`mypy`(111)/review_gates clean.** Next: W6-7 (dense-grid surface persistence + real-site validation + the stage-9 bake-off incl. a seeded PSO/DE comparison baseline).
- **W6-6 slice 1 (issue 016 stage 5, reference half): the dense exact-grid reference search — Karim (`agent/sizing-search`, /loop iteration 1).** New `gridq.sizing.search`: `dense_grid_search` scores every in-band point of `SizingDomain.grid(n_power, n_energy)` through the exact dispatch+finance evaluator via the stage-4 parallel harness, returning a `SizingSearchResult` — the full surface in deterministic order (`evaluated`), a **metric-honest** selection (rank = metric tier levered > project-IRR > priced-floor, then value, then smaller-system tie-break, mirroring `SizingObjective.rank_key`), an indicative `best`, and a covenant-gated `best_feasible` that is honestly `None` without injected debt (B001) with an "indicative only" warning. An empty grid at coarse resolution (band filters every linspace corner) warns and returns no point — never invents one. Per D28 the reference surface carries the duration-key steps the refiner must respect. `method="dense_grid"` is stamped so a refined result can never masquerade as the reference in the stage-9 bake-off. 5 new tests (`test_sizing_search.py`). **Whole tree green: 594 tests; `ruff`/`ruff format`/`mypy`(111)/review_gates clean.** Next slice: the adaptive refiner + convergence budget (stages 5b–6).
- **W6-5 (issue 016 stage 4): parallel LP harness + the solve-cost benchmark — the wider scope is affordable — Karim (`agent/sizing-search`).** New `gridq.sizing.parallel`: `evaluate_points_parallel` (round-robin chunks, **one evaluator per worker thread** — the stateful solver is never shared; deterministic ordered reduction test-proven identical to the sequential path) + `benchmark_parallel_evaluation` (`SolveBenchmark` — an explicit dev artifact, never persisted to a quote). **Measured** (demo site, full metered year, real `MilpDispatchSolver`, 8 LHS points): sequential 2.00 s/solve (~1,800/h); 4 worker threads 0.80 s/solve effective (~4,500/h, **2.50×** — HiGHS releases the GIL, so thread parallelism is real); parallel results identical to sequential; scored points honestly labelled `priced_floor_value_gbp` (no DebtTerms on the real path — B001). Verdict recorded in issue 016: a ~1,600-point dense exact reference grid ≈ 21 min at 4 workers. 5 new tests (`test_sizing_parallel.py`). **Whole tree green: 589 tests; `ruff`/`ruff format`/`mypy`(110)/review_gates clean.**
- **W6 (issue 016) stages 1–3 landed: continuous sizing domain hardened + site-scaled bounds + the continuous evaluator — Karim (`agent/sizing-search`).** Driving north_star Wave 6 off the adopted task list. **(a) LHS band-clamp defect fixed** (the issue-016 scope addition): `SizingDomain.latin_hypercube` now LHS-samples power only within the **band-feasible power window** (`[energy_min/d_max, energy_max/d_min] ∩ power box`) so every sample is in-band by construction — the old clamp branch could emit candidates outside the duration band `grid()`/`contains()` enforce; a box that never intersects the band is now rejected at construction. **(b) `SizingDomain.from_site` (stage 2):** site-scaled continuous bounds from the site's own binding scale (observed peak `load_data.max_import_kw`, else `connection.mic_kw` — mirroring `MilpEngine._default_search_space` exactly, never an invented kW), a labelled STRUCTURAL fraction band (`DEFAULT_POWER_FRACTION_RANGE` 0.05–1.0, wider than the legacy {0.25, 0.5, 1.0}), band-spanning energy envelope; no sizing basis → a data-ask `ValueError`, never a fabricated default. **(c) `ContinuousSizingEvaluator` (stage 3):** scores arbitrary continuous (power, energy) points through the **same** `_evaluate_spec` exact dispatch+finance path as the grid enumeration (streams/constraints resolved once, per-call cost = dispatch+finance only), and — the objective-metric honesty addition — every `ScoredPoint` records **which metric it was scored on** (`levered_return_pct` / `project_irr_pct` / `priced_floor_value_gbp`), with a parity test pinning the floor fallback to `MilpEngine._floor_value`, so a refined surface can never masquerade as the levered objective while B001 keeps debt absent. **(d) D28 recorded:** duration-keyed assumptions keep D22's conservative flooring on the continuous domain (honest direction at off-key durations; no golden re-baseline); the stages-5/6 refiner must be step-aware; interpolation deferred to sourced assumption content. New exports on `gridq.sizing`; 16 new tests (`test_sizing_domain.py` 10, `test_sizing_evaluator.py` 6). **Whole tree green: 584 tests pass; `ruff`/`ruff format`/`mypy`(109)/review_gates clean.** Next per the task list: stage 4 (parallel LP harness + solve benchmark), stages 5–9.
- **North Star drafted — the two-sided firming goal + Waves 6–11 roadmap — Karim (docs only; no engine code touched).** New [`docs/north_star.md`](north_star.md) (**adopted same day on Karim's go — D27**): the product goal every wave iterates toward — the BTM battery as a **two-sided firming asset** (firm the customer's cost against a volatile grid · firm the grid with contracted, provably deliverable flexibility · firm the lender's case with penalty-aware, firmness-differentiated underwriting), an 8-point world-class definition split test-enforced vs evidence-based (incl. the cost-compression wedge), the honest baseline, and the sequenced Waves 6–11 road with lanes and a commercial anchor (the pilot site as forcing function). Grounded in a **13-agent deep review** (8 subsystem readers + 5 analysis lenses) whose full findings + web sources are checked in as [`docs/reviews/2026-07-01-deep-review.md`](reviews/2026-07-01-deep-review.md) — headline verified findings: the stream→dispatch constraint seam is dead (`problem.constraints` never read by the solver; firming kinds are names only), `GateContext` has no producer (CM/Piclo permanently CONDITIONAL — no live path for contracted firming revenue into the priced floor), `end_of_life_deliverable` is a permanent stub (the CM deliverability proof and M4's rejection criterion cannot fire), no penalty economics anywhere (producer-side gap), P90 is one flat haircut with `perfect_foresight_haircut_pct` unconsumed and M7 nonexistent, the customer bill model is blank (C017) with zero TNUoS representation, and MIC/ASC headroom — the named connection primitive — is priced £0. The draft itself passed a **4-lens adversarial critic panel** (founder / engineering / honesty / code fact-check — all repo claims source-verified) and every blocker/major fix is incorporated. Synced in the same change: `CLAUDE.md` status block updated Wave-1/2→Wave-5 reality + doc-map/mission pointers to the north star; `ACTIVE_PLAN` refreshed (was stale at W5-2; now records the `agent/sizing-search` lane); [issue 016](issues/016-adaptive-sizing-search.md) gains the review-mandated Wave-6 scope additions (duration-key policy superseding D22's flooring sub-decision, covenant-feasibility-aware refinement, objective-metric honesty, LHS band-clamp fix). Flagged for immediate action outside the waves: enable the dormant `GRIDQ_BASIC_AUTH` gate before the next external share of the live URL (real-prospect prefill exposure). Note: `GRIDQ_CONTRIBUTOR` is unset on Karim's machine — set in `.claude/settings.local.json`.

## 2026-06-30

- **GridQ Platform Manual — a complete LaTeX user-and-rebuild manual — Olly.** New `docs/manual/`: a **144-page** compiled PDF (`GridQ-Platform-Manual.pdf`), **17 chapters + 2 appendices** (A glossary, B assumption-library reference) documenting the whole behind-the-meter BESS sizing and underwriting platform in plain language and in enough detail to re-implement it from scratch. Ordered by data flow — a raw meter file at the top of Chapter 4 through the three finished artifacts by Chapter 15 — across data contracts, ingestion, assumptions/provenance, the engine seam, the value stack, dispatch, sizing, scenarios, finance, quotes, telemetry and outputs, then the web app (Chapter 16) and the CLI (Chapter 17). Each module chapter carries a plain-language opening, an inputs/outputs box, at least one TikZ flow diagram, and a **"How to rebuild this"** recipe. Chapter 16 embeds six screenshots captured from the live app (`figures/` — landing, wizard, run, dashboard x2, artifacts). A frozen shared `preamble.tex` holds the brand styling, callout boxes, TikZ styles and a `\screenshot` helper; `main.tex` is the document root; `build_chapter.sh` compiles a single chapter standalone against the preamble. Honours the house invariants: numbers are never authored by prose, British spelling throughout, no em dashes. Docs only; no code or engine change. Left **uncommitted** for review.

## 2026-06-29

- **UI-refine loop (U00-U10): cinematic public landing + a global design system — Olly (`loop/ui-refine`).** A self-paced refinement loop over the frontend on top of the round-1-6 redesign; presentation only, the MILP optimiser and every number untouched. (a) **Design system** established (`docs/design/ui-system.md`): tokens, a reusable animated background and a11y baselines applied consistently across the chrome (U00). (b) **Landing rebuilt as a full-bleed cinematic story page** — after feedback that the earlier pass "looked exactly the same", the public landing moved outside the app shell into a new `base_immersive.html` + `.lp-*` namespace: a 100vh hero with an oversized kinetic headline, ambient glow, count-up stats and a nav that solidifies on scroll; a re-skinned dark scrollytelling pipeline (Ingest -> Optimise -> Underwrite); four inline-SVG feature visuals (3D sizing surface, revenue stack, 25-yr cashflow, the three artifacts) animating in on scroll; a market-agnostic markets section (ISO codes + a LIVE badge, dropping unreliable flag emojis); a no-JS `.reveal` safety gate. App pages keep the sidebar shell. (c) **Run/compute** screen got a cinematic dark pipeline overlay with a reusable animated background (U07). (d) **Dashboard** got section rhythm, exact count-ups and mobile overflow fixes (U08). (e) **Wizard** gained a step-1 (Site) purpose intro tying location/coordinates/grid connection to the DNO tariff bands, the solar simulation and the optimiser's sizing cap; the full Site -> Consumption -> Load -> Equipment -> Review intake validated live (U02-U06). (f) **HTML report rebranded** to the design system (`gridq/outputs/html_report.py`): Inter type, slate surfaces, brand-orange section headings, a branded letterhead, an `@media print` block and mobile overflow-wrap on long provenance values; presentation-only, every figure still rendered by the same generator (golden hash + `test_outputs_no_arithmetic_in_html` stay green) (U09). (g) **Reusable `.state` component + a designed 404** (the last bare state); global chrome/state coverage audited (U10). Bar: fixed a masked verification bug (a `tail` pipe had been hiding a pre-existing E501 since U09); ruff/format/mypy/pytest all green; verified desktop 1440 + mobile 390 with 0 horizontal scroll; the arithmetic-free-template invariant held; design-judge found no majors. Deployed to the live `:80` / cloudflared tunnel (`static_v` bumped). Committed on `loop/ui-refine` (not merged to `main`). Remaining items are all human-gated (brand sign-off, real copy, illustrative-figure confirmation, imagery/licences, Olly's design review).

## 2026-06-28

- **Olly-modules completeness loop (L01-L23): Brief-driven gap-closing across the scoped modules — Olly (`loop/olly-modules`).** A self-paced loop that swept Olly's modules (M0 ingestion, M1 schema, M5 finance, M6 scenarios, M8 outputs, X.1-X.3) against the System Brief and closed every code-ownable gap with a test; five adversarial completeness critics ran until the sweep came back dry. Highlights: (a) **Telemetry** — every run now logs outcome (new canonical `QuoteOutcome` enum, PENDING at creation), `realised_vs_underwritten_pct` (None until M&V, never guessed) and a free-text win/loss `outcome_reason` (L11/L18); `summarise_telemetry` aggregates the cost-compression dataset (system-vs-benchmark human-minutes + compression ratio, win-rate by data tier), naming the two data-gated metrics unavailable rather than guessing (L17). (b) **Outputs/quotes** — indicative artifacts never cite gated streams (enforced at the view layer, L13); DSCR/return sensitivities re-run under each Low/Central/High scenario and persist as `ScenarioSensitivity` rows in the underwriting pack (L15), which also reports time-to-energisation (COD delay) as a column (L16); the proposal states term, structure, indexation and buyout schedule explicitly (L19/L22); L23 renders the scenario sensitivities into the HTML report (closing the L15/L16 output gap). (c) **Ingestion** — T2 now requires a >=12-month calendar span (`coverage_days >= 363`), not just an interval count, so a dense short-window file grades T1 (L20). (d) **Schema** — added `SupplyBoundary` (import/supply MPAN + behind-the-meter perimeter + metering point) as an additive `SiteModel.supply_boundary` field (L21); pinned battery-only sizing with the net-load seam held open for v2 solar (L10). (e) **Finance** — `check_capex_plausibility` sanity-checks installed unit capex (£/MW) against a library benchmark band, warn-only, never altering a figure (L14). Additions that touch the record are hash-excluded, so `input_hash`/`quote_id` and the determinism golden are unchanged (goldens re-pinned only where the demo's own output rows changed). Bar: **643 tests pass**, ruff/format/mypy clean. Committed on `loop/olly-modules`; only human-gated items remain (H01-H06: commercial term/indexation/buyout values, real price bands).
- **W9 final slice → WAVE 9 COMPLETE (buildable scope) → THE NORTH-STAR DRIVE CONCLUDES — Karim (`agent/sizing-search`, /loop iteration 19, final).** **(a) The capped-bill structure exists honestly:** `PriceStructure.CAPPED_BILL` (additive enum member, finance lane) + a `recommend_price` branch that returns structure-only with NO value, naming both hard dependencies verbatim — the M&V method (**boundary Open Q4, owner UNRESOLVED**; PE ships the data contract only) and the sourced volume-variance keys (`bill_products.volume_variance_pct`/`weather_variance_pct` — the risk a bill guarantee transfers onto the seller, explicitly NOT the P90 revenue haircut); savings-share now names Open Q4 the same way. **(b)** `bill_products` registered as the fourth D30 growth section (empty homes; all releases predate it). 3 new tests. **Whole tree green: 674 tests; `ruff`/`ruff format`/`mypy`(115)/review_gates clean.** **Drive tally (19 loop iterations, 2026-07-01→02):** north star adopted (D27) · Waves 6–9 complete · 568→674 tests · D27–D30 · C025 finding · C026–C029 filed for Olly · C017/C020/C023 resolved · gb_v1_1 cut · 3 real defects found-and-fixed · the changes page live on the VPS. **Remaining is not buildable solo:** #15 externally gated (RTM/B002, NESO/Piclo/capex-shape sourcing incl. C025, Sim's gearing/B001, Olly's C026–C029 co-decisions, M7 realised data) and #16 human-only (GRIDQ_BASIC_AUTH on the live URL — still open, still urgent). The loop stops here per its rule.
- **W9 slice 2: connection-headroom — the objective design filed for Olly (C029) + the honest peak measurement lands — Karim (`agent/sizing-search`, /loop iteration 18).** The Stage-2 primitive stays deliberately un-priced, for the right reason made explicit: **the dispatch is bill-optimised, not peak-optimised** — charging can legitimately RAISE the site peak off-red-band, so asserting ASC-reduction £ from today's schedule would be invention. **(a) C029 filed:** the two candidate objective shapes for Olly (a max-import variable with a site-sourced £/kVA coefficient — needs additive `TariffStack` capacity-charge fields, jointly decided; or ε-weighted peak minimisation + shadow analysis), with the stream staying £0 until the objective actually protects the peak AND the site supplies its capacity-charge rate. **(b) The interim truthful measurement:** `CustomerBill.peak_import_before_kw/after_kw` (identities off the solved series, hash-safe additive fields) flow through `BillSavingsView` into the proposal as a "Peak import (measured)" row **with a permanent not-priced label** naming C029; older records without peaks render nothing (no invented physics). 2 new tests. **Whole tree green: 671 tests; `ruff`/`ruff format`/`mypy`(115)/review_gates clean.** Remaining W9: fixed/capped-bill pricing structures with volume-variance keys + the M&V dependency (boundary Open Q4) named.
- **W9 slice 1 (Wave 9 opens): the PV seam design filed for Olly (C028) + the charging calendar becomes versioned content — Karim (`agent/sizing-search`, /loop iteration 17).** **(a) C028 filed, SiteModel untouched:** explicit on-site generation (optional `LoadDataSummary` generation fields + PV-battery self-consumption dispatch + a compilable G99 export-*limit* constraint, merchant export *pricing* staying Wave-10/RTM-gated) is a deliberate D8 frozen-`SiteModel` joint event — the full design (field shapes, hash policy question, ingestion loader, bill-counterfactual honesty) is in OPEN_COMMENTS for Olly's co-decision. **(b) The GB DUoS charging calendar is no longer hardcoded:** `DuosWindows` + `duos_windows_from_assumptions` — a release may override the weekday red/amber/green windows via a `duos.band_windows_weekday` structured value (validated; malformed → the documented fallback with a warning, never a guess; an applied override warns loudly with its assumption status), threaded through `duos_band_labels`/`red_band_mask`/`duos_rate_series`/`energy_price_series` and resolved once per solve in the MILP. **Golden-preserving by design:** an absent key (every current release) yields the documented code fallback silently — byte-identical behaviour, proven by the full bar. This insulates 15-year quotes against the Reformed-National-Pricing / DUoS-SCR reshaping the north star flagged. 4 new tests. **Whole tree green: 669 tests; `ruff`/`ruff format`/`mypy`(115)/review_gates clean.** Remaining W9: connection-headroom pricing (max-import shadow value — Olly's solver lane, design next), fixed/capped-bill structures (M&V dependency named, boundary Open Q4).
- **W8 final slice → WAVE 8 COMPLETE (C020 + C023 resolved): the memo's sensitivities render from real scenario runs, and the provenance moat displays where a lender looks — Karim (`agent/sizing-search`, /loop iteration 16).** **(a) Sensitivities:** new `outputs.sensitivities.scenario_sensitivity_rows` — the standard Low/Central/High set (weights from the library) run over the record's OWN cashflows via `run_scenario_set`, per-scenario project IRR computed deterministically, per-scenario DSCR honestly n/a until debt terms exist (B001), identity bands named in the note (a spread is never fabricated). The HTML report's DSCR card — which previously ignored supplied sensitivities entirely — now renders them. **(b) C020/C023:** `AssumptionSourceRow` + `OutputViewModel.assumption_sources`: every stream-cited assumption resolved from the pinned release and displayed with value/status/confidence/**source** in a new memo "Assumptions & sources" section + an HTML sources card; unresolvable keys shown *absent*, never dropped; honest pending notes without the library. **(c) Wiring:** `generate_artifacts(..., assumptions=)` auto-builds both; `generate_for_run` auto-loads the release by `quote.assumption_version` (unresolvable → honest degrade) — the CLI and app get all of it with zero caller change. 5 new tests. **Whole tree green: 665 tests; `ruff`/`ruff format`/`mypy`(115)/review_gates clean.** **Wave 8 of the north star is complete** (D30 · foresight consumed · per-firmness P90 · penalty line · contracted-basis DSCR · C027 filed for Olly · gb_v1_1 · sensitivities · C020/C023). Next: Wave 9 (customer products & site completeness).
- **W8 slice 5: gb_v1_1 cut (the release lines reconverge; the firming sections get real homes) + the ValueStreamResult design filed for Olly (C027) — Karim (`agent/sizing-search`, /loop iteration 15).** **(a) C027 filed, contract untouched:** the `ValueStreamResult` growth (availability/utilisation split, `contract_tenor_years`, producer-side penalty attribution) is a deliberate D8 joint event — the design is in OPEN_COMMENTS for Olly's co-decision, not built unilaterally; the interim CashflowModel-layer shapes (W8 slices 3–4) cover the need meanwhile. **(b) `assumptions/gb_v1_1.yaml`** (new file — gb_v1_0/gb_v0_1 stay frozen, never edited): folds `financing.credit_rate_tenor_policy` from the gb_v0_1 line (**the D19/D18 release divergence closes** — quotes on the v1 line can finally resolve the M9 credit mapping) and populates the D30 growth sections as fully-labelled placeholders under the 8-field discipline: `availability` (empty-value homes — a £/MW/h price is asserted only from an awarded tender), `penalties` (CM non-delivery rate 0.25×clearing + 200%/100% caps as **research-grade placeholders whose `source` names the unverified review record** — confirm against the CM Rules before any real use), `tnuos` (the un-firmable residual home, never estimated per-site). End-to-end proof: contracted CM revenue on gb_v1_1 activates the W8 worst-case penalty line in P90 — and stays `not_bankable`. 2 new tests. **Whole tree green: 660 tests; `ruff`/`ruff format`/`mypy`(114)/review_gates clean.** W8 remaining (final slice): feed `run_scenario_set` into the memo's sensitivities slot + thread per-assumption sources into outputs (C020/C023).
- **W8 slice 4 + UI reflection + the public changes page — Karim (`agent/sizing-search`, /loop iteration 14).** **(a) Contracted-basis DSCR:** `CashflowModel.p90_contracted_ebitda_gbp` (deliberately conservative: contracted P90 revenue bears the penalty line AND all costs — no cost allocation invented) is measured by `assess_debt` against the same debt service (`DscrResult.min_contracted_dscr`/`contracted_by_year`, additive) — **reported, and gated only informationally** via the additive `dscr_covenants.min_p90_dscr_contracted` key (absent today → a loud "measured, NOT gated" warning; present → a meets/BREACHES note); the blended P90 covenant remains the formal gate and DSCR stays unavailable without Sim's gearing (B001). The financial model threads the basis only when contracted revenue is actually priced (a merchant-only run has no basis to report, not a negative one). 5 new tests. **(b) Demo UI reflection:** `DemoEngine.last_customer_bill()` added (mirrors `MilpEngine`) so the demo proposal shows the record-backed bill headline instead of the pending seam. **(c) The drive's changes page** — a self-contained, no-CDN HTML summary of the 1–2 July north-star drive (goal, Waves 6–8, findings, honesty notes, open human items) — deployed **additively** to the live VPS at `/static/changes-2026-07.html` (collision-checked, one new file, nothing of the deployed checkout touched; verified 200 locally and over the public tunnel). **Whole tree green: 658 tests; `ruff`/`ruff format`/`mypy`(114)/review_gates clean.**
- **W8 slice 3: firmness-differentiated P90 + the worst-case penalty line — contracted revenue gets its own risk shape — Karim (`agent/sizing-search`, /loop iteration 13).** **(a) Firmness split:** `revenue_by_firmness` + `CashflowModel.annual_revenue_firm_gbp`/`annual_revenue_contracted_gbp` (raw year-1 £; sums proven equal to the stream total) — the substrate the coming contracted-basis debt sizing reads. **(b) Per-firmness P90:** CONTRACTED revenue reads its own additive key (`p50_p90_haircuts.p90_revenue_haircut_contracted_pct`) when a release carries it; absent (both current releases) it shares the merchant haircut with a loud warning naming the gb_v1_1 fix — so today's numbers are **identity-equal to the pre-slice formula** (test-proven), and differentiation activates purely by content. **(c) The penalty line:** the D30 `penalties` section is consumed — **empty = a warned DATA-ASK whenever contracted revenue is priced** ("the P90 downside excludes penalties and is optimistic on this axis"), never zero; when `penalties.cm_annual_cap_pct_of_revenue` is populated, the deterministic worst case (cap% × CM revenue) enters the **P90 series only** (`CashflowModel.p90_penalty_exposure_gbp`, decaying with revenue), labelled as the bounded cap, not an expected value — P50 stays unpenalised expected operation (identity-tested both sides). 5 new tests (`test_finance_firmness_p90.py`). **Whole tree green: 653 tests; `ruff`/`ruff format`/`mypy`(114)/review_gates clean.** Remaining W8: contracted-vs-merchant CFADS in `assess_debt` (per-basis covenants), `ValueStreamResult` growth (joint — flag for Olly), gb_v1_1 (fold `credit_rate_tenor_policy`, populate the new sections with sources).
- **W8 slice 2: the perfect-foresight haircut is finally CONSUMED — P50 stops being the raw LP optimum — Karim (`agent/sizing-search`, /loop iteration 12).** The deep review's top honesty gap closes: `p50_p90_haircuts.perfect_foresight_haircut_pct` (in the library since gb_v0_1, owner Olly, previously **zero consumers**) now enters `build_cashflow_model` as a **NAMED deduction** (`CashflowModel.perfect_foresight_adjustment_gbp`): per-stream revenue stays RAW (it must match the record's stream table everywhere — no silent shrink of a stream figure), while the EBITDA/FCF basis nets off the premium on the **dispatch-optimised streams only** (`_PERFECT_FORESIGHT_STREAMS` = tariff arbitrage + DUoS avoidance — availability/capacity payments carry no foresight advantage). Loudly labelled: the warning quotes the %, the year-1 £, the assumption's status, and that the M7 backtest replaces it with a measured per-stream achievable/LP ratio; a missing/unusable key degrades to an explicit "P50 is the RAW perfect-foresight upper bound" warning — never a silent flatter. P90 compounds on the adjusted P50 (test-proven identities). **Consequence, handled honestly:** the demo's illustrative debt (55% gearing) no longer cleared the 1.30 covenant anywhere — DSCR is leveraged to margin, and the correction halved the demo's thin CFADS — so `DEMO_DEBT_TERMS` was re-tuned 55%→25% gearing (an empirical sweep: at 25% exactly 1 candidate passes, 8 are rejected, the optimum selects — the covenant machinery demonstrably firing; a labelled display parameter, never an underwriting value). 4 new tests (`test_finance_perfect_foresight.py`). **Whole tree green: 648 tests; `ruff`/`ruff format`/`mypy`(114)/review_gates clean.** Next W8 slices: firmness-differentiated P90 + the penalty cost line (reading the D30 `penalties` section), contracted-vs-merchant CFADS.
- **W8 slice 1 (D30): the assumption-schema evolution mechanism — the firming content has library homes without touching a frozen release — Karim (`agent/sizing-search`, /loop iteration 11).** `AssumptionLibrary` grows by **optional sections** (D30): the 23 `SECTION_NAMES` stay required forever (test-guarded); new sections join `OPTIONAL_SECTION_NAMES` as explicit fields defaulting to empty — an empty section means "this release predates it", **never zero**; a mistyped section is still a hard error. First three homes declared (empty — no values invented; content lands in gb_v1_1 with sources): `availability`, `penalties`, `tnuos`. Frozen `gb_v0_1`/`gb_v1_0` proven to load byte-identically with the optional sections empty; a release carrying one resolves through the unchanged `get()` door. 4 new tests. **Whole tree green: 644 tests; `ruff`/`ruff format`/`mypy`(114)/review_gates clean.** Next Wave-8 slices: consume `perfect_foresight_haircut_pct` (in the library, zero consumers), firmness-differentiated P90 + the penalty cost line, contracted-vs-merchant CFADS split, `ValueStreamResult` growth (joint event — flag for Olly), gb_v1_1.
- **W7-5 complete → WAVE 7 COMPLETE (C017 resolved): the bill headline renders from the record — the firming substrate is done end-to-end — Karim (`agent/sizing-search`, /loop iteration 10).** `BillSavingsView` now reads the persisted `QuoteRecord.customer_bill` (every figure copied verbatim — the view copies, the templates format, nothing recomputes); the proposal's "Your bill — before & after" and the HTML report render the record's numbers **with the `basis` honesty label always alongside the headline** (what is NOT modelled travels with the saving, so it can never read as a whole-bill promise); a record without a bill keeps the labelled-pending seam byte-for-byte. The AST arithmetic-free guard caught my string `+` in `html_report.py` during the bar — fixed to the module's f-string idiom (the invariant works). 4 new tests. **Whole tree green: 639 tests; `ruff`/`ruff format`/`mypy`(114)/review_gates clean.** **Wave 7 of the north star is complete:** the firming-contract shape (W7-1/C026) → reserved capability as solver physics (W7-2) → end-of-tenor deliverability computed, M4's rejection firing (W7-3) → evidence-bearing gates flipping contracted revenue into the priced floor (W7-4) → the customer bill headline (W7-5). All three firming promises (customer / grid / lender) are now mechanically real, and everything stays honestly `not_bankable` on placeholder content. Next: Wave 8 (underwriting risk shapes), starting with the assumption-schema evolution mechanism.
- **W7-5 slice 1 (C017): the customer bill EXISTS — computed from the series the MILP optimised, persisted to the record — Karim (`agent/sizing-search`, /loop iteration 9).** New `gridq.schemas.customer_bill.CustomerBill`: the annual bill before/after decomposition (energy ToU / DUoS / £-per-kWh levy lines; import kWh both sides; validator-enforced component sums and `saving = before − after` — a **negative saving is representable**, round-trip losses are real energy bought at the price faced) with a mandatory `basis` honesty label listing what is NOT modelled (standing charges, capacity/ASC, TNUoS residual — Waves 8–9 content), so the headline can never read as a whole-bill promise. Producer chain mirrors D23/D25 exactly: `HalfHourlyDispatchSchedule.customer_bill()` (identities over the solved series; `None` when the tariff has no time-mappable energy rate — the honest pending seam, never a partial figure) → `MilpDispatchSolver.last_customer_bill()` → `MilpEngine.last_customer_bill()` duck-typed hook → `generate_quote` persists additively onto `QuoteRecord.customer_bill` (Optional, **hash-excluded** — quote_id proven unchanged; BaselineEngine leaves it `None`). 5 new tests (`test_customer_bill.py`), all component figures asserted as manual-sum identities. **Whole tree green: 635 tests; `ruff`/`ruff format`/`mypy`(114)/review_gates clean.** Slice 2 next: `BillSavingsView`/proposal rendering reads the persisted bill (numbers-from-record, honest seam retained when absent) — then C017 closes.
- **W7-4: the first GateContext producer — contracted firming revenue has a LIVE, evidence-bearing path into the priced floor — Karim (`agent/sizing-search`, /loop iteration 8).** **(a) `CommitmentEvidence`** (new, `gridq.schemas.commitment`): every asserted gate fact names who confirmed it, on what basis, against which contract `reference` (**required** — a fact without provenance cannot be evidence); an engine-computed verdict says so (`confirmed_by='engine:…'`, never dressed up as third-party confirmation). **(b) `GateContext.evidence`** (additive, D12-sanctioned): the provenance list paired with the asserted booleans. **(c) `produce_gate_context`** (`gridq.sizing.deliverability`): asserts facts ONLY from **referenced** commitments — CM deliverability from the W7-3 computed check (one failure ⇒ `False`; unassessable ⇒ unset + warning), a referenced flex award IS the contracted-tender fact; unreferenced commitments warn "modelling input, not evidence" and assert nothing. **(d) Solver wiring:** `MilpDispatchSolver._solved` produces the context from the seam and threads it into `build_value_streams` (was `context=None` forever — the deep review's "GateContext has no producer" finding closes); `_capacity_market_value` now prefers the **engine-reserved** `deliverable_capacity_mw` over nameplate×de-rating. **End-to-end on the real solver (test-proven):** a referenced, end-of-tenor-deliverable CM commitment flips the gate CONDITIONAL→**CONTRACTED** and prices the reserved 50 kW (never nameplate); an *undeliverable* commitment keeps the gate CONDITIONAL (a dispatch reservation is not deliverability); a referenced flex award prices its tender-window MWh — **and every one stays `not_bankable` on placeholder content.** No commitments ⇒ context `None` ⇒ byte-identical runs (all goldens green). 8 new tests. **Whole tree green: 630 tests; `ruff`/`ruff format`/`mypy`(113)/review_gates clean.** Wave 7's grid+lender promises are now mechanically complete: schema → solver physics → aging check → evidence-bearing gates. Remaining: W7-5 (the customer bill headline).
- **W7-3: end-of-tenor deliverability is COMPUTED, never asserted — and M4's dormant rejection criterion finally fires — Karim (`agent/sizing-search`, /loop iteration 7).** New `gridq.sizing.deliverability`: the closed-form check `capability(N) = power × derate(duration) × (1−fade/100)^N ≥ committed_power_kw` (plus the energy-side hold `energy × retention ≥ held_energy_kwh`) for every tenored `AvailabilityCommitment` riding the constraint seam — pure arithmetic, zero extra LP solves; CM commitments use the de-rating table with D28's conservative flooring (single-sourced via `value_for_duration`; call-time import breaks the sizing↔optimisation module cycle), other streams are plain kW. Verdicts are three-state honest: `None` = data-ask (no tenored commitment / unusable fade — never a fabricated pass), `False` = a known failure always wins, `True` = arithmetic-on-library-values with the fade's assumption status quoted in the notes (a placeholder-fade verdict is labelled **arithmetic, not evidence** — provenance arrives with W7-4; bankability untouched). Wired into `_evaluate_spec` (replacing the permanent `_EOL_DATA_ASK` stub) + additive `extra_constraints` injection points on `enumerate_sizing` and `ContinuousSizingEvaluator` (default `None` — goldens safe). **The boundary doc's own CM promise ("deliverability verified through the final contract year at degraded capability") and M4's "at least one grid point rejected" criterion are now live code**, test-proven: a 50 kW candidate that PASSES its DSCR covenant is rejected because its degraded year-15 capability (≈22 kW) cannot honour a 40 kW commitment, while the 100 kW candidate is selected. 7 new tests. **Whole tree green: 622 tests; `ruff`/`ruff format`/`mypy`(113)/review_gates clean.**
- **W7-2: the dead constraint seam is ALIVE — `problem.constraints` compiles into the solver via a kind→builder registry; firming is now physics in dispatch — Karim (`agent/sizing-search`, /loop iteration 6).** `MilpDispatchSolver` gains `_compile_commitments` + an explicit kind→treatment registry (`_STRUCTURAL_KINDS` connection-limit/throughput-cap — enforced by the existing bound construction; `_COMPILED_KINDS` availability_window/held_state_of_energy — built here from the typed C026 payload; `_DEFERRED_KINDS` deliverability/export_limit — the sizing lane's W7-3 check / moot until the RTM gate; unknown kinds warn loudly, nothing silently dropped). Compiled constraints: **reserved net-injection headroom** `discharge_t − charge_t ≤ power − C_w` and **held state-of-energy** `soe_t ≥ held_kwh` over the commitment's deterministic calendar mask (`_commitment_mask`: months/days/time vs the metered timestamps — nothing interpolated). **The golden-preserving property is designed-in and test-proven:** a payload-less declared descriptor (today's universal state) produces a byte-identical schedule — every existing quote id survives (615-test bar green, incl. the runtime-determinism goldens). Infeasible commitments (hold > usable energy) compile anyway and warn — the solve honestly returns INFEASIBLE, a contract is never silently relaxed. Post-solve: the dormant `deliverable_capacity_mw` (from reserved CM commitments only — never asserted from nameplate) and `tender_window_discharge_mwh` (discharge within LOCAL_FLEX windows) populate, and binding commitment constraints land on `constraints_binding` (`availability_reserve:<id>` / `held_soe:<id>`). Acceptance proven on the real solver (`test_optimisation_commitments.py`, 6 tests): the unconstrained solve full-discharges through the committed red-band window; the committed solve **provably holds headroom and SoE through it, at a measured cost to DUoS value** (the constraint binds economics, not just algebra). **Whole tree green: 615 tests; `ruff`/`ruff format`/`mypy`(112)/review_gates clean.**
- **W7-1 (Wave 7 opens — the firming substrate's data shape): `AvailabilityCommitment` schema + the typed constraint payload — Karim (`agent/sizing-search`, /loop iteration 5; seam design flagged as C026 for Olly BEFORE building).** New `gridq.schemas.commitment` — `AvailabilityWindow` (market-agnostic recurring calendar: optional months/days-of-week filters + a validated same-day `[start, end)` span; overnight = two windows; canonicalised deterministic ordering) and `AvailabilityCommitment` (id, stream, `committed_power_kw`, optional `held_energy_kwh` — **never defaulted**, no invented energy hold — windows, optional `tenor_years` for the W7-3 end-of-tenor deliverability check, optional counterparty/`reference` so a commitment without provenance stays visibly bare). `DispatchConstraintDescriptor` gains an **optional typed `parameters` payload** (default `None` — every pre-existing descriptor constructs unchanged, test-proven): the firming kinds (`availability_window`/`held_state_of_energy`) become **compilable, not just nameable** — the exact dead-seam gap the north-star deep review flagged. Additive throughout: `SiteModel`/`OptimisationResult` untouched, `SCHEMA_VERSION` unchanged, quote hashes undisturbed (determinism goldens green). 5 new tests (`test_schemas_commitment.py`). **Whole tree green: 609 tests; `ruff`/`ruff format`/`mypy`(112)/review_gates clean.** Next: W7-2 — the solver kind→builder table that compiles these commitments into reserved net-injection headroom + held-SoE constraints.
- **W6-7 complete → WAVE 6 COMPLETE (issue 016 stages 1–9 all built & green): the PSO baseline + the stage-9 bake-off + the production-method decision (D29) — Karim (`agent/sizing-search`, /loop iteration 4).** New `gridq.sizing.search.pso_search`: a seeded, deterministic particle-swarm baseline (LHS-initialised in-band swarm, velocity clamps, positions clipped to the band-feasible window every generation, same exact evaluator batched through the parallel harness, budget + patience, convergence curve). Fixed a real rounding-order defect it exposed (in `pso_search._clip` AND latent in `SizingDomain.latin_hypercube`): rounding power after deriving the energy window could leave a pair a hair past the 8 h band edge — power is now rounded first and the window derived from the rounded value. **Bake-off (demo site, real MILP):** dense 12×12 reference 67 evals / 23.48%; adaptive +0.084% vs ref (within 0.5% at 41 evals); PSO +0.088% (within 0.5% at 24 evals) — both ≈zero information loss, the issue-016 certification target. **D29 recorded:** dense grid = the persisted reference surface, adaptive zoom = the production optimum refiner, PSO = baseline only; PSO's eval-efficiency win discounted (measured on the C025-degenerate corner-pinned surface); **the engine-default swap from the 3×3 stays gated on C025 + a post-C025 re-run.** 2 new tests. **Whole tree green: 604 tests; `ruff`/`ruff format`/`mypy`(111)/review_gates clean.** Wave 6 of the north star is done; the lane moves to Wave 7 (firming substrate) — W7-1 is the deliberate schema event and will be flagged in OPEN_COMMENTS before building.

## 2026-06-25

- **Frontend redesign, round-6: battery-utilisation donut + Theoretical Upside Project — Olly.** Frontend only; engine untouched. (a) **Battery utilisation on every quote** (`view.utilisation_island` + `charts.js::utilisation` + `_utilisation.html`): an animated Plotly donut splitting the representative dispatch day into Charging / Discharging-at-peak / Discharging-off-peak / Idle, with a labelled percentage list. Counts come straight off the persisted dispatch record; reduced-motion safe. (b) **"Theoretical Upside Project"** (`app/upside.py` + `_upside.html`): a persisted project (`build_upside_project`, the curated demo through the real DemoEngine, renamed for the Projects tab) that adds an illustrative route-to-market overlay. The dispatch, priced floor (£28k) and Capacity Market are the real engine output; the route-to-market streams (wholesale/P415, BM, ancillary, local flex) are shown as **illustrative** £/MW/yr ceilings times the engine-sized power, per-site (~£49k/yr) and at a 10 MW aggregated portfolio (~£3.97M/yr, the "contract many sites" view). They are loudly labelled illustrative and not bankable, never written into the QuoteRecord, and the engine is not touched — the gated streams stay £0 in the real value stack because their `GateContext` gates (D12) are the optimisation engine's domain. Bar: ruff/mypy clean (10 app files); `test_app_smoke` (7) green; verified the donut, the upside overlay (per-site + portfolio) and the Projects listing via Playwright. Left **uncommitted**.
- **Frontend redesign, round-5: battery-focused framing, demo through the wizard, clearer value stack — Olly.** Frontend only; engine and value functions untouched. (a) **Solar is an input, not sized**: the wizard's equipment stage is now "Battery system" (battery + inverter pickers) plus an optional "Existing solar PV (kWp)" input; the dashboard hero headline and tiles are battery-focused (no "solar sized" claim); the pinned pipeline copy de-emphasises solar. (b) **The demo walks the wizard**: `/demo` redirects to `/new-quote?demo=1`, which prefills the illustrative dataset (Demo Cold Store, NGED region, RAG tariff, 500 kVA) and posts `demo=1`; `/api/run` runs the curated demo (DemoEngine + illustrative debt) through the same streaming job, so the demo shows the run animation and lands on the rich result instead of a separate synchronous path. Editing any field turns it back into a real run. (c) **Value stack reads clearly**: a top-line summary (priced floor £/yr in green + conditional upside) makes the earned revenue unmissable, and gated streams show a "Locked" vs "Conditional" badge with what unlocks each, plus an honesty note (we never price speculative revenue into the floor). The £0 gated streams (wholesale, BM, ancillary, local-flex) are by-design, not a bug: they price only from a confirmed gated-upside value once a route-to-market partner, export consents and portfolio aggregation exist; nothing is fabricated. Bar: ruff/mypy clean (9 app files); `test_app_smoke` (7) green (the demo=1 streaming run reproduces the deterministic demo quote id); verified the prefilled demo wizard, the value-stack summary and the battery framing via Playwright. Left **uncommitted**.
- **Frontend redesign, round-4: clean demo, owned Review, system diagram, scroll-driven dashboard — Olly.** Frontend only; engine untouched. (a) **Demo runs clean**: `examples/demo_site/sitemodel.json` g99/g100 set to `not_required` (non-exporting site), clearing the last data-ask, so the demo Review shows zero blockers and zero conditions. (b) **Review owners are real people**: `redteam.py` maps every finding to the System Brief owner (Olly = engine, Olly = ingestion/assumptions/modelling, Zara = growth/customer, Sim = finance/credit, Karim = operations/partnerships/legal), and splits placeholder assumptions by owner (engine params to Olly, cost/credit to Sim, library to Olly). (c) **System diagram** on the results page (`_system_diagram.html`): a single-line schematic (grid, site load, inverter/PCS, battery, optional solar) with animated cables, labelled from the run's sized values, inline-SVG icons (no emoji-font dependency). (d) **Scroll-driven dashboard** (`landing.html` + `static/dashboard.js`): an animated gradient hero with count-up metric tiles, a pinned scrollytelling pipeline (Input, Processing, Output stages advance as you scroll), and scroll-reveal sections; IntersectionObserver based, `prefers-reduced-motion` respected, self-contained. (e) **Static cache-busting**: assets are versioned by newest mtime (`static_v`) so edited CSS/JS is refetched after a deploy, not served stale. Bar: ruff/mypy clean (9 app files); `test_app_smoke` (7) green (landing keeps the GridQ / real / demo contract); verified across the hero, the pinned pipeline (stages 01-03), the system diagram and the clean demo Review via Playwright. Left **uncommitted**.
- **Frontend redesign, round-3: make manual entry actually solve + a red-team Review page — Olly.** Frontend/ingestion only; the MILP optimiser and value functions are untouched. (a) **Synthetic load profiling** (`app/synthetic.py`): a manual entry (annual usage + business type + optional peak) now generates a deterministic 8,760-hour profile (business-type reference curve, weekday/weekend + seasonal variation, energy-exact, mean-preserving peak reshape) written as a half-hourly CSV. `POST /api/run` attaches it so the run actually solves (graded `T1_PARTIAL_HALF_HOURLY`, never the validated `T2`) instead of returning insufficient data; a SYNTHETIC warning keeps it honest and not bankable. The wizard flags the meter-file upload as "Most accurate" and tells manual users it is modelled. (b) **Reference data fills likely-known blanks** (`_apply_reference_data`): standard time-of-use import rates per tariff type (red/amber/green, day/night, peak/off-peak, so arbitrage actually prices) and ASC defaulted to MIC, disclosed via a reference-rates warning. A manual Tesco-style run now prices a real floor (tariff arbitrage circa £36k/yr) plus gated upside. (c) **No insufficient-data results surfaced**: the run job fails with guidance ("enter annual usage or upload a meter file") instead of saving an empty quote; the projects list already hides unsolved runs. (d) **Review page** (sidebar) + `app/redteam.py`: a deterministic red-team check of a run that compiles every ranked data-ask and warning into ordered findings (blockers first), each with a severity badge, the problem, a bullet to-do list, and the owner (Customer / Installer / Customer with the DNO / Neura underwriting). No LLM, no em-dashes. (e) **Clearer value stack**: "where the revenue comes from" intro, a plain-English description of each stream's mechanism, and a pointer to the 25-year cashflow for how it plays out. Bar: ruff/mypy clean; `test_app_smoke` (7) green; verified end-to-end via Playwright + the streaming API (manual run solves to a populated surface + priced value stack; Review page lists 11 owned issues). Left **uncommitted**.
- **Frontend redesign, round-2 refinements — Olly.** Frontend only; engine untouched. (a) **Streaming run experience:** `POST /api/run` now starts the pipeline as a background thread and returns a `job_id`; the client polls `GET /api/run/{job_id}` and only completes/navigates when the run truly reports done (shared `static/run_progress.js` overlay, used by the wizard and the improve panel). The browser is never blocked. (b) **Projects list shows only completed runs** (`_is_complete`: a real dispatch or at least one priceable/returning candidate, so T0 insufficient-data runs are hidden, not just non-empty grids). (c) **Animations:** page fade-up, horizontal **swipe** between wizard stages, fluid hover lifts and smooth progress bars (`prefers-reduced-motion` respected). (d) **Run-site-data unified with the wizard:** `GET /real` 307-redirects to `/new-quote` (the single guided intake); `POST /run/real` kept for the smoke test. (e) **Real map:** the SVG placeholder is replaced by **Leaflet + OpenStreetMap** (vendored `static/vendor/leaflet/`, OSM tiles) with zoom and click-to-select; a click sets coordinates, snaps the DNO to the nearest known region, and reverse-geocodes the address/postcode via Nominatim; the canned UK type-ahead remains for quick city entry. (f) **Improve this quote:** a results-page panel (`_improve.html`) carries the stored site forward and uploads a half-hourly meter file, then recalculates in place through the streaming run (a new quote id), taking a T0 run to a full T2 solve, instead of re-entering everything. Bar: ruff/mypy clean on touched files; `test_app_smoke` (7) green; verified end-to-end via Playwright (OSM map, wizard swipe nav, streaming overlay during a real solve, improve upload → recalculated quote). Note: external dependencies are introduced for the map only (OSM tiles + Nominatim), a deliberate departure from the offline posture, at the user's request. Left **uncommitted**.
- **Frontend redesign (Stages 1-4) to the gridq.ai (EASOS) reference — Olly.** Frontend only; the MILP optimiser is untouched. Benchmarked gridq.ai's Tesco demo into `docs/benchmark/gridq_ai_reference/` (19 shots + design tokens); plan in `docs/frontend_redesign_plan.md`; spec `docs/frontend_redesign_spec.md`. Built into `src/gridq/app`, deployed to the existing tunnel.
  - **Design system** (`static/app.css`): re-themed to the EASOS reference (slate `#f8fafc`, white flat 12px-radius cards with hairline borders, orange `#ff6b35` accent, sans headings) while keeping every existing CSS var + component class so the run screens restyle without breaking. Added a persistent left-sidebar shell (`base.html`: Dashboard / Projects / Equipment + New Quote, breadcrumb, run tabs); responsive (sidebar collapses to a top strip).
  - **New screens:** a visual-first Dashboard (`landing.html`), Projects list (`store.list_runs()`), Equipment catalogue (`app/equipment.py` — 20 panels / 5 batteries / 10 inverters, correct per-type units; `equipment.html` with a tab toggle and an upload-spec-sheet entry).
  - **5-stage New Quote wizard** (`new_quote.html` + `static/wizard.js`, GET `/new-quote`): Site (canned UK address type-ahead + inline-SVG drop-a-pin map that snaps to known lat/lon + DNO, tariff card), Consumption (manual / CSV / bill scan + smart-meter "soon"), Load profile (live modelled curve via vendored Plotly), Equipment (Auto-select pickers), Review (summary + curve). Honest gating: Continue disabled per stage and Submit blocked with a "before you can run" list until inputs are valid. Step-by-step run experience overlay then `POST /api/run` (real `MilpEngine`) -> `/run/{id}`.
  - **3D sizing surface (our differentiator)** made a continuous bilinearly-resampled mesh shown beside a 2D heat-map of the same return field, with the optimum + covenant-feasible sizes marked and floored (no-viable-return) cells disclosed (`view._dense_surface`, `static/charts.js`).
  - **Bar:** `ruff`/`ruff format`/`mypy` clean on touched files; `test_app_smoke` (7) + the surface island/charts-wiring checks green; smoke-test contracts preserved (`/`->"GridQ"+`/real`+`/demo`, inspect provenance + "Sharpen"/"NOT BANKABLE", 400/404 copy). Verified desktop 1440 + mobile 390 via Playwright. No em-dashes in new UI copy. Left **uncommitted** for review.
- **W5-5 — Final acceptance gate (the "Karim-ready" gate) + live public deploy — Olly (`agent/integration`).** Verdict: **ready to show Karim = YES**, with two flagged *exposure* judgment calls (not correctness). Full report: [`wave5_acceptance_report.md`](wave5_acceptance_report.md).
  - **Acceptance — all six checks pass.** (1) *Bankable* (acceptance test 1): every output carries P50 **and** P90 + a DSCR machinery, never a point estimate; DSCR is `unavailable` (not guessed) without sourced debt; `needs_review`/placeholder forces `not_bankable` via real gate logic. (2) *Fast* (acceptance test 2): telemetry captures time-to-quote + `human_minutes` vs the manual benchmark in one `TelemetryEvent` stream from quote 1; the realised-vs-underwritten loop is structurally present (`quote_mode` lifecycle + `linked_quote_id`) with no operating data yet (honest, pre-pilot). (3) *Determinism*: verified at runtime — identical Demo input → identical `quote_id` (`quote_run_714ab382f4e0ac94`) locally and over the public URL; provenance stamps input/code/schema/assumption hashes + seed + output_hash. (4) *Security/privacy*: no secrets in the repo, only synthetic `examples/demo_site` ships, `examples/real/` (incl. the new prefill) gitignored, submissions to `$GRIDQ_RUN_STORE` with a delete path. (5) *Honesty*: no LLM/network on the pricing path, `review_gates` GREEN, firm-vs-gated correct, UI labels honest. (6) *Green bar*: **568 tests pass; `ruff`/`ruff format`/`mypy`(101) clean** (baseline + after these changes).
  - **Live public deploy.** The Wave-5 app is hosted on the VPS as two systemd services (`gridq.service` uvicorn on `0.0.0.0:80` with determinism/run-store env pins; `gridq-tunnel.service` cloudflared), both `Restart=always` + enabled at boot. Public URL verified end-to-end (`/healthz` 200, demo run + 3D surface) over the internet. (Inbound to the VPS is blocked by an external Hetzner Cloud Firewall, so the public path is an outbound cloudflared tunnel; a branded `neura` URL needs either the firewall opened → `neurauk.<ip>.sslip.io`, or a Cloudflare token for a named tunnel on `neura.energy`.)
  - **Demo dry-runs (end-to-end through the UI incl. the 3D surface).** Demo run (synthetic, real engine) renders the populated 3D sizing surface + dispatch + P50/P90 + DSCR-vs-covenant + firm-vs-gated + provenance (screenshot captured); an uploaded 12-month HH CSV through the **real-site** form solved the real MILP to `optimal` (9-candidate sizing grid + dispatch profile + surface), honestly `not_bankable`.
  - **New (additive, tested): upload-first real-data entry + a real-prospect prefill.** `app/demo.py::load_haywood_oaks_prefill()` + `GET /real/haywood-oaks` pre-fill the real-mode intake from a **gitignored** `examples/real/haywood_oaks_prefill.json` (real transcribed invoice facts for Robert Thomas Farms — no fabricated load series; unknown fields blank as data-asks), with the HH upload front-and-centre; a landing card appears only when the example is installed (clean public build otherwise). Tests in `tests/test_app_examples.py` (hermetic). No frozen-contract or engine change; arithmetic-free-UI invariant intact. Left **uncommitted** for review.

## 2026-06-24

- **W5-3 — Public deployment: reproducible host-agnostic build + config + go-live runbook (D26) — Olly (`agent/deploy`).** Makes the Wave-5 app publicly hostable, prepared so the live step is a single action **Olly** performs (the agent created no accounts, accepted no terms, registered no domain, pushed nothing live — those are `TODO[Olly]` in [`deployment.md`](deployment.md)).
  - **Reproducible build.** New [`Dockerfile`](../Dockerfile) (`python:3.12-slim`): installs a **fully-pinned** runtime+`[app]` closure ([`requirements.txt`](../requirements.txt), 62 pkgs) **wheels-only** (`--only-binary=:all:` — **no compiler/apt layer**, `highspy` statically bundles HiGHS), then installs gridq **editable in its source-tree layout** (required — `assumptions/` + `examples/demo_site` resolve via `Path(__file__).parents[3]`; the demo dir has no env override). The lock was generated from the green env and **verified** by installing it into a clean venv and running the app smoke + **demo-MILP** suites against it (all green), so the deployed image reproduces the tested behaviour. Includes the linopy "iceberg" (`import linopy` eagerly needs `requests`/`google-cloud-storage`/`polars-runtime-32`/…); excludes the dev/agent toolchain.
  - **Determinism in the container.** The Dockerfile pins `TZ=UTC`, `LANG/LC_ALL=C.UTF-8` and single-threaded numerics (`OMP/OPENBLAS/MKL/NUMEXPR_NUM_THREADS=1`, `POLARS_MAX_THREADS=1`) so the MILP (already `threads=1, seed=0` in code) stays byte-reproducible on any host CPU/locale (extends D18/D22).
  - **Additive app code (tested — `tests/test_app_deploy.py`, 7):** `GET /healthz`→`200 {"status":"ok"}` (cheap, unauthenticated host probe) and an **optional HTTP Basic gate dormant unless `GRIDQ_BASIC_AUTH="user:pass"` is set** (guards everything except `/healthz`+`/static`; `secrets.compare_digest`, no short-circuit timing oracle; no credential in the repo). Default open posture + all existing tests unchanged. Verified end-to-end against a **real uvicorn boot** of the exact container start command.
  - **Config + privacy.** [`.dockerignore`](../.dockerignore) keeps `runs/`/`data/`/`.env`/`.claude/`/`graphify-out/`/tests out of the image; only the labelled synthetic `examples/demo_site` ships. Run store at `$GRIDQ_RUN_STORE` (image default `/data/runs`), **ephemeral by default** (privacy-aligned; volume documented for durability). [`Procfile`](../Procfile) (buildpack hosts), [`fly.toml`](../fly.toml) (recommended primary host), [`deploy/.env.example`](../deploy/.env.example).
  - **Packaging fix.** `pyproject.toml` was missing the `[project.optional-dependencies]` table header (the `app`/`dev` extras were bare `[project]` keys — invalid PEP 621, so a clean `pip install` failed; masked only because the dev venv predates strict tooling). Header added, **no dependency change** (flagged **C024**).
  - Docs: new [`deployment.md`](deployment.md) (one-command local run · reproducible-build rationale · host choice · Fly.io go-live runbook with `TODO[Olly]` markers · data-handling/retention/deletion · verification). Skill proposal at `skills/proposed_updates/deploy.md`. Left **uncommitted** for review.

- **W5-2 — Visualisation layer: vendored-Plotly interactive charts + a persisted representative dispatch day (D25) — Olly (`agent/viz`).** The visual analytics that make the solve legible, on top of W5-1 (D24).
  - **Renderer — vendored Plotly, superseding D24's WebGL (wave5_scope §4).** The Inspect charts re-render on a **vendored** `static/plotly.min.js` (pinned **v2.35.2**, no CDN — offline / CSP-safe, `/static` is auth-exempt): the **3D sizing surface** (`surface` with a P90-DSCR-feasibility colour band + `scatter3d` candidate markers + a distinct accent **chosen-optimum** marker; orbit/zoom), the **half-hourly dispatch** (charge/discharge bars + state-of-energy line + DUoS-red-band shading), **P50/P90 cashflows** (grouped bars), **P90 DSCR vs covenant** (line + covenant rule), and the **value stack** (firm-vs-gated stacked bars). `wave5_scope §4` sanctions "a vendored Plotly bundle **or** a small WebGL/canvas surface" as equivalent; the brief asked for Plotly. `static/charts.js` is now a thin Plotly config builder; the charts read the **same JSON islands** (raw record values + pre-formatted label strings — Plotly positions/draws but formats no figure); the accessible fallback tables remain and the template arithmetic-free AST invariant is untouched.
  - **Persisted representative dispatch day (D25(B); mirrors D23).** New serializable `gridq.schemas.dispatch_profile` (`DispatchProfile`/`DispatchProfilePoint`), projected by `HalfHourlyDispatchSchedule.representative_day_profile()` (**one real solved day** — the highest-total-discharge day, earliest on ties — never an average or invented shape), exposed via a duck-typed `MilpEngine.last_dispatch_profile()` / `DemoEngine` hook (the solver retains its last schedule; the engine reads it after the chosen-spec re-dispatch) and persisted by `generate_quote` onto **`QuoteRecord.dispatch_profile`** (+ `QuoteRunResult`). **Additive, Optional, hash-excluded** — frozen `OptimisationResult` untouched (D13); **`SCHEMA_VERSION` unchanged**; `None` on a run with no metered HH series and for the hook-less `BaselineEngine`. So the dispatch chart reads real persisted run data, not a browser recompute.
  - **View + templates:** `app/view.py` gains `dispatch_island` + `value_stack_island` and the surface's Plotly `z_matrix`/`feasible_matrix`; new `_dispatch.html`, a value-stack chart in `_value_streams.html`, `<canvas>`→`<div>` chart mounts, the vendored Plotly `<script>` + two new islands in `inspect.html`. Real-mode runs with no dispatch show a **labelled seam**, never an invented series.
  - **Tests:** `test_dispatch_profile.py` (9), `test_app_view_islands.py` (6), `test_app_charts_wiring.py` (5 — vendored-Plotly/no-CDN, all chart divs + islands present, demo islands populated, deterministic page, honest no-dispatch seam). Existing app/template/sizing tests stay green; the template arithmetic-free AST guard covers the new templates. Skill proposal added at `skills/proposed_updates/viz.md`. Left **uncommitted** for review.

- **W5-1 — World-class end-to-end UI: two run modes, the 3D sizing surface, honest status throughout (D24) — Olly (`agent/frontend`).** The Wave-5 product surface, built on W5-0 (D23) and the real MILP (D22). FastAPI + Jinja2, server-rendered, self-contained (no JS toolchain, no CDN).
  - **Two explicit run modes, no default (wave5_scope §2).** A landing **mode-chooser**: **Real site** (primary) runs the live path (`generate_quote(engine=MilpEngine())`), grades the tier and surfaces the **ranked data-asks** (reusing `gridq.ingestion`), degrading honestly to `insufficient_data` when inputs are missing — never an invented value; an optional half-hourly CSV upload upgrades the tier and lets the MILP dispatch. **Demo** (opt-in) runs the **real** engine on a clearly-labelled synthetic dataset (below). Neither is auto-selected.
  - **Navigable end-to-end flow (wave5_scope §3):** landing · real form · demo · **Inspect** (provenance · sized system · 3D surface · firm-vs-gated value split · finance) · system spec · **Memo + Proposal** (the `gridq.outputs` report served byte-identical) · telemetry. A persistent honest-status strip carries the **indicative-vs-underwritten** badge, **firm (priced floor) vs gated upside** figures, the bankable state, and the data tier on every run screen.
  - **3D sizing surface (wave5_scope §4):** a **self-contained WebGL** renderer (`static/charts.js`, no Plotly/CDN/build) over the persisted `QuoteRecord.sizing_grid` — power × duration × levered return, the **P90-DSCR-feasible region highlighted**, the **chosen optimum marked**; 2D P50/P90 cashflow + DSCR canvas charts; an accessible server-rendered fallback table always present. Charts read a JSON island of **raw record values** (geometry) + **pre-formatted strings** (text) — the client positions/draws but computes no figure.
  - **Demo dataset — real engine, labelled synthetic inputs (wave5_scope §5):** new `examples/demo_site/` (a deterministic synthetic 12-month HH CSV + a `SiteModel` + a labelling README). A standalone `gridq.app.demo.DemoEngine` runs the **real** MILP dispatch + public sizer with **illustrative (not underwritten) debt terms** injected, so the surface populates a levered-return axis, a feasible region and an optimum (124 kW × 4 h, DSCR 1.45, project IRR ≈21%) — yet the run stays honestly **`not_bankable`** and is badged *illustrative* throughout. Olly's `MilpEngine` is **unmodified**; the only additive core change is an optional `generate_quote(..., debt_terms=None)` passthrough to `run_financial_model` (default `None` ⇒ real-mode path unchanged).
  - **Arithmetic-free UI, enforced:** figures come only from `outputs.build_view_model` + `outputs.formatting` (Jinja `fmt_*` globals); new `tests/test_app_templates_no_arithmetic.py` AST-parses every template and forbids arithmetic/aggregation on displayed values (the template analogue of the outputs HTML AST test).
  - **Privacy + persistence (wave5_scope §5):** `gridq.app.store` persists each run (`quote`/`site`/`telemetry`) under the **gitignored** `$GRIDQ_RUN_STORE` (default `runs/`) keyed by the deterministic `quote_id`, with a `delete_run` path; real third-party data is never preloaded/committed — only the synthetic demo ships.
  - **Tests + bar:** `test_app_smoke.py` rewritten for the multi-screen flow; added `test_app_demo.py`, `test_app_real_mode_gating.py`, `test_app_templates_no_arithmetic.py`. Whole tree green: **537 tests pass; `ruff`/`ruff format`/`mypy`(100)/`review_gates` clean**; graph refreshed. Public hosting (Procfile/container + pinned solver deps) remains the separate wave5_scope §1 step. Left **uncommitted** for review.
- **W5-0 — Persist the full sizing evaluation grid for the 3D surface (D23) — Olly (`agent/sizing`).** The Wave-5 pre-req the harmony audit flagged: the sizer already **retained** the full power×duration grid (`SizingRun.candidates_evaluated`) but `MilpEngine.solve()` dropped it at the frozen `OptimisationResult` seam, so the 3D sizing surface couldn't plot it from persisted data. Now exposed **additively, without touching the frozen contract** (D13 still honoured):
  - **New `gridq.schemas.sizing`** — `SizingGrid` + `SizingGridPoint` (serializable `GridQModel`s) projected by a new **`SizingRun.sizing_grid()`**: every evaluated candidate's `power_kw`/`duration_hours`/`energy_kwh`/`levered_return_pct` (equity IRR) / `project_irr_pct` / `min_p90_dscr` / `passes_covenant` (the P90-DSCR feasibility flag) / `solver_status`, plus the chosen optimum and the run covenant. **No invented numbers** — scalars are copied verbatim off each `CandidateEvaluation`; an unavailable metric stays `None` (no debt ⇒ no levered return/DSCR), and the optimum is honestly `None` when no candidate meets the covenant (the placeholder case).
  - **`MilpEngine.last_sizing_run()`** — a duck-typed hook **mirroring `selected_dispatch_energy()`** (stash on `solve()`, reset at the top). `generate_quote` reads it via the same `getattr`/`callable` pattern and persists `sizing_grid` onto `QuoteRunResult` **and** the new **additive, Optional `QuoteRecord.sizing_grid`**.
  - **Backward-compatible / hash-safe:** the field is **excluded from the canonical hash input** (`assembly._canonical_run_input`), so `input_hash`/`quote_id` are unchanged and **`SCHEMA_VERSION` is not bumped**; existing records/fixtures (e.g. `examples/quote_record_example.json`) without it validate unchanged; an engine with no hook (`BaselineEngine`) simply leaves it `None`.
  - **Tests:** new `tests/test_sizing_grid.py` (12) — grid completeness vs `candidates_evaluated`, optimum-marked + feasible-region, honest-`None` on the placeholder, determinism, JSON round-trip, the engine hook, pipeline persistence, the no-hook backward-compat path, and a hash-unchanged proof. Whole tree green: **527 tests pass; `ruff`/`ruff format`/`mypy`(97)/`review_gates` clean**; graph refreshed. Flipped the `03_data_contracts.md` "Planned (Wave 5)" note to "Landed (D23)". Skill proposal appended to `skills/proposed_updates/sizing.md`. Left **uncommitted** for review (HEAD flips across lanes). The 3D surface renderer itself stays a later Wave-5 item.
- **W5-4 — README + operational docs rewrite (plain English, honest) — Olly (`agent/docs`).** Documentation only — **no `src/` behaviour changed**. The root `README.md` was badly stale (still said *"v1 scaffold stage… No application/pricing code exists yet"* though the whole Wave-4 engine is built and green), so a non-author had no accurate way to understand or operate the software.
  - **Full README rewrite** covering, in simple English: what GridQ is (one-engine-called-twice; the three artifacts; deterministic core / agentic perimeter); every pipeline step (site data → SiteModel T0/T1/T2 → value stack → dispatch MILP → sizing → finance → quote → memo/proposal/report); how to use it (CLI `run`/`render`/`info`, the demo UI, the two run modes, reading the five output files, indicative-vs-underwritten as lifecycle not bankability); where data goes (SiteModel JSON, HH meter CSV via `--hh`, Modo forecast as gated-upside, tier unlocks); how to change it (assumption library `gb_v0_1`/`gb_v1_0`, new-file-per-release rule, add-a-value-stream, frozen `SiteModel`/`OptimisationResult` contracts); and the installer workflow + an honest-limits section.
  - **New [`docs/operating_guide.md`](operating_guide.md)** — the deep operator runbook (install + solver stack, full CLI option table + exit codes, the UI + its Wave-5 successor, output-file reference with the *why* behind every "unavailable"/"pending" label, data placement + privacy, assumption-update + add-stream procedures, and a "do not present as bankable" checklist).
  - **Refreshed stale [`../examples/README.md`](../examples/README.md)** — it described a never-built `demo_site/`/`run_demo.*`; now lists the real fixtures and the `gridq run` flow, with the honest note that the example meter file isn't committed (so runs degrade to `insufficient_data` until a real `--hh` series is supplied) and that a packaged labelled demo dataset is Wave 5.
  - **Honesty bar held throughout:** documents only what's built; separates built vs Wave-5-planned (two-mode UI / visual flow / 3D surface); labels `indicative`/`gated`/`needs_review`/`not_bankable` honestly; records B001 (DSCR/debt unavailable) and B002 (wholesale/BM/ancillary gated). Verified against real runs (`gridq run examples/site_t0.json`, `…/site_t2.json` ± a synthetic `--hh` series, `python -m gridq.app`). Skill proposal filed at `skills/proposed_updates/2026-06-24-docs.md`. Left **uncommitted** for review.
- **W4-6 — Wave 4 harmony audit & Wave 5 readiness gate: GO — Olly (`agent/integration`).** Read-only audit (8 dimensions + first-hand verification of the go/no-go-critical claims) confirming the real engine is coherent and the system is genuinely ready for Wave 5, with two documentation-only fixes. Reports: [`docs/wave4_harmony_report.md`](wave4_harmony_report.md), [`docs/wave5_scope.md`](wave5_scope.md).
  - **Verdict GO.** The MILP (`gridq.optimisation.MilpEngine`) is the `gridq run` default (D22), deterministic, populates `DispatchEnergy` via `selected_dispatch_energy()`, and passes the W3-4 conformance suite; `Baseline`/`SimpleSolver` stay loud-labelled fallbacks. SSOT/enums/provenance intact; priced floor still `{FIRM, CONTRACTED}` with P415/BM/ancillary gated (B002) and credit a stub (B001); **no `market=="GB"` core branch**; `gb_v1_0` is **0 confirmed / 10 needs_review / 15 placeholder** with a finance bankable-gate (non-`CONFIRMED` ⇒ warning + `bankable=False`), so **nothing unsourced is presented as bankable** and every output stays `not_bankable`. Whole tree green: **515 tests pass; `ruff`/`ruff format`/`mypy`(94) clean** (full-year 17,472-HH MILP solve ≈2.6 s).
  - **Real-data E2E confirmed.** `gridq run` on a real SiteModel + real HH meter CSV + real Modo forecast → MILP → all **three artifacts** (`memo.md` underwriting pack, `proposal.md` customer proposal, `report.html`), firm/contracted floor separated from gated upside, DSCR/bill/counterparty labelled-unavailable not guessed, deterministic `quote_id`/`input_hash`. Caveat → Wave 5: no committed real dataset (privacy: `data/` gitignored) and no packaged labelled demo dataset (the `examples/README.md` is stale).
  - **Tiny doc fixes (no logic touched):** added `FinancialCase.equity_irr` to [`docs/03_data_contracts.md`](03_data_contracts.md) §4 (the one real drift — additive optional field from W4-2/D21; **closes the documentation side of C022**), and a "Planned (Wave 5, additive)" note for the sizing evaluation-grid exposure.
  - **Wave 5 pre-req logged (not a blocker):** the sizer already **retains** the full evaluated grid (`SizingRun.candidates_evaluated` — power×duration×levered-return×P90-DSCR×feasibility), but `MilpEngine.solve()` drops it at the frozen `OptimisationResult` seam. Wave 5 must expose it additively (mirror `selected_dispatch_energy()`, or an additive `sizing_grid` field — recorded decision + `SCHEMA_VERSION` note) so the 3D sizing surface is plottable from persisted data. Captured in `wave5_scope.md` §4. Coordination docs updated (TEAM_SYNC / HANDOFF_LOG / BLOCKERS / project_status). Skill proposal appended to `skills/proposed_updates/integration.md`. Left **uncommitted** for review.
- **W4-1 — Real half-hourly dispatch MILP (linopy/HiGHS) is the default engine — Olly.** Replaced the placeholder dispatch with a deterministic half-hourly battery MILP in a new [`gridq.optimisation`](../src/gridq/optimisation/) package (decision **D22**); it drops in behind the existing `DispatchSolver`/`OptimisationEngine` contracts and passes the W3-4 conformance suite with **zero** downstream change.
  - **`MilpDispatchSolver`** (linopy/HiGHS): SoE dynamics + round-trip efficiency (`charge = discharge ÷ ac_round_trip`, finance's convention), charge ≤ `min(MIC,ASC) − site_load`, behind-the-meter discharge ≤ load, warranty throughput a **hard** annual cap (per-duration, conservative flooring). **Objective = the site's own time-of-use bill reduction** (`import_rates` energy + `duos_*_rate`, placed in time by a new indicative GB `tariff_calendar` — *content, never a price*; unrecognised tariff labels degrade to no-arbitrage, never invented). No invented prices; the `£/MW/yr` proxies stay a labelled fallback.
  - **Per-stream value computed in the dispatch engine** ([`valuation.py`](../src/gridq/optimisation/valuation.py)) from the solved schedule; the value stack is consulted only for gating/firmness/constraints — **no value-stack file changed**. Attribution is non-double-counting (energy→tariff arbitrage, DUoS→red-band avoidance, MIC/ASC→£0 as it is realised through the connection-bounded arbitrage); CM/local-flex stay conditional upside.
  - **`MilpEngine`** composes the W4-2 `enumerate_sizing` loop with the MILP ("one engine, called twice"), is the **CLI `run` default** and is registered in the conformance `ENGINES`; `BaselineEngine` stays the `generate_quote` signature default (lowest blast radius). It exposes `selected_dispatch_energy()`, threaded into finance by a new additive optional `generate_quote(dispatch_energy=…)`, so **levies-on-charge + price-to-target now compute on the quote path** — without promoting fields onto the frozen `OptimisationResult` (D13 honoured). `quotes/assembly.py`'s not-bankable note was made engine-agnostic (it wrongly said "baseline engine — no revenues priced").
  - **Determinism:** HiGHS single-threaded, fixed seed, deterministic presolve, **IPM + crossover** (the annual throughput cap makes dual simplex ~15 s/full year; IPM reaches the identical optimum in <1 s with a clean vertex schedule). Added `linopy>=0.5,<0.6`, `highspy>=1.7` and pinned **`xarray>=2024.2,<2025`** (linopy breaks on 2025+/2026 xarray) — part of the reproduction contract.
  - **Tests:** 4 new modules (~30 tests) — physics invariants, determinism, the W3-4 conformance bar for `MilpEngine`, the dispatch-energy seam (real volumes → levies + price-to-target), the tariff calendar, and a full-year benchmark (**≈2.5 s/solve**, 17 472 half-hours; recorded by `test_benchmark_full_year_dispatch_solve`). Full suite green; touched files clean (ruff/format/mypy/review_gates). End-to-end `gridq run examples/site_t2.json --hh <year.csv>` → `production`/`optimal`, real arbitrage + red-band floor value, both connection + throughput caps binding, **still `not_bankable` on `gb_v0_1`**. Built on the shared tree's uncommitted W4-2 sizing (D21) + credit (D18) work.
- **W4-2 — Real sizing optimiser: levered return + the "one engine, called twice" consistency seam — Olly.** Made the M4 sizing outer loop genuinely optimise under the bankable constraint (no real MILP required; W4-1 still drops in behind the same `DispatchSolver`). Three parts (decision **D21**):
  - **Levered (equity) return is real.** New `finance.returns.equity_irr(...)` derives the equity IRR from the equity cashflow series `[-(capex − debt principal), then P50 FCF − debt service]`, reusing `project_irr` for the root-find (inheriting its one-sign-change gate). Computed on the **P50** base case; the **P90** case still governs the DSCR covenant (standard project-finance split). Wired through a new **additive** `FinancialCase.equity_irr` (optional, default `None`; flagged to Olly in **C022**) into `CandidateEvaluation.levered_return_pct`, so `SizingObjective` now maximises a real levered return. Stays `None` — never guessed — with no debt, an empty schedule, a non-positive equity outlay, or a non-conventional series.
  - **One engine, called twice (consistency contract #1).** Extracted the per-candidate dispatch+finance body into one `sizer._evaluate_spec(...)` primitive shared by `enumerate_sizing` and a new standalone `sizing.underwrite_spec(...)`, which reconstructs its call path **independently** from the raw `(site, assumptions, spec)` inputs. `sizing.financial_fingerprint(...)` is the timestamp-free projection a consistency test compares (load-bearing numbers + `run_context.input_hash`, never `created_at`/object identity) — so sizing and underwriting cannot drift, and the optimiser cannot select a spec underwriting would reject (contract #2).
  - **End-of-life deliverability (honest data-ask).** `SizingObjective.feasible` now rejects an explicit `end_of_life_deliverable is False`; `None` (the placeholder/v1 case — no late-life signal) is an unassessed data-ask, never a fabricated pass (per D12). Degradation already flows through finance (revenue fade → P90 DSCR).
  - Tests: equity-IRR unit tests, schema additive-field tests, an orchestrator equity test, and the four sizing behaviours (bankable-by-construction / covenant-violating-never-selected / sizing == underwrite re-price / determinism) + a gate-release test. Full suite green; touched files clean (ruff/format/mypy/review_gates). **Still not bankable on `gb_v0_1`** (placeholder assumptions) by construction. Built on the `agent/counterparty` lane's uncommitted credit→rate/tenor work (D18) in the shared tree.
- **W4-3 — `gb_v1_0` sourced assumption release (provenance pass) — Olly.** Created [`assumptions/gb_v1_0.yaml`](../assumptions/gb_v1_0.yaml), the sourced successor to `gb_v0_1`, carrying real GB values **only where a citable source exists** and honest placeholders everywhere else — no number invented or guessed. `gb_v0_1` stays **frozen** for provenance (new file, never an in-place edit). Sourcing boundary agreed with Olly: in-repo `source_docs` + verified gov constants, with **every value carrying a display-ready citation in its `source` field** so an output/frontend can show where each number came from.
  - **Populated from a real in-repo citation** (the Neura business plan), promoted `placeholder → needs_review`: `battery_capex.installed_cost` (£500k/MW) and `financing.target_equity_irr_pct` (12%, the low/conservative end of the cited 12–13% band). Confidence kept `low` (indicative plan figures).
  - **Provenance correction:** `gb_v0_1` cited `round_trip_efficiency` (0.88) as `system_requirements (≈12% round-trip loss)`, but that document states **no** such figure — the ≈12% was not in the source. `gb_v1_0` re-labels it honestly as **NOT sourced** (industry-typical placeholder pending an OEM datasheet). The fix lives only in `gb_v1_0`; `gb_v0_1` is left untouched.
  - **Never invented:** all other entries carry their `gb_v0_1` value forward unchanged with a sharpened, honest `source` ("no citable source identified" / "(gated)" preserved). Gated streams (`wholesale_p415`/`balancing_mechanism`/`ancillary_services`) stay placeholder **zero**. Volatile market series (CM clearing/de-rating, DUoS, DC/DM/DR) were **not** web-scraped in — per SKILLS §6 they are *referenced, not stored* (named in the gap list instead). Mix: **0 confirmed, 10 needs_review, 15 placeholder.**
  - **Gap backlog:** new [`docs/assumptions_to_source.md`](assumptions_to_source.md) ranks every gap by impact on the bankable floor/DSCR, names the authoritative public source for each (NESO/EMR DB, Ofgem/DNO CDCM, VOA, OEM datasheets, Sim/portfolio finance), and cross-references C007/C008/C012/B001/B002.
  - **Tests:** `tests/test_assumptions.py` +12 (load + 23 sections; entry-count=25 locked; full provenance on every entry; no bare-`placeholder` source; the two in-repo-sourced values cite Neura + are `needs_review`; nothing `confirmed`; status mix 10/15/0 locked + reported; gated-zero invariant; RTE corrected in `gb_v1_0` + its citation left frozen in `gb_v0_1`; **loader rejects a value missing a provenance field with `ValidationError`** via `base_dir`, with a missing-file `AssumptionError` twin). **20 pass; ruff/format/mypy clean** on touched files.
  - Decision **D19** (why a sourced release that confirms nothing, reconciling D18's "version bump = confirm" stance). Raised **C020** (per-value `source` not yet threaded to outputs/frontend — Olly's traceability ask). Docs: `assumption_library.md` updated; skill proposal filed at `skills/proposed_updates/2026-06-24-assumptions-provenance.md`.
  - **Warnings for next person:** `gb_v1_0` is **not bankable** (0 confirmed) — do not present it as such. The `agent/counterparty` lane (D18) is adding `financing.credit_rate_tenor_policy` + the C008 debt keys to the library in parallel (uncommitted in its `gb_v0_1` working copy); `gb_v1_0` was built from the **committed** frozen `gb_v0_1` and does not absorb that — fold it in at the next release. Left uncommitted in the shared tree for review (HEAD flips across lanes).

## 2026-06-23

- **Counterparty credit → rate/tenor mapping (W4-5, M9 v1 deliverable) — Olly.** Built the **deterministic credit→rate/tenor mapping** that feeds finance — the precondition "W3-5" had never been built, so this lane builds it as its foundation (the spec calls the mapping itself the M9 v1 deliverable). New **`gridq.finance.credit`**: `map_credit_to_rate_tenor(credit_status, assumptions)` maps a coarse `CreditStatus` band (`strong`/`adequate`/`weak`) → `(rate_pct, tenor_years)` read from a **new versioned assumption** `financing.credit_rate_tenor_policy` (per-band placeholder table — no private constants); `unknown`/`not_assessed` → **unavailable + warning, never defaulted to a band or a guessed number**; **monotonicity enforced in code** (worse credit ⇒ rate non-decreasing, tenor non-increasing — a non-monotonic table refuses to map, satisfying the spec's "no glowing return at a rate the covenant doesn't justify"); rate rounded to 4dp / tenor to int for bit-reproducibility. `build_debt_terms_from_credit(...)` combines the mapped rate/tenor with gearing (Sim's `financing.debt_gearing_pct`, **absent in v1** — never invented here) → a full `DebtTerms`, or returns the `CreditRateTenor` alone when gearing is absent. **Wired into `run_financial_model`:** when the caller injects no `debt_terms`, terms are derived from `site.counterparty.credit_status`; the mapped rate/tenor reach `FinancialCase.debt_assumptions` **even with gearing absent** (so the mapping is demonstrable now), while DSCR stays honestly **unavailable** and the case stays **not-bankable** on placeholders. An injected `DebtTerms` always wins (the `rich_quote` fixture is untouched). **Counterparty intake/validation:** `ingestion.validators._check_counterparty` now warns on an unassessed `credit_status` and a malformed company number (format-only, no external lookup); `ingestion.missing_data` emits a precise `counterparty_credit_status` ranked ask when identity is present but the band is missing (distinct from the identity ask — no double-ask). **Creditsafe seam (B001):** the input boundary is `CreditStatus`; the future tool produces `score → CreditStatus` upstream and drops in with no downstream change — `Counterparty` is **not** widened with a raw score. **Resolves the rate/tenor half of C008** (gearing remains the one open piece, Sim's); decision **D18**; B001 workaround now realised. No frozen-contract or library-version change (additive placeholder; bump on confirmation). 21 new tests (`tests/test_finance_credit.py`, `tests/test_ingestion_counterparty.py`); my files green — `ruff check`/`ruff format --check`/`mypy`/`review_gates` clean, 114 finance/ingestion/quotes/outputs tests pass. Filed `skills/proposed_updates/counterparty.md`. (Tree-wide pytest/ruff/mypy show transient reds in other lanes' uncommitted WIP — `scenarios/forecast_prices.py`, `finance/returns.py` — not in this lane's chain.)
- **Wave 3 harmony audit — GO for Wave 4 — Olly.** Confirmed all Wave 3 lanes (value-stack/real value functions W3-1, dispatch+sizing W3-2, outputs/M8 W3-3, engine conformance + `optimisation_contracts` removal W3-4, finance/credit, telemetry, demo app W3-7a/b) cohere as one system before Wave 4, with no contract drift from the parallel agents. Six checks all **CLEAN**: (1) merge integrity — 15 packages present, no conflict markers, `optimisation_contracts` gone; (2) single source of truth — every shared model/enum defined once in `schemas/` (`ForecastReference` deliberately in `scenarios/`), no shadowing; (3) no frozen-contract mutation — `Firmness`/`SolverStatus`/`MarketStream`/`QuoteMode`/`DataTier`/`BankableStatus` members unchanged, provenance (`input_hash`/`assumption_version`/`code_version`) intact, additive seams correctly **deferred** not landed (D13 dispatch energy, D12 `GateContext`, D15 bill fields); (4) imports/circulars clean + engine↔dispatch↔sizing naming settled (D16/D17; real MILP → future `gridq.optimisation`); (5) green bar — **417 passed**, ruff/format/mypy (86 files)/review_gates all clean; (6) gating discipline — **no `market == "GB"` branch in core** (only docstrings stating the rule), priced floor still `{FIRM, CONTRACTED}` (one frozenset mirrored in finance/conformance/outputs, gated barred at 3 layers), no invented numbers (HTML arithmetic-free by AST test, prose categorical, placeholders labelled, review-gates active). **One tiny adapter fix applied** (index files only, no lane logic): `cli.py` `_MODULES` + `tests/test_imports.py` `SUBPACKAGES` brought in step with the merged package set (added `dispatch`/`sizing`/`telemetry`/`dev`/`app`; `app` left to `test_app_smoke.py` as an optional extra). **No blockers** (B001/B002 remain external). Hygiene flagged, not fixed: the app/conformance/docs delta is uncommitted atop `cd981df` (commit before Wave 4 so history matches); stale `egg-info` still names `optimisation_contracts` (regenerates on build); the 1 pytest warning is a third-party Starlette/httpx `TestClient` deprecation. Report: [`docs/wave3_harmony_report.md`](wave3_harmony_report.md). **Verdict: GO.** Skill proposal appended to `skills/proposed_updates/integration.md`.
- **`OptimisationResult` conformance suite + `optimisation_contracts/` tidy (W3-4; resolves C003) — Olly.** Built the executable contract bar that lets Olly's real MILP drop in behind the frozen platform↔engine seam with **no silent drift** (issue 014). New **`gridq.engine.conformance`** — a **pytest-free, import-safe** harness depending only on the frozen contracts (`gridq.schemas` + the engine interface), so Olly runs the *same* checks against `MilpEngine` from his own lane: `check_engine_conformance(engine, *, assumptions, sites=None) → ConformanceReport` (`.ok`/`.failures`/`.summary()`), `assert_conformant(...)` (raises `ConformanceError` listing **every** failure), `check_result_conformance(result, *, request=None)` (per-result invariants, usable on a lone result), and `canonical_conformance_sites()` (T2/T1/T0 fixtures built **from the schema**, covering the spec and insufficient-data paths). The checks are **contract-level invariants true of any honest engine** — re-validation round-trip, `schema_version` pin, request-echo, **run-provenance stamping** (`solver_metadata` must carry non-empty `model_version`/`assumption_version`/`code_version` — issue-014 AC + SKILLS), stream-registry membership + per-list no-duplicates, `gated_upside ⊆ GATED_UPSIDE_STREAMS`, gated-never-firm, **gated-excluded-from-the-priced-floor** (teeth: a `contracted` gated stream passes Pydantic but over-counts the floor — C014/W2-2), gated-upside-states-its-unlock-condition, baseline-never-bankable, status↔spec coherence (`infeasible`/`insufficient_data` ⇒ no spec), power/energy coherence, and **determinism** (excluding wall-clock + identity/storage fields `created_at`/`solve_seconds`/`result_id`/`dispatch_schedule_ref`). It deliberately asserts **no specific numbers** and **no half-hourly dispatch physics** (off-contract, behind `dispatch_schedule_ref`) — those would wrongly block a correct MILP (e.g. an end-of-life-sized system whose usable energy ≠ power×duration). New `tests/test_optimisation_contract_conformance.py`: the placeholder **`BaselineEngine` passes all 53 checks**; **five non-conforming stubs each fail exactly one** Pydantic-uncatchable check (non-determinism, gated-in-floor, missing-unlock, baseline-claims-bankable, dropped-echo); and the M5 seam is exercised end-to-end via `quotes.pipeline.generate_quote` (M8 outputs join when stable). **Resolves C003 (D16):** removed the empty `gridq.optimisation_contracts/` scaffold stub (chose *remove*, not *repurpose* — the real engine's home is `gridq.optimisation` per the parallel-dev plan + C005); `gridq.engine` is the single seam home and now also hosts the conformance suite. Adapter fixes only: `cli.py` `_MODULES` (now lists `engine` + `value_stack`), `tests/test_imports.py`. Confirmed `BASELINE_POWER_FRACTION` stays a labelled integration heuristic (never a library assumption; result always `not_bankable`). The invariant set was **hardened by a 4-lens adversarial review workflow + 2 diverse skeptic-verifiers per finding** (11 candidates): removed two over-assertions a correct real engine could trip (the energy=power×duration nameplate identity; comparing `result_id` for determinism) and added the provenance / status-coherence / schema-version checks it surfaced. **Green: 413 tests pass; `ruff check .`, `ruff format --check .`, `mypy` (86 files), and `gridq.dev.review_gates` all clean tree-wide.** No schema, `BaselineEngine`, finance, or other-lane behaviour changed. Docs: `gridq.engine` README conformance section, decision **D16**, OPEN_COMMENTS **C003 → resolved**; filed `skills/proposed_updates/integration.md`. Refreshed the `graphify` graph.
- **Demo web shell — the three artifacts from one run (W3-7b; issue 013) — Olly.** Built **`gridq.app`**, the thin FastAPI + Jinja2 demo shell decided in W3-7a (`docs/frontend_decision.md`). A site flows end-to-end in one view: pick a committed example site (or paste a `SiteModel` JSON) → **`gridq.quotes.generate_quote`** (deterministic, `BaselineEngine` mock, honest `not_bankable`) → **`gridq.outputs.generate_for_run`** → the combined HTML report (System specification · underwriting pack · customer proposal) served **unchanged** inside an isolating `<iframe srcdoc>`. **Read-only over deterministic outputs — the shell computes nothing:** no pricing, no aggregation, no number→string conversion lives in any route or template (those stay in `finance`/`quotes` and `outputs.formatting`, whose HTML renderer is AST-asserted arithmetic-free). The contracted-vs-merchant (firm-vs-conditional) split, the warnings/data-asks and the NOT-BANKABLE banner are all surfaced from the stamped `QuoteRecord`; provenance (input hash, assumption + code version, data tier) is shown verbatim. Bad input degrades to a 400 page with the actionable ingestion error, never a 500. The web stack is an **optional `app` extra** (`pip install -e ".[app]"`; run `python -m gridq.app`) — the core engine, CLI and the rest of the suite import without it. 6 new tests (`tests/test_app_smoke.py`): the app boots; the run presents all three artifacts; **the served report equals a freshly generated `generate_for_run(...)` byte-for-byte, so every figure matches the record**; two runs are byte-identical (no UI-side variation); invalid/missing input → 400. **Green: 411 tests pass; `ruff check .`, `ruff format --check .`, `mypy` (86 files), and `gridq.dev.review_gates` all clean tree-wide.** No schema, assumption value, or other lane's behaviour changed; no app code authors a number. Files: `src/gridq/app/{__init__,app,__main__}.py`, `src/gridq/app/templates/{base,index,result}.html`, `src/gridq/app/static/app.css`, `tests/test_app_smoke.py`, `pyproject.toml` (`app` extra). Filed `skills/proposed_updates/frontend.md`.
- **Dispatch solver contract + sizing optimiser skeleton (W3-2; MILP deferred to Olly) — Olly.** Built the **inner dispatch-solver seam** and the **sizing outer loop**, MILP-ready, with the real half-hourly MILP (linopy/HiGHS) deferred to Olly. New **`gridq.dispatch`**: `DispatchProblem` (frozen dataclass — site + assumptions + fixed candidate spec + the de-duplicated union of value-stack `DispatchConstraintDescriptor`s + opaque scenario), `DispatchResult` (pydantic `GridQModel` mirroring `OptimisationResult`, plus flat Optional `annual_charge_kwh`/`annual_discharge_kwh` using **D13's reserved names**, no `created_at` so it stays pure/deterministic), the `DispatchSolver` Protocol, `dispatch_constraints_for(...)` (deduped constraint union off `default_registry()`), and **`SimpleSolver`** — the labelled placeholder (dispatch-layer twin of `BaselineEngine`): always `implementation_level=baseline`/`bankable_status=not_bankable`, prices nothing, splits the six floor/contracted streams into `value_streams` and the three gated into `gated_upside`, reports `insufficient_data` when `max_import_kw` is missing, and **leaves charge/discharge `None` (unavailable) — never an invented volume**. New **`gridq.sizing`**: `SearchSpace` (validated power×duration grid — rejects ≤0, dedupes, sorts, derives energy, deterministic `(power,duration)` order; power must be supplied, duration defaults to the structural `(1,2,4)h` grid keyed to the library), `SizingObjective` (maximise levered return s.t. **P90 DSCR ≥ covenant** embedded as a feasibility gate; levered return is **unavailable in v1** — finance computes only unlevered project IRR — so it never asserts a bankable best; `None` metrics are unrankable), and **`enumerate_sizing(...)`** — the "one engine, called twice" loop: per candidate it solves the injected `DispatchSolver`, maps the result onto a per-candidate frozen `OptimisationResult` (baseline⇒not-bankable hard-coupled; streams split by `GATED_UPSIDE_STREAMS`), runs `run_financial_model` (passing `to_finance_dispatch_energy(result)`), and scores against the objective; per-candidate `InsufficientInputsError` is caught and recorded, never aborting the run. **Resolves the C010 producer seam (D13):** `to_finance_dispatch_energy` feeds finance's injected `DispatchEnergy` hook, so finance **levies-on-charge + price-to-target compute the moment a real dispatch supplies volumes — with no field added to the frozen `OptimisationResult`** (proven by `tests/test_dispatch_energy_seam.py`, which feeds *synthetic* volumes; the placeholder never invents). On the placeholder the `SizingRun` is honestly `bankable=False` with no selection (DSCR unavailable without injected debt). The covenant gate genuinely **fires** (proven: zero-revenue placeholder + injected `DebtTerms` rejects every candidate; a synthetic fixed-revenue double + debt admits some and rejects others in one run). Reviewed by 3 adversarial subagents (honesty/determinism/scope, contract-validity/seam, sizer/objective) — two dimensions fully clean; one minor fix applied (lossless `repr` in per-candidate `result_id` so distinct candidates can't collide). 34 new tests (`tests/test_dispatch_contract.py`, `test_dispatch_energy_seam.py`, `test_sizing_search_space.py`, `test_sizing_sizer.py`). **Green: 405 tests pass; `ruff check .`, `ruff format --check .`, `mypy` (81 files), and `gridq.dev.review_gates` all clean tree-wide.** No real MILP (linopy/highspy still not installed, D3); no `GateContext` added here (D12 — deferred); no schema, assumption value, or other lane's behaviour changed. Recorded decision **D17**; raised **C019** (confirm the `gridq.dispatch`+`gridq.sizing` package homes with Olly, M3/M4 owner) and responded to **C016** (the value-stack Protocol change is compatible — W3-2 consumes the registry only via `constraints()`). Docs: `docs/dispatch_engine.md`; filed `skills/proposed_updates/2026-06-22-dispatch-sizing.md`.

## 2026-06-22

- **Outputs (M8): underwriting pack + customer proposal + HTML report (W3-3) — Olly.** Built `gridq.outputs`: one stamped `QuoteRecord` → the three consistent-by-construction artifacts (System Brief Outputs 2/3, issues 011/012). Architecture is a strict **extract → format → render** pipeline so the M8 rules hold *mechanically*: **`viewmodel.build_view_model`** is the only reader of the record (it **classifies** — value-stream firmness → priced floor {firm,contracted} vs merchant upside {conditional + every `gated_upside`} vs excluded, matching finance `_PRICED_FIRMNESS`/C014 — and **aggregates values already on the record**, never authoring a new figure); **`formatting`** is the only place a number becomes a string (and the only place value-scaling like £k arithmetic lives); **`underwriting`/`proposal`/`html_report`** render from the same view model. **`html_report` does zero arithmetic** (f-strings + `str.join`, no numeric `+`/`sum`/`round`) — asserted by an AST scan. **Prose is LLM-free in v1**: a `ProseDrafter` Protocol (default `DeterministicProseDrafter`) drafts narrative from **categorical facts only** (booleans/enums — never a £-figure), so prose cannot carry a record number; an LLM drafter can be injected later on the perimeter without touching a number. **Degrades honestly**: a missing figure renders as an explicit *unavailable(reason)* / *placeholder* label, never a 0 or guess — DSCR/debt unavailable (no debt structure, B001), price-to-target unavailable (no dispatch volume), and **bill before/after/savings is a labelled-pending seam** (no customer-bill model in v1; never estimated). Baseline/placeholder runs carry a **NOT-BANKABLE banner**; provenance (input hash + assumption/code/schema versions + expiry) appears in every artifact; regeneration is **byte-identical** (no wall-clock in the output — time comes from the record). The `QuoteRecord` does not embed the `SiteModel`, so counterparty/consents/gates take an optional `SiteModel` alongside the quote (else rendered *site context not supplied*, not assumed). Wired into the CLI: `gridq run` now writes `memo.md`/`proposal.md`/`report.html` next to `quote.json`; new **`gridq render QUOTE.json [--site] [--out]`** regenerates the three from a saved record. 18 new tests across 4 files (`tests/test_outputs_{generate,numbers_match_record,sections_and_provenance,no_arithmetic_in_html}.py`) + a synthetic illustrative `rich_quote` fixture in `tests/conftest.py`; they cover the real baseline quote, the minimal committed example record (null result/finance — must not raise), and the fully-populated synthetic case. **Green: 405 tests pass; `ruff check .`, `ruff format --check .`, `mypy` (81 files), and `gridq.dev.review_gates` all clean.** No schema, assumption value, or other lane changed. Recorded decisions **D14** (LLM-free v1 prose; outputs render the record, never author a number) + **D15** (bill/savings as a labelled-pending seam, not invented). Raised **C017** (additive `FinancialCase` bill/savings fields for the finance lane to own) + **C018** (`SiteModel` not embedded on `QuoteRecord` — outputs takes it alongside). Docs: `docs/outputs.md`; filed `skills/proposed_updates/2026-06-22-outputs.md`. Refreshed the `graphify` graph.
- **Real GB value functions + `GateContext` (W3-1) — Olly.** Implemented the real GB value functions behind the generic value-stream interface so each stream now **values a dispatch schedule** (numbers from `gb_v0_1` × dispatched quantities × `scenario.price_multiplier`), under the firmness the gate decides — the £ side of M2, previously a revenue-free stub. **Pricing happens only over a real schedule:** with no `dispatch_schedule` (or a closed/excluded gate) every stream still prices `0.0` (you cannot value dispatch you do not have), which preserves every existing interface test. The market-agnostic base (`streams.py`) only orchestrates (no-schedule→revenue-free; else delegate to a per-stream `_price` hook); **all GB numbers/formulas live in `gb_streams.py`** (no `market=="GB"` branch in the core). Per-stream value (all traced to `gb_v0_1`, asserted in tests): tariff arbitrage / DUoS red-band / MIC-ASC = indicative `£/MW/yr × power`; **Capacity Market** = `clearing_price_gbp_per_kw_yr × de-rated kW` (de-rated = engine-verified `deliverable_capacity_mw`, else `power × de_rating_factor_by_duration_h[duration]` — the **deliverability profile at degraded capability**, with `degradation.annual_capacity_fade_pct` surfaced); **Piclo** = `utilisation_price_gbp_per_mwh × tender_window_discharge_mwh`; the three **gated** streams = `gated_upside_value × power` = **£0** (placeholder zero), with the ancillary **decay** machinery (`1 − fade%`) wired so it activates unchanged when a confirmed value lands; PPA/bill-savings deliberately **not** separately priced (itemised across its parts — no double-count). Every priced figure carries a **NOT-bankable warning naming the placeholder key**; gated upside stays £0 and is never firm (barred at three layers). Added the **`GateContext`** (decision D12, OPEN_COMMENTS C006) — a generic, market-agnostic carrier of the three pre-solve facts the `SiteModel` cannot hold (`deliverability_confirmed`, `local_flex_tender_open`, `portfolio_mw`/`portfolio_threshold_mw`), threaded **additively** (`context=None`) through `ValueStream.check_gates`/`value_function`, so CM/Piclo open & price as **contracted** when a producing lane supplies the context — and a no-context call is byte-identical to before. Added a **dispatch read-model** (`dispatch.py`: `DispatchScheduleLike` Protocol + `DispatchSummary` + `read_dispatch`) — duck-typed (like finance's `Scenario`), explicitly **not** the frozen `OptimisationResult` (landing these aggregates on the seam stays M3's, D9/D13). 22 new tests (`tests/test_value_stack_value_functions.py`); the 41 existing value-stack tests unchanged. **Green: 345 tests pass; `ruff`/`ruff format`/`mypy`/`gridq.dev.review_gates` clean on `src/gridq/value_stack/` + the new test (mypy clean tree-wide, 81 files).** No schema, assumption value, or other lane changed. Raised **C016** (the additive `ValueStream` Protocol-signature change for the dispatch lane). Docs: `docs/value_stack.md`. *(Pre-existing, not mine: `ruff`/`format` are red on the untracked `src/gridq/outputs/*` M8 WIP.)*
- **Stamped-run pipeline + quote lifecycle wired end-to-end (W2-4) — Olly.** Built the `gridq.quotes` package and the first real end-to-end path: a `SiteModel` now flows ingestion → `BaselineEngine` → `run_financial_model` → a **stamped, traceable `QuoteRecord`**, with a `TelemetryEvent` emitted from the first quote (X.3). **`quotes/assembly.assemble_quote_record`** stamps the run via `build_run_context(...)` over a canonical, timestamp-free hash input (mirrors `finance._hash_input`: `model_dump(mode="json", exclude={"created_at"})`; a forecast is pinned by `content_hash` only) and copies the flat `input_hash`/`assumption_version`/`code_version` onto the record, deriving a deterministic `quote_id`. **`quotes/lifecycle`** implements the append-only `draft→indicative→underwritten→contracted→operating` (+ `lapsed`, terminal) state machine — `transition_quote` returns a **new** record via `model_validate` (not `model_copy`, so the schema guards — e.g. underwritten-from-T0-needs-a-warning — actually re-fire) and appends the outgoing mode to `status_history` (prior snapshots preserved; current state is `quote_mode`, not duplicated in history). **`quotes/telemetry.emit_quote_telemetry`** emits a `TelemetryEvent` (`quote_created`) carrying data tier + outstanding-ask count + contributor, in the same shape as the manual-benchmark control group. **`quotes/pipeline.generate_quote`** is the one-call orchestrator (engine injected behind `OptimisationEngine`), **degrading honestly**: when finance has no positive sizing it raises `InsufficientInputsError`, which is caught into a `financial_case=None`, `not_bankable` DRAFT quote with the ranked asks surfaced. **Wired `build_ranked_data_asks → SiteModel.missing_data`** at last: new `ingestion/assembly` — `populate_missing_data` (safe `model_copy`) and `assemble_site_model` (folds a real HH summary into `load_data`, re-grades `data_tier`, preserves a prior `annual_kwh`/`max_import_kw`, and round-trips `model_validate` so an inconsistent fold raises rather than minting an invalid site). New **`gridq run SITE_JSON [--hh] [--forecast] [--assumptions] [--contributor] [--out] [--wholesale] [--json]`** CLI chains it all, pins a supplied wholesale/Modo forecast as gated-upside provenance (never the floor), and always writes `runs/<quote_id>/quote.json` + `telemetry.json` (gitignored). **Everything stays `not_bankable` on the baseline.** Verified live on a real HH CSV + Modo forecast (re-graded T2→T1, forecast `verified=True`, stamped + not-bankable) and on a degraded T0 site (exit 0, ranked asks, no invented numbers). 26 new tests (`tests/test_quotes_{assembly,lifecycle,telemetry}.py`, `tests/test_ingestion_assembly.py`, `tests/test_cli_run.py`). **Green: 322 tests pass; `ruff check .`, `ruff format --check .`, `mypy` (65 files), and `gridq.dev.review_gates` all clean.** No schema, assumption value, or `BaselineEngine` behaviour changed. Also refreshed the `graphify` knowledge graph for the new code and fixed the stale graph-tooling note in `CLAUDE.md` (venv path + the real `graphify query`/`graphify update .` commands). Next: the e2e baseline smoke test, then real dispatch (M3/M4) and outputs (M8).
- **Real-engine contract decisions recorded (W2-6; resolves C006 + C010) — Olly.** Decided the two deferrable real-engine contract questions and recorded them as **D12** (C006) and **D13** (C010) in `docs/decisions.md`; **no code or schema changed** (decision-only, baseline pipeline untouched). **D12 (C006):** the three value-stack gate inputs the gates report as missing — CM de-rated deliverability, Piclo tender availability, aggregated-portfolio MW — do **not** go on the frozen `SiteModel` (none is "the site's own data": deliverability is engine/sizing-derived, tender availability is time-varying market state, portfolio MW is fleet-level; the perishable tender flag would also break the hash-pinned site's reproducibility). v1 stays **data-ask-only** (a zero-diff ratification — `gates.py` already parameterizes the three predicates and `gb_streams.py` passes `None`, so CM/local-flex are `CONDITIONAL` and BM/ancillary stay gated-upside `CONDITIONAL`). The agreed promotion path is a new **generic, market-agnostic `GateContext`** threaded into the `ValueStream.check_gates(..., context=None)` seam when the producing lanes land (sized engine M3/M4, a confirmed Piclo source, portfolio runs — gated by C007); all three flow through `GateContext` because `check_gates` is pre-solve and never sees an `OptimisationResult`; never on `SiteModel`, never in the versioned assumption library, never a `market=="GB"` core branch. **D13 (C010):** (1) dispatch energy (`annual_charge_kwh`/`annual_discharge_kwh`) **stays an injected `DispatchEnergy` hook** for now — not promoted onto the frozen `OptimisationResult` ahead of its producer (M3 dispatch does not exist; promoting a guessed shape/granularity would be premature, contra D9/D10/D11); agreed eventual home is two **additive Optional** fields landed **with M3**, jointly by Olly (producer) + Oli (schema), conformance-tested, with the shape coordinated with Olly first and `DispatchEnergy` retained as the override/test hook. (2) the `Scenario.price_multiplier`/`cost_multiplier` seam is **confirmed already first-class** in `scenarios/models.py` with semantics matching finance + `scenarios/engine.py`; finance **keeps duck-typing** (per the `interface.py` `Scenario = Any` decoupling), with the intentional permissiveness asymmetry (`0.0`/None/`<0` → `1.0`) noted and a cheap cross-lane rename-guard test recommended. Decisions adversarially stress-tested by a 10-agent verification workflow (3 independent analysts + 2 skeptic verifiers per comment) — unanimous endorsement; the verifiers sharpened the deliverability-flows-via-`GateContext` (not `OptimisationResult`) point and the `meets_portfolio_threshold` needs-both-`portfolio_mw`-and-`threshold_mw` forward-wiring note. Updated `docs/03_data_contracts.md` (C006 open-question struck → resolved; dispatch-energy seam note on §3 `OptimisationResult`), `docs/shared_work/OPEN_COMMENTS.md` (C006/C010 → resolved), and `docs/project_status.md`. **Docs + decision log only — no schema/code changed, so the 296-test suite is unaffected.**
- **Forecast-provenance naming + ingestion→scenarios converter (W2-3; resolves C015) — Olly.** Renamed `ForecastReference.input_hash` → **`content_hash`** so it no longer collides with the **run-level `input_hash`** on `RunContext`/`QuoteRecord` (a forecast-content hash was masquerading under the run-pin's name); the run-level `input_hash` is now free for W2-4 and was left untouched. Aligned with `gridq.ingestion`'s vocabulary: `content_hash` is now the **canonical forecast-content hash** (`hash_market_forecast`/`hash_json`, format-independent — a CSV and a JSON export of the same curve pin identically), and an **optional `file_sha256`** mirrors ingestion's literal-bytes pin. `build_forecast_reference` and `verify_forecast_reference` were reworked to go through the ingestion loaders (CSV/JSON by suffix, via local imports so the scenarios import path stays pandas-free) so `content_hash` is the canonical hash on **every** construction path and `verify` does **content-level** drift detection (a benign re-export of the identical curve is not flagged). Added the single converter **`forecast_reference_from_market_forecast`** (`MarketForecast` → `ForecastReference`, exported from `gridq.scenarios`): recomputes `content_hash` from the forecast's points (never trusts a pre-set/fabricated hash), carries `file_sha256`, derives the coverage window from point timestamps, and **never invents** `source`/`forecast_version`/`file_ref`/`created_at` (raises if unknown). `verified` is now an honest provenance-origin signal — True iff the forecast was loaded from a real file (carries a `file_sha256`) **and** pins ≥1 point; an empty curve is never verified. Hardened + test-gaps closed after a **19-agent adversarial review** (JSON-arm seam, CSV↔JSON content-hash equality, verified-ref engine path, verify failure branches, refuse-to-invent matrix, override matrix, empty-curve guard). Docs: `docs/scenario_engine.md` §2/§4 updated. **Green: 296 tests pass; `ruff check .`, `ruff format --check .`, `mypy`, and `gridq.dev.review_gates` all clean.** No assumption values or run-level provenance contracts changed.
- **Data contracts frozen & reconciled doc↔code (W2-1; resolves C002/C004) — Olly.** Rewrote `docs/03_data_contracts.md` to mirror the implemented Pydantic schemas (`src/gridq/schemas/`) **field-for-field** and ratified those shapes as the **frozen v1 contracts** (recorded as decision **D8**). The doc had drifted to an older shape — `import_capacity_kva`/`asc_band_kva`; `chosen_spec`/`value_by_stream`/`cost_by_stream`/`meta`/`conditional_upside`/`solved_status`; per-stream `tag`+`exposure`; `lifecycle_status`/`versions{}` on `QuoteRecord`; and a `total_value` field that the code never had. It now documents the real shapes: `mic_kw`/`mec_kw`/`asc_kw` (kW, v1 convention), flat `selected_power_kw`/`selected_energy_kwh`/`duration_hours`, `value_streams[]` with a `firmness` enum, `gated_upside[]`, `solver_status`/`solver_metadata`, `quote_mode` with flat `input_hash`/`assumption_version`/`code_version`, plus a per-field table (type/req/default/constraint), all model-validator rules, the full 13-enum vocabulary (incl. `SolverStatus.insufficient_data`, C004), and the reusable `Provenance`/`GridQModel` base. The doc now declares the **schemas are the source of truth** (code-first; doc mirrors). Verified by **12 adversarial per-schema verifiers — 0 discrepancies**. Also recorded the no-external-input decisions: **D9** keep `DEFAULT_HORIZON_YEARS` a labelled structural model parameter (C009), **D10** keep tier-boundary constants in `ingestion/data_tier.py` (C013), **D11** keep `manual_benchmark` an `event_type` string marker, no schema field (C011) — C009/C011/C013 downgraded to recorded decisions. Added a glossary note distinguishing **`QuoteMode`** (lifecycle) from **`ManualQuoteType`** (artifact type). **Green: 283 tests pass.** **Doc + decisions only — no schema code changed.**
- **Finance priced-floor semantics fixed (W2-2; resolves C014) — Olly.** `finance._is_priced` (`src/gridq/finance/cashflows.py`) now defines the priced floor as firmness ∈ {FIRM, CONTRACTED} (via a new `_PRICED_FIRMNESS` frozenset) instead of the old `firmness != EXCLUDED`, which also counted CONDITIONAL. This aligns finance with the value-stack (`FloorStream` ⇒ FIRM, `ContractedStream` ⇒ CONTRACTED, `GatedUpsideStream` ⇒ at most CONDITIONAL) and `docs/03_data_contracts.md`. Latent today (BaselineEngine emits only excluded value_streams plus a separate £0 gated_upside), but it stops finance over-counting the floor once M3's real engine emits *valued* conditional streams. The gate sits in front of **both** `revenue_by_stream` and `dispatch_cost_total`, so the fix holds conditional/gated streams out of the revenue **and** dispatch-cost sides. Two regression tests added in `tests/test_finance_cashflows.py`: `test_conditional_streams_contribute_no_revenue_or_cost` (a valued CONDITIONAL stream is excluded from both sides) and `test_contracted_streams_count_toward_priced_floor` (CONTRACTED stays inside the floor — guards against over-narrowing to FIRM-only). **Green: 283 tests pass; `ruff check .`, `ruff format --check .`, `mypy` all clean tree-wide.** No assumption values or schema contracts changed.

## 2026-06-20

- **Wave 1 harmony audit — coherent & green.** Audited that all Wave 1 lanes fit together as one system (10 checks: shared schema names/meanings, real HH input, real forecast input, no invented data, placeholder labelling, provenance/traceability, quote/telemetry/status names, value-stack↔finance shared concepts, coordination-doc agreement, test/fixture naming). **Verdict: coherent and green — 281 tests pass; `ruff check .`, `ruff format --check .`, `mypy` all clean tree-wide.** No shared model is re-defined outside `schemas/`; code uses `value_streams` (never the doc's `value_by_stream`); the HH chain (`load_half_hourly_csv` → `to_load_data_summary` → `SiteModel.load_data` → value-stack gates) and both forecast paths line up on names; no module invents data; placeholders stay labelled and force not-bankable. The earlier repo-wide "RED" notes were transient mid-build states, now resolved. No tiny code fixes were required (no broken/violated shared contract); only documentation harmony was applied. Filed **C014** (finance `_is_priced` should be `firmness ∈ {firm, contracted}`, not `!= excluded` — latent until a real engine emits valued conditional streams) and **C015** (`ForecastReference.input_hash` overloads the run-level `input_hash`; reconcile with ingestion's `content_hash` + add a `MarketForecast → ForecastReference` converter). Report: `docs/wave1_harmony_report.md`. Refreshed the stale `docs/project_status.md` to Wave 1 done/green; updated `TEAM_SYNC.md` + `HANDOFF_LOG.md`. **No product code changed.**
- **Wave 1 product lanes landed (Phase 4+, 2026-06-17/18):** value & gating stack (M2, `gridq.value_stack`), financial model (M5, `gridq.finance`), scenario engine (M6, `gridq.scenarios`), ingestion (M0, `gridq.ingestion`), and dev-quality tooling (`gridq.dev.review_gates` / `gridq.dev.skills` / `gridq.telemetry.manual_benchmark`) — each built against the frozen `SiteModel`/`OptimisationResult` contracts with its own tests and docs (see `HANDOFF_LOG.md` and the per-lane docs). No invented numbers; placeholders labelled; nothing presented as bankable on `gb_v0_1`.

## 2026-06-17
- **Value stack & market-gating framework (M2) — `agent/value-stack`.** New `gridq.value_stack` package: the generic `ValueStream` (`@runtime_checkable` Protocol — `stream_id`, `display_name`, `required_inputs`, `check_gates`, `value_function`, `constraints`); the gating types `GateResult`/`GateStatus` (open/closed/conditional/unknown)/`RequiredInput`/`DispatchConstraintDescriptor`; deterministic **market-agnostic** gate predicates (`gates.py`); generic base streams (`FloorStream`/`ContractedStream`/`GatedUpsideStream`) with a **revenue-free** value function (`streams.py`); the nine GB streams as content (`gb_streams.py`); and `StreamRegistry` + `default_registry()` (`registry.py`). Gating follows the System Brief: import-side floor (PPA/bill savings, tariff arbitrage, DUoS, MIC/ASC headroom) **firm** with data / **excluded** without; Capacity Market + local-flex (Piclo) **conditional** (deliverability/tender not captured in `SiteModel` v1); wholesale/P415 + BM + ancillary **gated upside, never firm** — and barred from firm at three layers (`GatedUpsideStream`, `GateResult`, `ValueStreamResult`). The RTM gate is **assumption-status-driven** (placeholder-zero/`needs_review` ⇒ `route_to_market_available` False ⇒ conditional), so promoting a gated stream is a version bump, not a code edit. **No revenue or data invented**: missing data ⇒ warnings + missing requirements (never a guess); reuses the frozen `ValueStreamResult` contract. 41 new tests (132 total); ruff/`ruff format`/mypy **clean on `src/gridq/value_stack` + the two test files**. Docs: `docs/value_stack.md`; raised OPEN_COMMENTS C005–C007 (package location vs plan, `SiteModel` fields gates report missing, data sources gating the conditional/gated streams). **No pricing maths** — £ valuation is deferred to M3 dispatch + real inputs behind the same interface.
- **Parallel development plan (Phase 4+ enablement).** Added `docs/parallel_development_plan.md`: the rulebook for several Claude Code sessions building at once. Defines eight `agent/<workstream>` branches/worktrees (`value-stack`, `dispatch`, `finance`, `scenarios`, `ingestion`, `quotes-telemetry`, `outputs`, `dev-quality`) mapped to modules/owners/packages; merge & coordination rules (own branch; update `HANDOFF_LOG.md`; questions to `OPEN_COMMENTS.md`; only the skills-maintainer edits `SKILLS.md`, others propose via `skills/proposed_updates/`; run the quality bar before handoff; no fake data treated as real; placeholders stay labelled `placeholder`/`needs_review`); a contract-change discipline for `SiteModel`/`OptimisationResult`; and a per-lane definition of done. Codified the **shared input-data rule**: the repo supports real half-hourly commercial load data and real wholesale/Modo forecast files (under gitignored `data/`, D4); Claude must never invent missing HH, price, tariff, connection, consent or market-revenue data — missing data becomes a validation warning or a ranked data ask. Created `skills/proposed_updates/` with a proposal-format `README.md`. Updated `TEAM_SYNC.md`, `ACTIVE_PLAN.md`, `project_status.md`. **No product modules built; no pricing/optimisation logic.**
- **Optimisation engine interface + baseline (Milestone 3, part 1).** `gridq.engine`: `OptimisationEngine` (`@runtime_checkable` Protocol — `build_optimisation_request` + `solve`) and `BaselineEngine`, a deliberately limited integration-only engine. It returns a schema-valid `OptimisationResult` that is always `implementation_level=baseline` / `bankable_status=not_bankable`, invents **no** revenues (all value streams 0 with warnings), sizes illustratively (10% of `max_import_kw`), and reports `solver_status=insufficient_data` when `max_import_kw` is missing. Added `SolverStatus.INSUFFICIENT_DATA` (additive). `engine/errors.py` (`EngineError`/`InvalidRequestError`); `engine/README.md` (where the MILP plugs in, what the baseline is/ isn't for, how to avoid fake precision). 7 new tests (59 total); ruff/mypy clean. **No spoof real engine, no pricing maths.**
- **Assumption library + provenance implemented (Milestone 2).** `gridq.assumptions`: `Assumption`/`AssumptionMetadata`/`AssumptionLibrary` models (8 required fields per assumption; 23 explicit sections), a single-door `load_assumptions(version)` loader (YAML/JSON; `AssumptionError` on missing version), and `assumptions/gb_v0_1.yaml` (metadata + all 23 sections, every value a documented placeholder — nothing confirmed). `gridq.provenance`: `stable_json_dumps`, `hash_json`, `hash_file`, `get_git_commit` (safe "unknown" fallback — repo isn't a git checkout), `get_code_version`, and `build_run_context` → `RunContext` (run_id derived deterministically from the pins, not the clock). Docs: `docs/assumption_library.md` (review + placeholder policy) and `docs/provenance.md` (why quotes show assumption version + input hash). 14 new tests (52 total); ruff/mypy clean. **No pricing maths.**
- **Shared schema layer implemented (Milestone 1).** `src/gridq/schemas/`: Pydantic v2 models for `SiteModel` (+ `LoadDataSummary`, `TariffStack`, `ConnectionDetails`, `ConsentState`, `PhysicalGateStatus`, `Counterparty`), `OptimisationRequest`/`OptimisationResult` (+ `ValueStreamResult`, `SolverMetadata`), `FinancialCase` (+ sub-models), `QuoteRecord`, `TelemetryEvent`; enums incl. `DataTier` and the `MarketStream` registry; a `GridQModel` base (`extra='forbid'` + `schema_version`). Six validation rules enforced (no-negative kW/kWh/GBP except documented; T2 needs HH coverage; underwritten-from-T0 needs a warning; gated upside can't be firm; baseline can't be bankable_candidate; every model versioned). 5 example JSONs (generated from the models) + `tests/test_schemas.py` (19 tests). pytest 38 pass; ruff/mypy clean. **No pricing/optimisation logic.** Implemented per the explicit field spec, which intentionally diverges from `docs/03_data_contracts.md` (kW units; flat `OptimisationResult` + `firmness`; `quote_mode`) — reconciliation raised as OPEN_COMMENTS C002.
- **Shared development coordination system + self-evolving `SKILLS.md`.** Added root `SKILLS.md` — compact procedural memory with a protected `<!-- SLOW_UPDATE_START/END -->` block. Added `skills/` governance: `README`, `skill_update_protocol` (repo-local SkillOpt — evidence → ≤3 bounded `append`/`insert_after`/`replace`/`delete` edits → validate → accept/reject → log; protected block off-limits outside a slow-update), `meta_skill`, `validation_cases`, `edit_log`, `rejected_edits`. Added `docs/shared_work/` (`TEAM_SYNC`, `ACTIVE_PLAN`, `HANDOFF_LOG`, `OPEN_COMMENTS`, `BLOCKERS`) and `src/gridq/dev/team_sync.py` (`add_handoff_entry` / `add_open_comment` / `resolve_open_comment` / `update_active_plan` / `build_team_sync_summary`), runnable as `python -m gridq.dev.team_sync` and the new `gridq-team-sync` script. Tests added (19 pass total); ruff + mypy clean. **No pricing/optimisation logic.** Reframed collaboration: Olly co-edit one codebase; the owner split is a responsibility hint, not a wall.

## 2026-06-16
- **Subagent definitions.** Added `.claude/agents/`: `build-planner`, `feature-builder`, `schema-reviewer`, `underwriting-reviewer`, `perimeter-ai-reviewer`, `quality-regression-checker`, `frontend-polish-reviewer`, `docs-maintainer`, `optimisation-interface-reviewer`. Boundary-aware (not enforcing) prompt templates with tools scoped per role (reviewers read-only; builder/docs can edit; quality-checker can run the bar); each lists read-before-acting, tasks to handle, tasks to refuse/hand-back, output format and a quality bar. Supports sequential build, review, regression, docs/spec, frontend polish and clean optimiser↔platform integration with minimal back-and-forth.
- **Reusable slash commands.** Added `.claude/commands/`: `/plan-feature` (research + plan, stop for approval), `/update-project-docs` (sync changelog/status/decisions/open-questions), `/create-issue` (new `docs/issues/` file in house format), `/review-boundary` (platform/engine/Shared vs out-of-scope), `/postmortem` (record only permanent lessons), `/run-quality` (tests/lint/typecheck/format + propose fixes), `/update-graphify` (refresh the knowledge graph, with the verify-before-trust caveat). Each is self-contained and invocable.
- **Documentation system.** Added `docs/architecture.md` (short, links to the detailed docs), `docs/glossary.md` (BESS, BTM, PPA, CM, Piclo, P415, BM, DNO, MIC, MEC, ASC, G99, G100, DSCR, P50, P90, COD, RTM, SoC, SoH, DUoS), `docs/manual_benchmark.md` (manual-quote-time template), and `docs/module_ownership.md` (platform/engine/Shared table). Restructured `docs/decisions.md` to date/decision/reason/alternatives/consequences, and `docs/project_status.md` to cover milestone/completed/next/blockers/open-questions/owner-split. Added a documentation map to `CLAUDE.md` and refreshed its stale "docs-first" note (the repo is now scaffolded).
- **Scaffolded the repository (Milestone 0).** Added `pyproject.toml` (setuptools, src-layout; deps pydantic/pandas/numpy/pyyaml/rich/typer + dev pytest/ruff/mypy), `README.md`, `.gitignore`, `.env.example`, the `src/gridq/` package with subpackages (`schemas`, `ingestion`, `assumptions`, `provenance`, `optimisation_contracts`, `scenarios`, `finance`, `quotes`, `outputs`), a minimal `cli.py` (typer/rich; `version` + `info`), `tests/test_imports.py` (import smoke test), `examples/`, and `docs/project_status.md` + `docs/decisions.md`. Filled the `CLAUDE.md` command table; recorded the storage decision (files-first, SQLite only if needed). No pricing logic yet.
- Added `docs/03_data_contracts.md` — field-level v1 definition of the six shared contracts; adversarially verified and fixed (orphaned `optimisation_request_ref`; `contracted_vs_merchant` derived from a per-stream `exposure`; split `input_hash` into `site_input_hash` + run-level; `basis_year`, `result_output_hash`, value-sum tolerance, infeasible payload, tighter tier/duration/kVA rules, authoritative export trigger, `reproduction_env` in QuoteRecord).
- Added `docs/02_architecture.md` — local-first MVP architecture (deterministic core / agentic perimeter; stack; `src/gridq/` layout; seven data contracts; platform/engine seam; determinism; traceability; production path). Verified and fixed (platform-scoped determinism + reproduction-environment; corrected mock↔real conformance bar; frozen stream registry; orthogonal `model_version`; seed in traceability; canonical reduction order).
- Added `docs/issues/` — 15 build-issue files (001 scaffold → 015 demo-site-run), owners assigned so Oli's path builds on the mock without waiting on the real engine; verified (structure, owners, dependency cross-refs, grounding).
- Added `docs/01_project_spec.md` — practical Product + Engineering spec for v1 (mission, users, outcomes, MVP / non-MVP, M0–M9 + X.1–X.3 module map, data flow, milestones, risks, open questions); verified and fixed (defined the `OptimisationResult` contract, fixed X.1 deps, pytest strategy, M4 enumeration, capex placeholder, tier boundaries).
- Added root `CLAUDE.md` — operating guide (mission, working style, scope, architecture principles, owner split, command table, open questions); incorporated the `graphifyy` knowledge-graph directive with a verify-before-trust caveat.

## 2026-06-15
- Added `docs/00_project_boundary.md` — defines the Product Engineer workstream perimeter (what GridQ is / is not), the exact PE scope, the platform↔engine working split with the two shared contracts, the non-negotiable architecture rules, and open questions. Verified against the three source documents and corrected (quote attribution, credit-ownership scoping, quote fidelity).
