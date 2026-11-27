# Decisions

Decision log. Newest first. Each entry: **date · decision · reason · alternatives considered · consequences**.

---

### D198 — The twin's cooling plant: the thermal model's exact node step, moved only by an accepted IC-11 instruction

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-TWIN-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option (b), as amended.
  - **One public exact step.** A new module, `gridq/thermal/nodes.py`, holds `step` and `deviations`: the
    exact constant-deficit solution in `(E, d)` that `certificate._sweep` inlines for one minute. The
    certificate, `plant.py` and the package `__init__` are untouched, because parallel thermal work packages
    edit them.
  - **The only driver of chiller power is an IC-11 `FlexInstruction`.** It is accepted only when
    `check_instruction` passes against the surface and the plant is at rest. During the hold
    `F = PowerCurve.deficit(x, T, regime, K)`. With no instruction, `F = 0`. A non-finite deficit delivers
    nothing and is counted.
  - **`chiller_band` is not a driver.** It is not mapped to any register.
  - **Inputs.** The heat `x` is the IT load from the twin's load source (the `it_load` basis). The dry bulb is
    a per-minute input.
  - **The load model is authoritative for baseline cooling.** Import is `facility - (P(x) - P(x - F))`.
    `P(x - F)` is reported only as a diagnostic chiller-power point. The reduction is clamped to facility
    minus IT, and the clamp is counted.
  - **Recovery is a labelled twin assumption.** After the hold, `F = -min(spare, u_cap, E/dt)` until `E = 0`,
    with `spare = n x unit - x`. This is the policy the WP-THERM-03 oracle simulates. The rebound kW adds to
    import.
- **Reason.** The twin then re-implements nothing the certificate proves. A hold the surface accepts runs on
  the very equations that certified it, so the twin is a test of the surface rather than a second model of
  the plant.
- **Alternatives considered.**
  - (a) Re-deriving the nodes inside the twin. This makes a third copy.
  - (c) Driving chiller power from `chiller_band`. IC-11 instructs a kW reduction, not a band, and the band
    is read-only in `diode_read_only`.
- **Consequences.**
  - Two copies of the node update exist until a later work package folds the certificate onto
    `gridq.thermal.nodes`. This is recorded as debt.
  - A drift tripwire fails if the certificate's lines change:
    `tests/site_twin/test_twin_thermal.py::test_the_certificate_still_steps_the_nodes_as_nodes_restates`.
  - Evidence:
    - the twin matches the independent oracle within its own error bound
      (`test_the_twin_trajectory_matches_the_independent_oracle`);
    - accepted instructions stay inside `loop_up`, `hall_up` and `f_rate`
      (`test_accepted_instructions_stay_inside_the_certified_limits`).

### D197 — The twin's battery: kernel-segment replay at one second, no edit to the kernel

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-TWIN-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option (a), "kernel-segment replay".
  - **The clock.** The twin runs a 1 s simulated clock. A register write takes effect from the next whole
    second.
  - **The kernel call.** At each minute boundary, and at any change of setpoint, the twin calls
    `gridq.adequacy.kernel.run_kernel` once:
    - one draw, one minute, `fine=True`, `skip=False`;
    - a `Trace`, and `soe0_j` set to the present state of energy.

    It then consumes `Trace.soe_after` one entry per second.
  - **Setpoint encoding, after clamping to the ratings.**
    - Discharge `S`: `permit = net - S`, `cover_cap = S`.
    - Charge `C`: `charge_cap = C`, `permit = net + C`, target = usable J.
    - Idle: caps and target 0.
    - Always: merchant 0, floor 0, no events, no recharge metrics.
  - **The load** is the same `LoadSource` with an explicit `draw_index`. There is no default draw and no
    medoid.
  - **The meter.** Import is `facility - cooling reduction + (grid - ac)`. One meter function,
    `meter_joules`, holds the only restated quantisation lines. The state of energy comes only from
    `Trace`.
  - **`Trace` is promoted to a supported read interface** of the kernel.
  - **The twin obeys its setpoints.** It reports import-limit exceedance; it does not enforce the limit.
- **Reason.**
  - Equality with the design-time model is then true by construction and checkable to the joule.
  - `kernel.py`, which the parallel SITE work packages edit, is not touched.
- **Alternatives considered.**
  - (b) A separate 1 s battery model. This would be a second implementation whose agreement could only be
    a tolerance.
  - (c) Adding a step API to the kernel now. This edits a file other branches are changing.
- **Consequences.**
  - Follow-up, after SITE-05/06 merge: add `ac_j`/`grid_j` to `Trace`, which removes the meter's copy.
  - The tests in `tests/site_twin/test_twin_electrical.py` give:
    - encoded equality per second;
    - native-policy equality;
    - mid-minute changes equal to chained kernel runs;
    - meter invariants;
    - an AST check that the state of energy comes only from `Trace`;
    - the coarse 60 s path within 60 J per active minute.

### D196 — The device boundary: real Modbus TCP, a separate reference gateway, commissioning as data

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-TWIN-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option B, amended.
  - **The twin's wire.** The twin (`gridq.site_twin`) serves real Modbus TCP on 127.0.0.1 from a
    Neura-declared SYNTHETIC register map. Every value is an int64 in four big-endian registers, in IC-14
    canonical units.
  - **The gateway** is a new sibling package, `gridq.edge_southbound`, built on the standard library and
    `gridq.contracts` only. It holds a Modbus client, a poller and a batch sealer, and takes its clock by
    injection.
  - **Descriptors are commissioning data, not gateway code.** The gateway copies finished
    `SeriesDescriptor`s verbatim, plus a wire binding (unit, table, address, width).
    - The twin's commissioning set declares `simulated`, with the register coordinates in the address, and
      `synthetic_fixture`.
    - A real site's set declares `modbus_tcp`.
  - **The write allow-list is injected as data.** The harness passes the classes from
    `write_bindings(stage, mode)`. A refused write sends zero bytes.
  - **Sealing.** The twin never seals telemetry. The gateway seals as `edge_acquisition`, with
    `receipt_lag_ms` not applicable.
  - **Limits.**
    - Only the Modbus conduits are covered (battery C3, the site meter, read-only facility and cooling).
    - ANM enters as scenario input to the IC-5 reference edge.
    - The gateway is a reference: it has no reconnect, store and forward, or fail-safe, and no northbound
      IC-5 transport of batches.
    - The twin serves SYNTHETIC sites only.
- **Reason.**
  - Acceptance 1 ("no twin-specific code path") is then a property of the gateway's imports and arguments,
    and it can be proved structurally.
  - The edge reaches the twin the way it reaches any site: over the same protocol, from the same kind of
    commissioning data.
- **Alternatives considered.**
  - (A) An in-process device interface that the twin implements. The "gateway" would be twin-aware by
    construction.
  - (C) SunSpec models. A vendor model text is a person-held ask, and it would be reproduced from memory.
- **Consequences.** The tests in `tests/site_twin/test_twin_gateway.py` give:
  - the import closure, by AST and in a fresh interpreter;
  - a no-branch lint;
  - an independent responder written in the tests, driven by the same gateway from a `live` / `modbus_tcp`
    set, with `check_batch` passing in both runs;
  - byte-identical request streams;
  - constructor opacity;
  - allow-list refusal with zero bytes sent;
  - the IC-5 reference loop reaching the twin.

### D179 — The thermal turn-down limit is catalogued but not applied, and the catalogue states which path applies each constraint

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SIZE-02). Executed for Olly.
- **Context.** WP-SIZE-02 lists thermal limits in the shared set. The battery MILP has no cooling
  variable. The rolling-horizon MPC solves each window through the annual build with the same
  `DispatchProblem` (its descriptors included), but never with the export overlay.
- **Decision.** `gridq.constraint_library.thermal` holds the constraint `thermal_turn_down_limit`
  (`turn_down_t ≤ deliverable_kw(surface, t, duration)`, read from the IC-11 surface by the contract's own
  `query_at`; an `unavailable` or `unknown` cell gives 0 kW with its status beside it, never an invented
  number) and the row builder. Its catalogue entry states `applied_by=()`. `export_limit` states
  `("annual_milp",)` (the MPC runs behind the meter); every other constraint, the two commitment rows
  included (measured: each MPC window whose span meets a committed window builds
  `availability_reserve_aggregate` and `held_state_of_energy_<id>`), states both paths. The catalogue says
  what is applied; nothing is implied.
- **Tests.** `tests/test_constraint_library.py::test_thermal_limits_are_read_from_the_ic11_surface`,
  `::test_every_roadmap_family_is_catalogued`, `::test_each_mpc_window_builds_its_constraints_from_the_library`.
- **Direction of effect.** None on any figure.
- **Alternatives considered.** Add a cooling variable to the battery MILP now (rejected: a new resource in the
  dispatch is WP-SIZE-03/A5 scope and would move the golden pins). Leave thermal out of the library
  (rejected: B2 and A5 must take it from one place when it is applied).
- **Consequences.** When a cooling resource enters the model it calls `add_turn_down_rows`, and its
  catalogue entry gains the paths that apply it (a minor library release). The MPC's export overlay, if
  B2-02 needs one, would move `export_limit` the same way.

### D178 — `OptimisationResult.constraint_library`: an optional stamp in IC-12 3.2.0, set by every engine, kept out of `input_hash`

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SIZE-02). Executed for Olly.
- **Context.** The acceptance requires the library version in every `OptimisationResult`, a frozen IC-12
  model.
- **Decision.** `OptimisationResult` gains `constraint_library: ConstraintLibraryRef | None = None`
  (`gridq.schemas.constraint_library`: `library_id`, `library_version`, `library_hash`, sorted unique
  `constraint_ids`; the members restate IC-3's `ConstraintLibraryPin`). Released as **IC-12 3.2.0**, an
  additive minor on the D79 template: the three `v3.1.0` files archived at their tagged SHA-256, `v3.2.0`
  rendered, `SiteModel` byte-identical, `QuoteRecord` moved only through the embedded result.
  1. **Optional in the schema, obligatory for an engine.** The member is absent from every dump while
     unset (`omit_unset`, the 2.0.0 rule), so every 3.1.0 payload reads with the same bytes. The
     obligation is enforced where it belongs, on producers: every `OptimisationResult(...)` in `src`
     passes it (AST test), and the engine conformance bar checks
     `provenance.constraint_library_stamped` on every engine result.
  2. **Every producer stamps it.** The sizer's per-candidate result (`sizing/sizer.py`), the MILP
     engine's no-spec result (`optimisation/engine.py`), the baseline (`engine/baseline.py`, which solves
     nothing: its stamp names the set any dispatch from that build applies) and the demo's no-spec result
     (`app/demo.py`). `model_copy` and the underwriting post-pass (`model_validate` of the result's own
     members) carry it through.
  3. **Out of `input_hash`.** The library is code. `code_version` already pins it through `run_id`, and
     `input_hash` excludes `code_version` so that it answers "did an input change?". Hashing the stamp
     would move `input_hash` on a library edit that moved no input, which is the defect that exclusion
     exists for. `quotes.assembly._canonical_result_dump` excludes it. Finance's `case_id` hashes the
     whole result, which already carries `code_version`, so it is unchanged in kind (finance not touched).
- **Tests.** `tests/contracts/test_ic12_chassis.py::test_3_2_0_is_3_1_0_plus_the_stated_optional_members`,
  `::test_the_3_2_0_stamp_is_one_closed_definition_mirroring_the_ic3_pin`,
  `::test_a_3_1_0_reader_refuses_a_payload_that_sets_the_3_2_0_stamp`,
  `::test_an_existing_run_input_result_and_record_keep_their_bytes`;
  `tests/contracts/test_schema_archive.py::test_the_archive_pins_every_tagged_file`;
  `tests/test_constraint_library.py::test_every_result_constructed_in_src_is_stamped`,
  `::test_the_sizer_stamps_each_candidate_result`, `::test_the_baseline_and_underwriting_engines_stamp_their_results`,
  `::test_the_milp_engine_stamps_its_no_spec_result`, `::test_the_demo_no_spec_result_is_stamped`,
  `::test_the_conformance_bar_refuses_an_engine_that_does_not_stamp`, `::test_the_stamp_never_moves_the_run_identity`;
  `tests/test_optimisation_contract_conformance.py::test_engine_conforms` (baseline and MILP).
- **Direction of effect.** None on any figure or identity hash.
- **Alternatives considered.** A required member, a major (rejected: every 3.1.0 result and record would be
  refused, and nothing about them is wrong). The version in `SolverMetadata.model_version` (rejected: that
  string names the dispatch formulation and enters `input_hash`; overloading it hides the library hash).
  Hash the stamp into `input_hash` (rejected, point 3).
- **Consequences.** IC-12 is at 3.2.0; a 3.1.0 reader refuses a stamped result, as for every minor here. Tag
  `contracts/ic12-chassis/v3.2.0` is created at the integration merge.

### D177 — Library identity: a semantic version, a source hash, an append-only release record, and the IC-3 pin built from them

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SIZE-02). Executed for Olly.
- **Context.** A version string alone can be left unbumped after an edit; a hash alone tells a reader
  nothing about compatibility. IC-3 (D88/D89) already pins `library_version` + `library_hash` +
  `constraint_ids` in an envelope and compares them at dispatch.
- **Decision.** `LIBRARY_VERSION` (now `1.0.0`) is semver: patch for a change that moves no row, bound or
  default; minor for an added constraint; major for any change to an existing constraint's rows, bounds or
  defaults. `library_hash()` is the SHA-256 of every module in the package except `_releases.py` (by sorted
  name; name, NUL, text with newlines normalised, NUL). `_releases.RELEASES` is append-only and pins the
  hash each version had, and a test fails when the source moves without a new version and entry.
  `library_ref()` is the result stamp; `library_pin()` the IC-3 pin the envelope exporter (WP-SIZE-08) writes;
  `check_envelope()` is IC-3's `check_constraint_library` against this build.
- **Tests.** `tests/test_constraint_library.py::test_the_released_hash_is_the_source_hash`,
  `::test_the_current_version_is_the_newest_release`, `::test_the_hash_covers_every_module_but_the_release_record`,
  `::test_the_stamp_and_the_ic3_pin_state_the_same_library`,
  `::test_live_dispatch_matches_an_envelope_sized_with_this_library`, `::test_the_stamp_refuses_a_malformed_library`.
- **Direction of effect.** None.
- **Alternatives considered.** Hash the whole `gridq` tree (rejected: it would move on every unrelated edit,
  so "same library" would never be provable across commits). Use the git commit (rejected: not available in
  every runtime, and identical constraint code would differ by commit).
- **Consequences.** Any edit to a library module is a release. Live dispatch can prove it runs the constraint
  set an envelope was sized with.

### D176 — `gridq.constraint_library`: what the shared set holds, and the contracts-only boundary

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SIZE-02). Executed for Olly.
- **Context.** NEW-SIZE-LIB: the live MPC (B2-02) must apply the same constraint set as the sizer (A5). The
  set lived inside `optimisation/milp_solver.py`, with the import limit and the warranty cap written again
  in `mpc_solver.py` and the import limit in `two_stage.py`.
- **Decision.** One package, `gridq.constraint_library`, with a module per family: `connection` (import
  limit min(MIC, ASC), the power-factor de-rating, the charge, discharge and export bounds, the export
  inverter row, the reactive screen, the inverter P-Q and connection kVA octagons), `resilience` (the
  forced load-service draw and the pre-window state-of-energy rows), `soc` (the window, its bounds, the
  usable-energy disclosure, the rolling-horizon start check), `warranty` (the ceiling lookup, the cap, the
  row, the disclosure), `commitments` (the kind registry, the calendar mask, the deliverable envelope,
  the compiler) and `thermal` (D179), plus a `catalogue` of the eleven constraint ids.
  1. **The library holds algebra, row names and disclosure text; the caller resolves every number.**
     Release reads (`maybe`/`numeric`), the provisioning derivation (`gridq.sizing.provisioning`) and
     the ancillary throughput stay in the solver, which hands the library numbers and arrays.
  2. **Boundary.** The library imports only `gridq.contracts`, the IC-12 chassis in `gridq.schemas`,
     itself, the standard library, numpy, pandas and xarray. Proven by an AST scan of every module (lazy
     imports included) and by a fresh interpreter whose loaded GridQ modules are the contracts and
     chassis only. Neither `gridq.contracts` nor `gridq.schemas` imports the library.
  3. **Pure refactor.** The arithmetic, row names and creation order are unchanged. The optimisation
     package keeps its old private names as aliases of the library's objects (tests and callers read
     them), and `optimisation._assumptions.ceiling_value_for_duration` is a re-export.
- **Tests.** `tests/test_constraint_library.py::test_the_library_imports_contracts_only`,
  `::test_the_library_loads_no_workstream_module_transitively`, `::test_the_boundary_scan_has_teeth`,
  `::test_the_contracts_never_import_the_library`, `::test_the_annual_milp_builds_its_constraints_from_the_library`,
  `::test_each_mpc_window_builds_its_constraints_from_the_library`,
  `::test_the_sizer_search_box_reads_the_library_import_limit`,
  `::test_the_optimisation_package_reads_the_library_not_a_copy`; the golden tier,
  `tests/test_determinism_golden.py`, unedited and green. Measured: every linopy model built by seven
  dispatch test modules (410 models: annual MILP, provisioning, commitments, reactive, MPC windows) wrote
  a byte-identical LP file before and after the extraction.
- **Direction of effect.** None.
- **Alternatives considered.** Let the library read the `AssumptionLibrary` itself (rejected: that is
  workstream code, and the B2 runtime will not carry a release object). Keep the MPC's own copies of the
  limit and the cap (rejected: the duplication is the defect NEW-SIZE-LIB names).
- **Consequences.** A change to a dispatch constraint is a library release (D177), visible on
  every result it produces (D178).

### D175 — Reconciliation of the five WP-SITE-04 rulings

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-04). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Where D170 to D174 overlap, the build follows these rules.
  - (a) **One integer-joule kernel** (ruling 5) carries ruling 2's quantisation. The discharge request
    on the battery side is `ceil(ac x den / num)`. When the floor clamps it, the AC delivered is
    `floor(battery x num / den)`. Charging takes `min(headroom, rating, ceil(room x den / num))`
    from the grid and stores `floor(grid x num / den)`, which never exceeds the room.
  - (b) **The policy carries no evidence field** (ruling 4). Ruling 3's sign-off slot is dropped. The
    result embeds the policy and the approval side by side.
  - (c) **Recharge metrics apply to `reserve_held` only.** Under the envelope adversary they are
    `NotApplicable`: that posture drains by design. The recharge target of a gap is the lower of the
    state on entering the event and the held level.
  - (d) **`paths_digest`** is SHA-256 over a change-record stream: the step, the draws that changed, their
    state, their shortfall, and the floor. Skipped steps change nothing, and a floor change makes its
    minute active. So `skip=True` and `skip=False` give the same digest, and a test asserts it.
  - (e) **Which clock answers which question.** IC-11 cells are read on the scenario's GB wall clock.
    IC-11 and IC-3 validity, and the IC-3 terms, are read at the study instant (the scenario instant plus
    the load anchor's whole-day shift). The floor cell is read on the load minute that the step protects.
  - (f) **Data errors and contract errors.** Missing data returns `NotComputed` with typed asks. Contract
    errors raise: an export set, a site mismatch, a BESS or load that the policy was not sized from, or
    fewer than `minimum_draws()` draws.
  - (g) **The kernel counts breaches independently of `_dispatch`.** The aggregation raises `FloorBreach`
    if any are counted. The property test re-derives the floor from the policy rather than from the kernel.
  - (h) **Pinned hashes.** A cross-platform golden hash is pinned only for explicit-array load. A
    load-model run is checked in-process and in a fresh interpreter but not pinned, since WP-SITE-01
    pins no float output either.
- **Reason.** Each rule removes a place where two rulings would have produced two answers.
- **Alternatives considered.** A float kernel with a tolerance would lose cross-platform hashes. Keeping a
  sign-off slot on the policy would put the evidence inside the bytes it signs.
- **Consequences.** `gridq.adequacy` has one arithmetic, one choke point and one approval path.

### D174 — IC-3: the admissible-adversary bracket, behind a single floor choke point

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-04). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  - **`reserve_held` is always run.** There is no trading, and the battery recharges toward its held level.
  - **`envelope_adversary` is run when an IC-3 envelope is given.** It models the worst dispatch the
    envelope allows. Every step outside an event, it requests the full `discharge.merchant_kw`, down to the
    floor plus the energy held for services. It stops when the event's notice arrives, and from then until
    the event ends it recharges toward the envelope's ceiling.
  - **Headline and bound.** The headline posture is the adversary when there is an envelope (bound
    `every_envelope_compliant_dispatch`), and otherwise `reserve_held` (bound `no_trading`).
  - **The floor is never spent on a restriction.** A shortfall is unserved energy, and the resilience kW
    is held back from cover: cover is capped at `rating - resilience_kw - withheld`.
  - **Envelope conformance.** The effective floor is `max(policy, envelope)`. There are three findings:
    `ENVELOPE_FLOOR_BELOW_POLICY`, `ENVELOPE_RESILIENCE_KW_BELOW_PROTECTED_LOAD` and
    `ENVELOPE_FAIL_SAFE_FLOOR_BELOW_POLICY`. Any finding makes the run nonconforming, which cannot be
    approved for use.
  - **Producer helper.** `resilience_terms(policy)` hands WP-SIZE-08 the floor kWh and the discharge kW,
    both rounded up, with 0 kW to charge.
  - **One choke point.** Every joule that leaves the battery passes through
    `_dispatch(soe, request, purpose, floor, purpose_floor)`, which never goes below
    `max(floor, purpose_floor)`. A structural test checks that the kernel sets the state of energy only
    through `_dispatch`, `_charge` or the initial fill.
  - **Ten mutants**, each killed:
    - lower bound 0;
    - floor − 1;
    - purpose floor only;
    - cover bypassing `_dispatch`;
    - the envelope floor alone;
    - an unsigned floor zeroed;
    - the previous cell's floor after a step-up;
    - an un-ceiled floor;
    - a separate clamp on the one-second path;
    - merchant trading inside events.
- **Reason.** Bracketing every compliant dispatch proves the floor against any trader the envelope admits,
  without modelling markets. A single choke point makes the floor property checkable.
- **Alternatives considered.** Simulating a specific merchant strategy, which gives no bound. A
  per-path clamp, which is easy to bypass.
- **Consequences.** A `MerchantSource` seam is left for a real dispatcher.

### D173 — Sign-off: the signed document is the floor statement itself (IC-4 1.2.0 suffices)

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-04). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  - **No new IC-4 type.** IC-4 1.2.0's `critical_facilities_signoff` (since 1.0.0; inventory row 15:
    WP-SITE-04, issued by the host or the customer) is the type. There is no IC-4 1.3.0 and no schema change.
  - **The statement.** The policy renders `gridq.resilience_floor_statement/1.0.0` as canonical bytes.
    The bytes carry the floor, its cells, the requirement verbatim, the policy hash and the fixed
    declaration that the reserve is never available to markets. A sign-off binds only if its
    `document_hash` is the SHA-256 of those bytes.
  - **The admission bar.** `admit_floor_signoff` returns `Approved` only when every check passes:
    - the hash matches;
    - the object has a `SiteScope` for this site;
    - it is issued by the host or the customer;
    - it is not a synthetic fixture, unless the policy is one;
    - it is VALID on `as_of`;
    - it is `Verified` by `confirmed_with_issuer` on or before `as_of`.
  - **Otherwise `Unsigned`.** The outcome carries one of thirteen reason codes. The reason of the candidate
    that got furthest wins. It also carries a typed ask that names the exact hash to sign.
  - **No forged approval.** The result validator re-runs the admission and refuses an `Approved` the bar
    would not give.
  - **Labels.** Labels come from one closed table: an unsigned floor reads "FLOOR UNSIGNED: …" and never
    reads as approved.
  - **Approval does not touch the physics.** `simulation_hash` excludes the approval, so a signed run and
    an unsigned run of the same inputs share it.
- **Reason.** Binding to the bytes means a sign-off of a 300 kWh floor cannot sign a 400 kWh floor. A
  changed requirement voids the old sign-off. No IC-4 change is needed.
- **Alternatives considered.** An IC-4 1.3.0 "floor approval" type, which duplicates an existing
  member. A free-text reference, which cannot be checked.
- **Consequences.** Signing changes the label, never the floor. The ask names the hash the customer
  must sign.

### D172 — The floor: a worst-window envelope, per IC-3 cell, never clamped

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-04). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  - **Inputs.** The protected load is a stated constant kW, or a stated fraction of the modelled IT
    or facility load, rounded up to whole watts. The window is `n = ceil(duration_s / 60)` minutes.
  - **Window energy.** For each draw and each start minute whose window fits the horizon, the window's
    energy is taken. The windows that do not fit are counted, not tiled.
  - **Statistic across draws.** This is either the ensemble maximum or a customer-stated quantile
    ("higher" method).
  - **Per cell.** Each cell takes the maximum over the windows that start in it. A cell of a GB-clock
    partition is IC-3's Season × DayGroup × TimeBand.
  - **Floor arithmetic.** A stated margin is added. Then `floor_kwh = ceil_snapped(E / eta_discharge)`
    (D86).
  - **Power reservation.** `resilience_kw` is the maximum protected kW, rounded up.
  - **Summary figures.** `flat` is the maximum over cells and `fail_safe` is the minimum.
  - **Unknown cells.** A cell with no assessed start is `Unknown`, and the flat floor applies there.
  - **States.**
    - `Sized`.
    - `Indeterminate`: any input unknown; each unknown is a typed ask.
    - `Infeasible`: the floor is above the usable energy, or the reservation is above the discharge
      rating. It is never clamped.
  - **Flags.**
    - A round-trip-only efficiency is used as a flagged safe-side bound, with a one-way ask.
    - A non end-of-life energy figure is flagged, with an ask.
    - A floor equal to the usable energy is flagged `no_tradable_energy`.
  - **Margin.** A margin of none must be declared (`NotApplicable`). An unknown margin blocks sizing.
- **Reason.** The worst window over the ensemble is the only statistic needing no stated risk level.
  Per-cell floors free energy where the protected load is low. Integer arithmetic makes the floor exact.
- **Alternatives considered.** A flat floor only. A mean-load floor, which is not a reserve. Clamping
  to the usable energy, which would hide an infeasible requirement.
- **Consequences.** The autonomy requirement and BESS parameters are inputs. Nothing is defaulted.

### D171 — Estimators: integer and rational results, intervals on load-draw error only

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-04). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  - **Units.** Power is integer W, energy integer J (W·s), probabilities `ExactRational`. There are no
    floats in `gridq.adequacy_result` 1.0.0.
  - **LOLP.** The headline is `sum_s w_s k_s / N`: the probability that a path has any unserved step
    (an unserved step is one short by at least 1 W).
    - Also reported: the expected loss seconds and the per-event counts. A step belongs to the event in
      force or most recently started, or to `outside_event`.
    - Its interval is per-scenario Clopper-Pearson, exact on a ppm grid and rounded outward, with a
      Bonferroni split, weighted and summed.
  - **EUE.**
    - It is reported as an exact expectation, and as `per_year` only when every period is at least
      365 days.
    - P50 and P95 of the per-draw totals.
    - A 2,000-resample bootstrap from a SHA-256 counter stream, ranks 50 and 1,950.
  - **Minimum state-of-energy margin over the floor**, with where and when it was reached.
  - **Recharge feasibility per gap.** Each gap is `feasible`, `infeasible` or `censored`. The record also
    gives counts, the minimum margin in seconds, the maximum shortfall in Wh, and
    `p_any_infeasible_gap` with its interval.
  - **Binding.** The record binds every input by hash, embeds the policy and the config, and states
    `interval_scope = load_draw_sampling_error_only` with three fixed warnings.
- **Reason.** Exact arithmetic is what makes acceptance 3 (reproduce by hash) hold on every platform.
- **Alternatives considered.** Float estimators with tolerance-based comparison, which give no hash
  reproducibility. Normal-approximation intervals, which are wrong at zero events.
- **Consequences.** A test re-runs in a fresh interpreter with a different `PYTHONHASHSEED`. Golden
  hashes are pinned for the explicit-array case.

### D170 — The kernel: one chronological equation, quiescence-skipping, full cross product

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-04). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  - **One equation.** A fixed-step equation runs at 60 s, or at 1 s inside event windows when asked. It is
    vectorised over the N load draws and runs once per scenario. The state of energy is carried through
    the whole scenario, and recharge is simulated.
  - **Skipping.** Minutes where the state is a provable fixed point are skipped. `skip=False` gives the
    same record.
  - **Sampling.** Joint sampling is the full cross product S × N, with path weight `w_s / N`.
  - **Time base.** Scenario UTC maps to the Europe/London wall clock, then to the load model's naive
    minute, shifted by a stated `LoadAnchor` or the same month and day in the horizon. Clock-change
    minutes are counted, and a horizon that does not cover the mapping is a data ask.
  - **Restriction depth.** It comes from the IC-2 minute series, or from the held half-hourly series.
    The permit is `max(0, reference - depth)`. An unknown reference capacity needs a stated
    `import_capacity`, or it becomes an ask.
  - **Thermal flex** is read only through IC-11:
    - no response terms means no flex, and an unknown notice counts as zero;
    - the call starts after the surface's notice;
    - the start cell decides the curve, and the hold is cut at the first unavailable or unknown cell
      and at `valid_until`;
    - a longer event holds the longest point for its duration;
    - the ramp is linear, and delivery is clamped per draw to `facility - IT`;
    - the rebound is the peak for `ceil(energy x 60 / peak)` minutes, and the next call waits out the
      recovery.
  - **Two arms** are reported: thermal off and thermal from IC-11.
- **Reason.** A single equation is checkable. Skipping makes year-long scenarios cheap without a
  second code path.
- **Alternatives considered.** Event-only simulation, which loses recharge. An analytic convolution,
  which cannot carry the state of energy.
- **Consequences.** WP-THERM-03 is consumed only through IC-11, so no ThermaFlex code is imported.

---

### D169 — The firmness certificate: a dominating deficit, a settle tail, live refusal during recovery

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation of WP-THERM-03 design choices; adopted by
  the session for Olly that built WP-THERM-03.
- **Context.** D166 chose a closed-form certificate checked by an independent oracle. Five
  derivations needed a verdict before code: the dominating forcing, the rate bound, the undershoot cap,
  the rebound bounds, and what to do with a start inside another event's recovery.
- **Decision.** Accepted with amendments. (1) The two-node system is cooperative (Metzler), so the
  trajectory driven by the largest deficit dominates every start in the slot; the stored heat is judged
  at the end of the hold, and a running minimum over kW keeps the frontier monotone (tested). (2) Rate:
  `|F| <= rho (C_loop + C_hall) / 3600` against a constant baseline inlet. (3) Undershoot: the hall is a
  first-order lag of the loop, so recovery stops when the stored heat reaches zero and the oracle checks
  the discrete overshoot. (4) Rebound: energy and peak use a Lipschitz (chord) bound on `dP/dx` across
  staging steps; spare capacity excludes locked-out chillers until the lockout ends; recovery time is
  monotone in duration. (5) A start during recovery is refused live (`REBOUND_RECOVERY_IN_PROGRESS`), and
  the declared recovery time includes a settle tail `ln(d0_max / eps) / lambda` so the residual imbalance
  after the stored heat clears cannot break the hall bound. As built: `eps = 0.01 K x C+ / max(C_loop,
  C_hall)`, a `SETTLE_MARGIN_K = 0.01` on every thermal check, and a switch-off formulation that keeps
  enough units carrying the highest load through the lockout, so no lockout deficit arises.
- **Reason.** Each amendment closes a gap in the proof the advisor found: a non-Lipschitz slope bound
  understates rebound across a staging step, and the residual imbalance after recovery is real heat.
- **Alternatives considered.** Folding a mid-recovery start into the start box (rejected: it widens
  every cell to cover a rare case, and the live refusal is already an IC-11 reason). A typed safety
  margin (rejected in D166).
- **Consequences.** The live refusal is not built: it belongs to the controller that receives IC-11
  instructions. The fixture's declared recovery runs about five times the oracle's (the settle tail).

### D168 — Inputs and sources: cited presets, a data request for real sites, stand-ins only as synthetic

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-THERM-03 session for Olly.
- **Context.** The user's rule: no invented data. Constants and ASHRAE limits come from cited public
  sources, plant parameters for real sites from OPS-01 dossier fields or a data request. Every OPS-01
  site has an unknown cooling topology today (no DPA registered).
- **Decision.** (1) A-c: an ASHRAE A1 preset (recommended and allowable) from the TC 9.9 2011 whitepaper,
  held with URL, sha256 `ce23e471…ab549`, retrieval date and its caveats (2011 whitepaper, not the paid
  5th edition; third-party host; altitude derate not applied; note f rates applied to the recommended
  row). It is a constructor and a comparator (`matches | tighter | looser | not_comparable`), never a
  default. H1 has no preset: it is not in the held source. (2) S-a: a real site gets a typed
  `PlantDataRequest`, not a surface; `thermal_plant_model` returns `ThermalFlexAvailability |
  PlantDataRequest`. The dossier topology mapping is total and tested. (3) Every parameter carries
  `SiteEvidenced | Declared | StandIn`. Any stand-in forces `record_class = synthetic_fixture` under a
  `SYNTHETIC-` id from the input hash, names the stand-ins in `ModelledBasis.model`, and returns the data
  request alongside. (4) Psychrometrics use the WMO No-8 / JCOMM TR 63 Magnus coefficients (sha256
  `8fa0eb32…e4d1`) with their validity guard. Capacitances are inputs; no fluid property is held.
- **Reason.** A registered all-unknown surface would still assert a topology and limits under a real
  site id. A preset without a comparator lets a mis-typed class pass silently.
- **Alternatives considered.** No preset (rejected: leaves a citable envelope unused). Stand-ins never
  (rejected: blocks indicative work). A fixed glycol `rho c_p` (rejected: an invented number).
- **Consequences.** IC-11 1.1.0 cannot carry a minimum RH, so the A1 allowable humidity is emitted as
  Unknown with the reason and enforced inside the model. That gap is for the next IC-11 minor.

### D167 — Chiller strategies, rebound and humidity

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-THERM-03 session for Olly.
- **Context.** Chiller minimum run and off times, rebound after every event, and humidity are in scope.
- **Decision.** (1) R-c: unload-only and switch-off are separate producers; each point takes the better
  firm one. Switch-off cannot stop a unit before `min_run` under worst-case history and locks it out for
  `min_off`; an unknown timing makes switch-off "none". (2) B-b: recovery surplus is the lesser of spare
  running capacity and a cap that respects the rate and undershoot limits; the first-law closure
  (deficit = recovery + stored) is a tested invariant; declared rebound figures round up. (3) H-a: the
  dew point is held between its declared bounds; minimum RH becomes a temperature ceiling and maximum RH
  a floor (WMO Magnus inverted).
- **Reason.** Full-capacity recovery can break the rate limit on the way down; unload-only discards real
  flex at long holds; ignoring minimum RH misses the limit that binds as the hall warms.
- **Alternatives considered.** R-a unload only, R-b switch-off only, B-a full capacity, B-c a stated
  rebound cap (no site has one), H-b/H-c (silent or unverifiable).
- **Consequences.** None of the strategy timings is defaulted; real sites ask for them.

### D166 — Firmness is proved by a certificate and checked by an independent oracle

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-THERM-03 session for Olly.
- **Context.** Acceptance (4): every declared kW is firm from sampled starts across each cell at worst
  ambient and load, held for its minutes within limits.
- **Decision.** F-d. The certificate (comparison principle on the cooperative two-node system) gives the
  largest kW per (slot, duration) exactly; its preconditions are checked, and a failure is `Unknown`
  with the reason. A naive forward-Euler oracle in the tests, importing nothing from `gridq.thermal`,
  samples starts and driver paths (random and worst-case) and checks the kW held, every limit, and the
  rebound. Its tolerance is its own Euler error bound. Hypothesis is not installed in the shared
  environment, so the sampler is seeded numpy (a recorded deviation; the advisor allowed Hypothesis as a
  dev extra only).
- **Reason.** Sampling alone proves nothing about unsampled starts; a certificate alone has nothing
  independent checking it.
- **Alternatives considered.** F-a sampling with a typed margin, F-b alone, F-c zonotope reachability
  (over-approximates what monotonicity gives exactly).
- **Consequences.** No typed safety margin. Slack comes only from flooring kW, rounding rebound up, and
  the 0.01 K settle margin.

### D165 — Cells: data-driven bands, two-sided envelopes, a minute-grid frontier

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-THERM-03 session for Olly.
- **Context.** IC-11 cells are season by band, and each Flex cell is a duration/kW staircase.
- **Decision.** G-c over a G-b seam: seasons are the caller's; bands come from a deterministic greedy
  merge of half-hour slots, jointly across seasons, minimising lost kW-minutes, at most 6 bands, with a
  status change always an edge; a pinned grid is accepted. W-b: per start slot, pointwise upper and lower
  envelopes over every season day and every start in the slot, on real Europe/London timestamps. D-b:
  the frontier on a minute grid from 15 to 1440 min, at most 12 points, dropping the least area. kW and
  durations round down, rebound up, limits inward. Unknown for an unknown parameter, short coverage or a
  missing driving temperature; Unavailable only from caller windows.
- **Reason.** "Worst" has two directions (hot and loaded breaks the inlet limit; cool and light leaves the
  least power to shed); fixed bands hide the evening cliff.
- **Alternatives considered.** G-a fixed bands, W-a scalar maxima, W-c a design day (invented unless the
  dossier holds one), D-a a fixed duration list.
- **Consequences.** The grid and the envelopes enter `input_hash`.

### D164 — The power curve is the WP-THERM-04 C-form only; a Carnot front end is a follow-up

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-THERM-03 session for Olly.
- **Context.** COP against load and ambient. The advisor first proposed the C-form with an optional
  Carnot front end; D168 then advised no Carnot. The tie-break kept the C-form only.
- **Decision.** Plant power is the C1/C2/C3 form shared with WP-THERM-04 and SITE-01 (D81), per regime
  at T*, on the heat-extracted basis. Its validity is checked on each slot's box (`P > 0`, `dP/dx > 0`
  at the corners, `dP/dT >= 0` at both load ends, `b4 >= 0`, both regimes where a slot straddles T*);
  a failure is `Unknown` with the reason, never clipped. **Follow-up:** a Carnot-fraction front end
  compiling to C3 coefficients, triggered by the first site that offers only nameplate data, derating kW
  by twice the fit residual and inflating rebound by it, citing BIPM SI Brochure 9th ed. v4.01 section
  2.3.1 (https://www.bipm.org/documents/20126/41483022/SI-Brochure-9-EN.pdf, sha256 `5442eea2…7e02c`)
  for the Kelvin offset. No Kelvin constant or Carnot code ships now.
- **Reason.** `dP/dx` is affine, so corner checks prove the sign over a slot; coherence with the
  baseline model; no site holds nameplate data.
- **Alternatives considered.** Carnot alone (breaks coefficient identity with WP-THERM-04), a
  manufacturer bi-quadratic (ten coefficients nobody holds).
- **Consequences.** A nameplate-only site cannot be modelled until the follow-up lands.

### D163 — Model form: chilled-water family, two thermal nodes, an electrical-power cap

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-THERM-03 session for Olly.
- **Context.** WP-THERM-03 is built clean-room (ADR-D7 option (c); WP-THERM-01 no-go): no ThermaFlex
  code, document or parameter.
- **Decision.** T-a: one chiller type (air-cooled, or water-cooled with towers) serving CRAH units, with
  optional free cooling; DX/CRAC, rear-door and direct liquid cooling are WP-THERM-10. M-b: a two-node
  linear model (loop and hall) in deviations about the baseline, solved exactly per minute. E-a: the event
  caps electrical power `K` kW below the baseline; the setpoint band is a state constraint, not the
  actuator. Envelope leakage is omitted (conservative).
- **Reason.** The declared quantity is the controlled one; one node cannot hold the setpoint band; the
  two-node system is monotone in the deficit, so a worst case is provable.
- **Alternatives considered.** T-b a narrower family, T-c a black box, M-a one node, M-c transport delay,
  E-b a COP-derived kW, E-c setpoint reset as the actuator (a decaying kW).
- **Consequences.** The `FlexProducer` seam (`answer`, `rebound`) is where a future interface-level
  adapter would plug in and be conformance-tested against the same IC-11 output (note 35). Nothing is
  built for it.

### D162 — Slurm adapter reconciliation: a node basis, nested rungs per half-hour, one ramp per class, a computed cap, a policy-fact refusal

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-08). Executed for Olly.
  It is not the owner's own words. Two executor choices are marked below.
- **Decision.** A Fable 5.2 chair reconciled D160 and D161 where they conflicted.
  Everything else in those rulings stands.
  - (C1) **kW is counted on nodes.** A node counts for class C in half-hour h only if every RUNNING job
    on it is in C and counted in h. kW is floored once per (class, h, hold).
  - (C2) **Curves and rungs.** `K_c,h(d)` sums the nodes whose jobs all have `p_j ≤ t_h` and
    `e_j ≥ t_h + 30 min + d`, with `e_j` clamped to a future MAINT reservation.
    - Each distinct cleaned curve is a rung envelope, `<class_id>.r<kW, 6 digits>.<8+ hex of its points>`.
    - Half-hour h offers every rung whose step vector is at or below its own at every hold.
    - A class with two or more rungs has the exclusion group `xg.<class_id>`.
  - (C3) **When a job becomes preemptible.** `p_j` is `preemptable_time`, else `start_time` plus the
    QOS `preempt.exempt_time`, else plus the policy's cluster exempt time. An unknown `p_j` withholds the
    job's nodes.
  - (C4) **One ramp per class.** It is the maximum over every job of the class admitted in the validity.
    Holds the ramp cannot finish inside are dropped before rung identity.
  - (C5) **The cap.** It is the policy cap if stated and lower, else the largest summed first-point kW
    over the half-hours.
  - (C6) **Merit order.** The policy's class list, then other classes by id. Within a class, rungs go in
    descending step vector, then by id.
  - (C7) **A new code, `POLICY_FACT_MISSING`.** A class-level miss skips the class. A declaration-level
    miss (horizon, floor) refuses. If every class is skipped, the refusal carries the first code present
    in this order: `POLICY_FACT_MISSING`, `POWER_BASIS_MISSING`, `UNMAPPABLE_WORKLOAD`,
    `NO_FLEXIBLE_WORKLOAD`.
  - (C8) **Ids and labels.** A Slurm name is encoded injectively: every code point outside `[A-Za-z0-9-]`
    becomes `:` plus 6 hex digits. The class id is `[SYNTHETIC-]slurm.<partition>.<qos>.<outcome>`. The
    operator label is the QOS name verbatim, so the IC-9 synthetic-label rule binds QOS names.
  - (C9) **Residuals and node exclusions.**
    - A suspend class uses the suspended residual; requeue and cancel use the idle one.
    - A mixed node is withheld and ledgered.
    - A node in a MAINT reservation active at capture is excluded.
    - Multi-node jobs count node by node.
    - "Offered at start" semantics are an IC-9 1.1 candidate.
    - The basis stays `DeclaredBasis`.
    - A half-hour with no rung is an `Unavailable` override, never `Unknown`.
  - **Executor choices (not Fable rulings).**
    - The no-rung override's reason code is `workload_protected`, with the reason "no eligible
      workload".
    - The blackout circularity is resolved this way: a half-hour is `maintenance` if no class has
      positive kW there and a MAINT reservation touches a contributing node. Since every half-hour then
      carries an override, `valid_until = valid_from + horizon`.
- **Reason.**
  - Rulings S3 and S4 counted kW per job and per class differently, and both left half-hours undefined.
    The chair fixed one basis so that every number is reproducible from the state and the ledger.
  - Nested rungs let a consumer call across a decay step without the adapter inventing a curve.
- **Alternatives considered.**
  - Per-job kW, rejected because jobs share nodes.
  - One curve per class at capture, rejected because it ignores job ends.
  - A recurring template, rejected because a snapshot evidences no pattern.
  - Clamping the horizon, rejected because it would hide a policy error.
- **Consequences.** `gridq.datacentre.orchestrators.slurm` implements C1 to C9, and
  `tests/test_orchestrator_adapters.py` checks them by hand in `test_reference_kw_ramp_and_maintenance_clamp`.

### D161 — Time and response for the Slurm adapter: notice from the policy, ramp from grace, a dated horizon

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-08). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  - **Notice.** `min_notice_s` is the policy's and is required.
  - **Ramp.** For requeue and cancel, `ramp_s` is the largest grace time (QOS `limits.grace_time` or
    partition `grace_time`) plus the policy's `stop_settle_s`. For suspend it is `stop_settle_s` alone.
    Partition `suspend_time` and `timeouts` are never used.
  - **Job end.** `e_j` is `end_time`, else `start_time + time_limit`. An infinite limit is unbounded.
    Nothing derivable is `AMBIGUOUS_STATE`. MAINT reservations clamp `e_j` and produce `maintenance`
    overrides.
  - **Validity.** It starts at capture rounded up to the half-hour and runs for the policy
    `horizon_min`. The horizon must be a multiple of 30 from 30 to 1,440; any other value is invalid
    and never clamped.
  - **Template and supersedes.** The template is a single `Unknown` cell. Every half-hour is a dated
    override. `supersedes` comes from the policy.
- **Reason.**
  - Slurm states grace and job ends but not the operator's notice or power settling, so those come
    from the policy.
  - A snapshot is evidence for a dated window only.
- **Alternatives considered.**
  - `suspend_time` as the ramp, rejected because it is power-saving, not preemption.
  - A fixed notice.
  - A recurring weekly template.
- **Consequences.** `SlurmPolicy.horizon_min`, `SlurmClassTerms.min_notice_s` and `stop_settle_s`; the
  ramp test and the grace-time mutant.

### D160 — Slurm workload classes and kW: (partition, QOS, outcome), measured node power less a stated residual

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-08). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  - **Classes.** A class is (partition, QOS, outcome).
  - **Effective mode.**
    - Under `preempt/qos`, the mode is the QOS `preempt.mode`, and the QOS must appear in another QOS's
      `preempt.list`.
    - Under `preempt/partition_prio`, it is the partition `preempt_mode`.
    - An empty mode or `CLUSTER_GLOBAL` takes the policy's cluster mode.
    - `GANG`, `WITHIN` and `PRIORITY` are modifiers. `OFF` and `DISABLED` protect the work. Two actions
      in one mode are `UNMAPPABLE_WORKLOAD`.
  - **Mapping to IC-9.**
    - SUSPEND is deferred and returns later.
    - REQUEUE of a requeueable job is deferred and returns later.
    - Other REQUEUE, and CANCEL, are dropped, with no return only if the policy says so, else `Unknown`.
  - **Running jobs.** Only `job_state == ["RUNNING"]` counts.
  - **Measured power.** `current_watts` must be set, positive and collected within the policy's
    `max_power_sample_age_s`. Otherwise the node is withheld. Power is never estimated from GPU counts.
  - **Shed and kW.** Shed is `current_watts` minus the partition's stated residual. The idle residual
    may instead come from the policy's opt-in measured idle twin. kW is `floor(Σ shed W / 1000)`.
  - **Curve.** Holds are the policy's, Pareto-cleaned. There is no cross-class exclusion. The retained
    floor is required.
- **Reason.**
  - QOS and partition preemption are Slurm's documented mechanisms (https://slurm.schedmd.com/preempt.html),
    and they decide what happens to the work, which fixes the IC-9 workload action.
  - Measured node power is the only kW Slurm states.
- **Alternatives considered.**
  - Per-job classes.
  - TRES-based power estimates, which would be invented data.
  - A default residual, which would be an invented number.
- **Consequences.** `SlurmPolicy` and `SlurmClassTerms`, and the refusal tests for unmappable modes,
  unmeasured or stale power and zero offers.

### D159 — The adapter pattern: a sealed declare, refusals as values, a derivation ledger, labelled state documents

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-08). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  - **Package and registry.** The code lives in `gridq.datacentre.orchestrators`, a WS-SITE plug-in
    package that touches no CORE path. It holds a local `Registry` keyed by API id, and adapters
    register under the package owner, never `CORE_OWNER`.
  - **A sealed declare.** `SealedAdapter.declare` cannot be overridden. It checks the API, version,
    labels, site and operator, then calls the subclass's `_map`, seals the ledger, fills
    `resolved_hash` with the contract's own function, and validates with the frozen IC-9 model.
  - **Outcomes.** It returns `Declared | Refused`, never raising for an unmappable state.
    `RefusalCode` is closed. A class that cannot be mapped is skipped with its reason. Zero surviving
    classes is a `Refused`.
  - **The ledger.** Every number comes from a `LedgerBuilder` with its rule, exact value, rounding
    direction and the JSON pointers it read. A bare number is `UNTRACED_VALUE`.
  - **State documents.** A `StateDocument` holds the raw API bodies, a `recorded` or `synthetic` label,
    the API doc URL and `captured_at`, which is also `issued_at`, since adapters read no clock.
  - **Record class.** Synthetic plus synthetic gives `synthetic_fixture`. A recording plus an operator
    policy gives `registered`. Anything else is `LABEL_MISMATCH`.
  - **Fixtures.** Every committed fixture is synthetic, and a test asserts it. The registered branch is
    tested only with in-test documents.
- **Reason.**
  - With these rules an adapter can only hand back what the frozen contract accepted, with every number
    traced.
  - A refusal is data a consumer can act on.
- **Alternatives considered.**
  - A plain ABC with an overridable declare.
  - Exceptions for refusals.
  - Provenance inside the declaration, which IC-9 is frozen against.
  - Registering in a CORE registry.
- **Consequences.**
  - The modules `adapter`, `ledger`, `outcome`, `policy`, `registry`, `state` and `builtin`.
  - The sealed, untraced, contract-rejected and registry tests.
  - An AST test that bans network, subprocess, clock and `open` calls.

### D158 — The reference orchestrator is Slurm, read from slurmrestd v0.0.46 responses

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-08). Executed for Olly.
  It is not the owner's own words.
- **Decision.** The reference adapter reads Slurm through the bodies of five slurmrestd GETs:
  - `/slurm/v0.0.46/jobs/`;
  - `/slurm/v0.0.46/nodes/`;
  - `/slurm/v0.0.46/partitions/`;
  - `/slurm/v0.0.46/reservations/`;
  - `/slurmdb/v0.0.46/qos/`.

  The shape is the one Slurm 26.11 documents for data parser v0.0.46
  (https://slurm.schedmd.com/archive/slurm-26.11.0/rest_api.html).
- **Reason.**
  - Slurm is a public, versioned API that states node power (`energy.current_watts`), preemption classes
    and job windows.
  - It is the scheduler most GPU campuses run under a Conductor-type orchestrator.
- **Alternatives considered.**
  - Kubernetes: it has no power field and preemption always deletes. It is the best second adapter.
  - Ray: it has no preemption classes.
  - Run:ai and Base Command: their APIs are proprietary.
  - Kueue alone: it handles admission only.
- **Consequences.**
  - `gridq.datacentre.orchestrators.slurm`.
  - Synthetic fixtures in `tests/fixtures/orchestrators/`.
  - A Kubernetes adapter is left open.

### D157 — The SL 2 review is computed, with a claim ceiling, a second witness and a sealed slot for the purchased text

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-EDGE-02). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option D of four: (A) a markdown table, (B) typed per-SR records, (C) typed records with derived
  dispositions, (D) C plus a claim ceiling, a second witness and a sealed upgrade slot.
  1. The catalogue is the 51 SR identifiers and titles from ISA's published table of contents of ISA-62443-3-3
     (99.03.03)-2013, cited by URL and SHA-256 (`gridq.edge_design.public_sources`). No model has a field for
     requirement text, and a title must equal the catalogue's.
  2. `ReviewStatus` sits on the root: `certified: Literal[False]`, `conformity_assessment: Literal[False]`,
     `sl_target: Literal[2]`, `sl_achieved: Unknown`, `basis: Literal["public_sr_id_and_title"]` and a sealed
     `full_text_review: Unknown`. Every SR's `sl2_re_applicability` is `Unknown`: which requirement enhancements
     apply at SL 2 is paid text.
  3. The claim ceiling, enforced on construction: `title_level_mapped` needs a design element and a test and
     carries only the global gap; `partially_mapped` needs an element, a test and a named gap; `deferred_to_wp`
     needs a work package that owns a named gap; `host_responsibility` needs a data ask and a site-fact gap.
  4. The second witness: for every mapped SR that NIST CSF 1.1 cites (47 of 51; not 3.6, 3.7, 7.5, 7.7), the CSF
     subcategories of the named elements must meet NIST's own subcategories for that SR.
  5. Gaps are records (`GAP-62443-nn`, kind, owner, closes-when). `GAP-62443-00` (text unavailable) is on every
     SR and is owned by the person-only purchase of IEC 62443-3-3 and 3-2.
  6. `DISCLAIMER` is rendered at the top of the document, at the head of the review and above each FR section.
- **Reason (Fable).** Over-claiming becomes a type error or a failing test, not an editorial slip, and acceptance
  2 ("reviewed, gaps listed") becomes a test output that WP-EDGE-03 to -08 and WP-GRC-10 inherit.
- **Alternatives considered.** (A) drifts and the disclaimer can be deleted. (B) dispositions are hand-asserted,
  so "addressed" can be claimed with nothing behind it. (C) the vocabulary can still overstate.
- **Consequences.** No SL 2 enhancement judgement, no SL-A or SL-C claim; SR 3.6, 3.7, 7.5 and 7.7 have no second
  witness; a test reference proves a test exists, not that it is adequate (WP-GRC-10). Result: 13 SRs
  title-level mapped, 20 partially mapped, 17 deferred to a work package, 1 host responsibility; 23 gaps.
  Executor's choices inside the decision: the controls behind an SR are derived from the control table, so the
  two tables cannot drift; only the four dispositions in use are in the vocabulary (no SR is `gap`,
  `oem_responsibility`, `not_applicable` or `undetermined`); test references are resolved by parsing the test
  file; the disclaimer appears nine times (top, review head, seven FRs), not eight; Fable's brief said NIST cites
  49 SRs, the parsed PDF shows 47. Tests: `test_claim_ceiling`, `test_csf_second_witness`,
  `test_review_cannot_claim_certification`, `test_disclaimer_in_doc_top_review_and_every_fr`,
  `test_every_gap_is_referenced_and_owned`, `test_wp_refs_exist_in_roadmap`, `test_test_refs_resolve`.

### D156 — The data diode is the default towards the BMS; L3 is a one-way chiller band offer the host's BMS adopts

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-EDGE-02). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option D, a hybrid of B and C. `BmsConduitMode` per site, the reference design using
  `diode_read_only`:
  - `no_bms_conduit`: nothing reaches the BMS; L2 at most.
  - `diode_read_only` (reference): BMS telemetry out through a data diode (C7); no chiller authority; L2 at most.
  - `diode_with_band_offer`: two opposed one-way paths, telemetry out (C7) and a chiller band offer in (C7o), the
    inbound path carrying one message type into a host-owned register; the host's BMS logic adopts or ignores
    it; an expired offer is dropped; L3.
  - `brokered_bidirectional`: a firewalled protocol-break conduit writing allow-listed chiller points (C7w); L3;
    a listed gap (GAP-62443-20).
  - An unknown mode permits L2 at most. A non-default mode needs a deviation reason; an L3 mode needs the B1-09
    safety case and the host's sign-off, each by hash (`Unknown` refused). A closed-loop chiller band at a site
    whose mode has no chiller path is refused `outside_write_scope` (`query.design_write_scope_reasons`,
    composed with IC-5's `check_setpoint_schedule`, which is unchanged).
- **Reason (Fable).** Spec B1-07 says "unidirectional gateway to the facility BMS where the customer requires
  it. No data centre will allow BMS writes without it." A diode as NIST SP 800-82r3 E.1.2 describes it cannot
  carry a write in, so the second sentence holds only if "it" means the zone and conduit design as a whole; the
  roadmap says "option" and the spec "where the customer requires it". The band offer applies spec section 8
  (Neura issues envelopes, the owner's controller executes) to cooling as it already applies to the battery, and
  silence is the safe state. The spec is not reworded; the design document states the reading.
- **Alternatives considered.** (A) diode only: L3 unreachable at exactly the sites that insist on a diode,
  defeating ADR-D2. (B) a Neura-driven write still reaches the control point, which hosts refuse. (C) no declared
  inbound conduit, so acceptance 1 cannot check it.
- **Consequences.** The band-offer mode is not hardware-incapable of the reverse direction end to end (an
  application-level loop) and the design says so in its caveat. Whether any host accepts it is a data ask. The
  offer's message schema, the registers and any product are out of scope. Executor's choices inside the
  decision: the edge-to-adapter conduit differs by mode (C6 read-only, C6o band offer only, C6w chiller points),
  and an invariant (`adapter_relays_only_what_reaches_the_bms`) makes what enters the adapter equal what it may
  pass on, so the adapter is never where a write widens or dies. Tests: `test_bms_mode_is_what_it_says`,
  `test_adapter_relays_only_what_reaches_the_bms`, `test_a_chiller_band_is_outside_write_scope_without_an_l3_mode`,
  `test_l3_mode_needs_safety_case_and_signoff`, `test_max_stage_by_mode`.

### D155 — Zones by owner and Purdue level, typed conduits with a three-part direction, FRs derived from attributes

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-EDGE-02). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option D of four: (A) one coarse OT zone, (B) one zone per Purdue level, (C) zones per owner and
  function as a list, (D) C's zones as a typed graph with the "must be impossible" rules proved over it.
  1. Twelve zones, each with a Purdue level range and a responsible organisation: `neura_cloud`, `remote_ops`,
     `market_partner` (off site); `site_dmz`, `edge_control`, `thermaflex_adapter`, `bess_oem`,
     `site_substation`, `dno_anm`, `facility_cooling`, `metering`, `facility_power_ro` (on site). The DMZ, the
     edge and the ThermaFlex adapter break protocol (terminate and re-originate, never route). The substation's
     and the meters' owners are unknown, with data asks.
  2. A conduit's direction is three fields: `initiator`, `data_flow` and `authority` (`NoWrite` with its reason
     or `WriteScope` with the writer, point classes, lowest IC-5 stage, tighten-only and safety-case flag).
     Protocols, payloads and controls are required and non-empty; a conduit missing any cannot be built
     (acceptance 1). Each conduit names the IC-5 trigger raised when it is lost.
  3. A closed control vocabulary, each control mapped to FRs, SR titles and CSF subcategories. The FRs a conduit
     needs are derived, never typed: FR 5 and 6 always, FR 1 and 3 over IP, FR 2 and 3 for a write, FR 1, 3, 4
     and 7 across the site boundary; the validator refuses a conduit whose controls do not cover them.
  4. Named invariants, checked in every BMS mode on release: adjacent Purdue levels only; the enterprise reaches
     only the DMZ; the boundary is crossed outbound only; no off-site zone reaches a site zone except through a
     protocol break; ANM only between the DNO and the edge; writes only into zones that admit the point class;
     the writer opens the session; IC-5 stages per point class; authority matches controls; read-only context is
     one way; humans only by brokered MFA sessions ending at the DMZ; code only signed; no wireless; no
     person-to-person channel; every comms-loss trigger has a conduit; every zone is reached; the BMS mode is
     what it says; the adapter relays exactly what reaches the BMS.
- **Reason (Fable).** One OT zone cannot express "the UPS is never writable" or "ANM never reaches the cloud";
  per-level zones merge the OEM PPC, the host BMS and the DNO RTU, which differ in owner, trust and change
  control; a plain list leaves the bans as prose.
- **Alternatives considered.** As above.
- **Consequences.** Real site networks stay unknown; register-level allow-lists are WP-EDGE-03's; the FR mapping
  is Neura's vocabulary, not requirement text. Executor's choices inside the decision: the remote-access
  conduit (C11) is opened from the site to the broker and ends at the DMZ, and C2 carries no human session; the
  partner link (C10) is opened from the site, and whether it exists at all is a data ask (the partner may reach
  the site only through Neura's cloud); `device_binding` gives FR 1 and `protocol_dpi` FR 3 on southbound IP
  fieldbus conduits; the meter conduit (C8) uses Modbus TCP as the design's requirement of operational meters,
  and how revenue meters are read is a data ask; below L2 the edge writes nothing (IC-5), so whether the DNO
  signal also reaches the PPC directly is a data ask for WP-EDGE-05, not a conduit. Tests:
  `test_conduit_without_protocols_direction_or_controls_is_refused`, `test_controls_cover_required_frs`,
  `test_conduit_missing_an_fr_is_refused`, one test per invariant in `tests/edge_design/test_edge_design_invariants.py`.

### D154 — The zone and conduit design is a typed graph with invariants and a query API, released as hashed JSON with a generated document

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-EDGE-02). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option D of four: (A) YAML plus a hand-written document, (B) a frozen contract in
  `gridq.contracts`, (C) a typed model with committed JSON and a generated document, (D) C plus named invariants
  and a query API.
  - Its own package, `gridq.edge_design`, authored in code and built on `gridq.contracts._base`
    (`ContractModel`, `Unknown`, `NotApplicable`, `content_hash`).
  - Released as `src/gridq/edge_design/release/edge_zone_conduit_design.v1.0.0.json` (canonical JSON) and
    `docs/design/edge_zone_conduit_design.v1.0.0.md` (generated, with a Mermaid diagram by Purdue level), both
    byte-checked by test and by `python -m gridq.edge_design --check`; `DESIGN_HASH` pinned.
  - A query API for later packages: `write_bindings(stage, mode)`, `conduits_between`, `conduits_for_trigger`,
    `controls_for_sr`, `effective_stage`, `design_write_scope_reasons`.
  - A site binds by hash: `SiteZoneConduitInstance` holds every site fact as a value or `Unknown` with its data
    ask; `unbound_instance(site_id)` is all unknown and permits L2 at most.
- **Reason (Fable).** WP-EDGE-03 to -08 and WP-GRC-10 get executable rules, not prose: the allow-list must be a
  subset of `write_bindings(stage, mode)`, the pen-test scope is the conduit set, the fault-injection list is
  the trigger map.
- **Alternatives considered.** (A) the document drifts from the data and nothing is typed. (B) this is a design
  release, not a wire seam between two parties; a contract adds archive and golden obligations to a design that
  will change when the purchased standard and real site networks arrive. (C) a passive record: every later
  package would re-derive the rules.
- **Consequences.** No JSON Schema archive, contract tag or `gridq.contracts --check` entry (promote it only if
  an outside party consumes it). No register allow-list, TLS or key design, runtime enforcement or real site
  instance. A change to the design is a new version, never a re-pin. Tests: `test_release_json_is_current`,
  `test_design_doc_is_current`, `test_design_hash_is_pinned`, `test_cli_check_passes`.

### D153 — IC-14 1.0.0 as one contract: four schemas, one block per series, the method embedded, clocks kept, fill and events deferred

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-TEL-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** A Fable 5.2 chair reconciled D148 to D152 into one IC-14 1.0.0 and is binding
  over them where they differ.
  - **Four stems:** `ic14_series_descriptor`, `ic14_telemetry_batch`, `ic14_curated_series`,
    `ic14_dq_annotation` (`.v1.0.0`), tag `contracts/ic14-telemetry-series/v1.0.0`. Every record
    carries one head: contract, version, kind, `tenant`, `site_id`, `series_id`, `data_class`,
    `record_class`.
  - **A raw batch is one block of one series.** The chain key is `(site_id, sender_id, series_id,
    stream_epoch)`; unobserved slots are `GapRun(not_recorded)`; a jump in origin needs a new epoch.
    Samples and gaps are one ordered `segments` tuple of `ValueRun` and `GapRun`.
  - **`SeriesDescriptor` is its own hashed schema,** bound by `descriptor_hash`. A descriptor change
    forces a new `stream_epoch`; otherwise the tracker reports `profile_mismatch`.
  - **`CuratedSeries` embeds its `CurationMethod` in full** (`method_id`, `aggregation`
    mean|min|max|last, `min_usable_ms`, `usable`, `applied_findings`, `rounding` iff mean). A curated
    window is one block at the curated resolution. `sum` and `time_integral` are cut from 1.0.0.
  - **One overlay, `DqAnnotation`,** with `disposition` asserted|withdrawn. `rejected_ms` is the
    usable time overlapped by an effective annotation whose kind the method applies.
  - **One freshness reader, `age_at`:** fresh, stale, lost, future_dated, clock_untrusted, no_data;
    value and quality only when fresh.
  - **Units confirmed:** W, Wh, millidegrees C. Coarser units only through converters with a stated side.
  - **An unsynchronised clock keeps the batch's values.** `curate` treats every slot of it as a
    `clock_untrusted` gap and `age_at` reports `clock_untrusted`.
  - **Curated derives from raw only.** `resample` refuses curated input.
  - **Deferred to 1.1.0:** fill and `substituted` (`from_opcua(0x40910000)` raises in 1.0.0),
    event-driven timing, `sum`/`time_integral`/`delta`. Later: `emitted_under`, multi-series bundles.
  - **OPC UA codes were verified** against the OPC Foundation `StatusCode.csv` before the tag; all
    sixteen match.
- **Reason.**
  - One hash naming one series over one span is what ledger, verifier and NESO files cite; a
    multi-series batch would make each series' hash depend on its neighbours and tangle tenancy.
  - A verifier must recompute from IC-14 alone, so the method travels with the record rather than as
    a hash of parameters held elsewhere.
  - Destroying measurements at the edge because a clock was unsynchronised would be irreversible.
  - `emitted_under` is circular if it names the carrying frame and weak if it names any other.
- **Alternatives considered.** A `stream_id` grouping several series; separate sample and gap lists;
  a `MethodRef` with a `params_hash`; a three-state freshness result; dropping values of an
  unsynchronised block; shipping fill and event timing now. Each was rejected for the reason above.
- **Consequences.** `src/gridq/contracts/telemetry_series.py` is built to this field list, with the
  readers listed in the contracts CHANGELOG. Tests: `tests/contracts/test_ic14_telemetry_series.py`.

### D152 — IC-14 tenancy: a `TenantKey` in every record and its hash, a three-layer guard, and readers that need a `ReadDecision`

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-TEL-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option (c), tightened.
  - `TenantKey{org_id, org_kind customer|neura, site_id}`; `canonical()` is `org/site`.
  - `series_id` is `<org>/<site>/<point_key>`; a prefix mismatch is refused.
  - Synthetic is three-way: `record_class` synthetic_fixture iff the site and org ids start `SYNTHETIC-`.
  - `data_class` is required (`neura_operational`, `customer_commercial`, `customer_personal`);
    `neura` iff `neura_operational`; curated takes the strictest of its inputs.
  - `read_guard(claims, key)` returns a `ReadDecision` (allowed with the IC-13 `ScopeGrant`, or denied)
    and never raises. Data-returning readers take a `ReadDecision` and raise `TenantReadRefused` on a
    denial or a record of another tenant. Pure transforms raise `MixedTenants` on more than one key.
  - The key and `data_class` are inside the content hash.
- **Reason.**
  - Segregation is checkable at three independent layers (the id string, the record validator, the
    reader capability), so a cross-tenant read is refused under test from `gridq.contracts` alone.
  - It reuses IC-13 unchanged: `ResourceKind.TELEMETRY` is already customer data, so every read is
    auditable with no IC-13 bump.
  - A capability argument leaves no unguarded code path, which a convention would.
  - Hashing the key means a record moved between tenants cannot keep its hash.
- **Alternatives considered.** `org_id` alone (segregation not visible, transforms can mix tenants);
  `site_id` with a registry (a leaf contract cannot hold one, site-to-tenant is not a function at a
  colocation site, and a registry edit would silently re-tenant raw data); an opaque or HMAC token
  (needs a secret, and IC-13 needs the real org id); `Unknown` for `data_class` (it would have to be
  read as personal anyway).
- **Consequences.** Tests: the tenancy section of `test_ic14_telemetry_series.py`, including the
  IC-13 `audit_access` round trip over six claim fixtures and the forged-decision test.

### D151 — IC-14 tiers: a chained raw tier, a data-quality overlay, superseding curated revisions; IC-5 1.0.0 left untouched

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-TEL-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Tier option (a) plus a separate overlay; IC-5 option (A), modelled on `AnmEvent`.
  - **Raw** `TelemetryBatch` carries its own chain: `seq`, `prev_batch_hash` (genesis iff seq 0),
    `prior_epoch_head` (NotApplicable only in epoch 0, Unknown for lost state). `verify_batch_chain`
    checks it; `track` reports in_order, duplicate, gap, late_fill, fork, stale_epoch,
    profile_mismatch or new_epoch, and refuses only a fork.
  - **DQ overlay** `DqAnnotation` cites raw by `RawSpan`.
  - **Curated** `CuratedSeries` declares its `inputs` (raw spans), its `annotations` and a `revision`;
    a new revision `supersedes` the old by hash for a reason (late_raw, annotation_change,
    method_change). `verify_curated` recomputes byte for byte; `check_supersession` and
    `current_revision` police the chain.
  - **IC-5 is not changed.** `TelemetryBatch` has `site_id` and a fresh `kind`, so a later IC-5 minor
    can add it to `EdgeToCloudBody` as one additive member. `Ic5Binding{link_profile_hash,
    link_epoch}` records the link it rode. `telemetry_series` does not import `edge_cloud`.
- **Reason.**
  - `AnmEvent` is the precedent: an edge-to-cloud body with its own chain, sent when the link allows.
  - The IC-5 frame chain is per link epoch and wiped on reconnect; a seven-day buffer spans several
    link epochs, so the evidence chain must live in the record.
  - At 20 Hz a point is 1,728,000 samples a day; on the control chain a telemetry fork would refuse
    the control epoch.
  - An import of `edge_cloud` would become circular once the IC-5 minor imports `telemetry_series`.
  - Supersession keeps evidence stable: a citation of revision 0 stays verifiable.
- **Alternatives considered.** Three tiers with a validated raw-resolution tier (duplicates 20 Hz
  volume); lineage pointers only (cannot recompute); DQ folded into curated (a monitor upgrade would
  rewrite aggregates invisibly); IC-5 1.1.0 now (reopens a contract frozen today); heartbeats only; a
  Merkle root of inputs; a chain keyed on `link_epoch`.
- **Consequences.** IC-5 needs a minor before a batch can ride an IC-5 frame. The IC-5 shape test in
  `test_ic14_telemetry_series.py` proves a throwaway union with `TelemetryBatch` validates.

### D150 — IC-14 quality: values and gaps are different types, OPC UA-mapped enums, exact coverage and a stated threshold

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-TEL-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Two closed enums mapped injectively to OPC UA status codes (`QUALITY_MAP`):
  `ValueQuality` (good, local_override, inaccurate, out_of_range, subnormal, held; worst-of order) and
  `GapReason` (comms_lost, sensor_failure, device_failure, out_of_service, configuration_error,
  clock_untrusted, buffer_lost, not_recorded, insufficient_coverage; all Bad). A gap is a run with a
  reason and no value field. Raw refuses `insufficient_coverage`. A curated interval closes exactly
  (`usable_ms + rejected_ms + gap_ms == period`); below the method's `min_usable_ms` it is a
  `CuratedGap`, never a value. `TRIGGER_GAP_REASON` maps IC-5's trigger codes to gap reasons.
- **Reason.**
  - Splitting the types makes "zero for missing" a schema error, which the stdlib gateway can enforce
    from the JSON Schema alone. This is acceptance criterion 2 by construction.
  - OPC UA is the edge's northbound, so an injective map costs nothing there.
  - Exact integer-ms tiling follows the house rule that every closure is an equality.
  - Run-length gaps make a seven-day outage one record with a stable hash.
- **Alternatives considered.** The raw OPC UA or IEC 61850 code sets (hundreds of codes, no
  `insufficient_coverage`); a home-made enum with no mapping (every adapter invents one); a sample with
  an `Unknown` value (a numeric-shaped field that invites `or 0`); implicit gaps; coverage as a
  percentage; a default coverage threshold (no published source sets one, so the method states it);
  scaling partial energy up (estimation presented as measurement).
- **Consequences.** Property tests P2 to P8 in `test_ic14_telemetry_series.py`.

### D149 — IC-14 time base: fixed UTC blocks of integer slots, a clock statement per block, age from the data's own instant

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-TEL-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  - A block has a UTC `origin` (whole ms, a multiple of the block span) and a closed resolution class:
    `event_20hz` (50 ms, 60 s block), `operational_1s` (1 s, 60 s), `standard_1min` (60 s, 30 min),
    `settlement_30min` (30 min, one UTC day of 48). Raw uses the first three; curated the last three,
    strictly coarser than its source.
  - Samples are integer slots; `slot_instant` gives the instant. Samples and gaps must tile every slot.
  - `ClockStatement` per block: `stamped_by`, `clock_state`, `clock_offset_bound_ms` (an integer when
    synchronised), `boot_id`, monotonic origin and end, `receipt_lag_ms`. `clock_drift_ms` is exact.
  - Settlement periods are a reader (`settlement_period_of`, 46/48/50 by Europe/London), never on the
    wire.
  - `FreshnessPolicy{stale_after_ms, lost_after_ms}` on the descriptor; `age_at` measures from the
    data's own instant against the caller's `at`, equality reading as the worse state.
- **Reason.**
  - With implicit timestamps and the partition rule, a missing sample cannot be silence: it is a
    declared gap or the block is refused.
  - 50 ms, 1 s, 60 s, 30 min and one day all nest, so every settlement half-hour is a whole number of
    blocks at every tier.
  - Fixed spans give one encoding per series, so two producers hash identically (D85's one canonical
    encoding).
  - Age from receipt would read a relayed week-old buffer as fresh.
- **Alternatives considered.** A `UtcInstant` per sample (bulky at 20 Hz, silence representable);
  epoch-ms per sample (unstated holes); any integer period (many encodings of one series); per-sample
  clock trust; local time or settlement numbers on the wire; age from receipt.
- **Consequences.** Property tests P1, P9 and P14; IC-3's `RESOLUTION_MIN` is asserted equal to the
  settlement period at import.

### D148 — IC-14 point naming and units: a closed `PointName`, one integer unit per quantity

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-TEL-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Naming option (c), units option (1).
  - `PointName{asset_kind, asset_id, quantity, role, aspect}`; `point_key` is the five parts joined by
    dots, and `parse_point_key` reverses it. Asset kinds, measurement roles and aspects are closed enums.
  - `QUANTITY_TABLE` fixes unit, sign and range per quantity: active power W (signed, positive into
    the asset from the grid side), reactive var, apparent VA, energy import and export Wh (cumulative),
    frequency mHz, voltage mV, current mA, state of energy Wh, state of charge basis points,
    temperature millidegrees C. Every value is a JSON integer with |v| <= 2^53-1.
  - Source address, scaling, Brick class, plausible range and meter binding are annotations on the
    descriptor, never identity. Meter roles need a `MeterBinding` (or `Unknown`);
    `require_certified` raises unless the certificate is an `EvidenceRef`.
  - Readers: `counterparts`, `split_import_export_w` and the unit converters, which take a stated
    rounding side when they lose precision.
- **Reason.**
  - Dispatch, ledger, verifier and twin must agree on what a number means without a lookup.
  - Integers keep every closure exact and hashes stable across languages.
  - The units fit the fastest consumers (20 Hz frequency response, 1 s meters, calibration fits), and
    kW, kWh and tenths of a degree are exact multiples, so comparison upward never rounds.
  - Sign by quantity removes the usual source of sign errors, a per-series declaration.
- **Alternatives considered.** IEC 61850 paths (no vocabulary for chillers, UPS or state of energy;
  kept as a source address); Brick or Haystack tags (no closed validation; kept as `brick_class`);
  free strings plus a registry (meaning outside the hash); floats with UCUM (float repr in the hash);
  a mantissa with a per-series exponent (the ×10 scaling fault inside the contract); separate import
  and export power series (doubles 20 Hz volume).
- **Consequences.** Property tests P10 to P12; the descriptor guard tests.

### D147 — Open parameters are slots that list every option; no definition has a default

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation of WP-OPS-11 design choices; adopted by the
  session for Olly that built WP-OPS-11.
- **Context.** Spec §15.3 names three candidate definitions but leaves open the statistic, the window, the
  resolution, coincidence across sites, the gap policy and each definition's basis. Choosing any of them is
  part of the agreement (WP-OPS-02), not of this pack.
- **Decision.** Every open parameter is a `ChoiceSlot` listing each admissible option in canonical order,
  or a `NumberSlot` with no value. No definition carries a default filling. The slots for window,
  resolution, statistic, coincidence, gaps, quality and authority, and the conditions on control
  authority and the dispatch envelope, are shared verbatim by A, B and C. Combinations that cannot be filled
  are listed with their reason (B's committed basis at 1-minute resolution), never dropped. `resolve` needs
  only the slots on the chosen path, and names the filled variant by its hash.
- **Reason.** A default is a recommendation by another name. Shared slots make the three options
  comparable clause by clause.
- **Alternatives considered.** One fully filled formula per definition (rejected: chooses for the
  parties). Free-text parameters (rejected: not recomputable). Per-definition copies of the common slots
  (rejected: they drift apart).
- **Consequences.** A test fails if any candidate's shared slot differs from `SHARED_SLOTS`, or if a model
  gains a default, preference, rank, score or status field.

### D146 — The pack is rendered: grant text quoted in place, the draft agreement written from the typed definitions

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-OPS-11 session for Olly.
- **Context.** The pack must quote what the application and the competition scope say about the MW figure,
  and carry draft agreement text, without recommending a definition or recording one as agreed. The grant
  documents may be quoted but never copied into the repository.
- **Decision.** `docs/ops/demonstrator_mw_options.md` is generated, and a test fails if it differs from the
  render. Grant text lives in a typed register (`mw_grant_text`): each held document by workspace path and
  sha256, each quote as verbatim fragments with its section, each figure with the MW notion it is a figure
  of. A test re-reads each document, checks the hash and finds every fragment (skipped where the file is
  absent). Documents not held, and award-only figures (AW-01, AW-04, AW-09), are owner asks. Internal meeting
  notes are context and never grant text. The draft (`mw_draft`) opens and closes with a DRAFT banner,
  renders A, B and C in spec order with identical clause structure, puts every party, date, reference and
  figure in a typed `[[blank]]`, has unsigned signature blocks, and maps the executed text to three IC-4
  `method_agreement` objects for WP-OPS-02. It creates no evidence object.
- **Reason.** A rendered draft cannot describe a formula other than the one in code, and a hash-checked
  quote cannot drift from its source.
- **Alternatives considered.** A hand-written draft (rejected: drifts from the formulas). Copying the
  application into `docs/` (rejected: the owner's rule). A lexical "fit" score of each quote against each
  definition (rejected: a ranking by another name; the pack lists shared words only).
- **Consequences.** A word walk refuses preference and agreement language in every product (the fixed
  not-agreed statements and verbatim quotes are exempt). Which Q9 version was submitted is an ask: the held
  PDF states ~50MW distributed, the draft .docx states no 50 MW figure.

### D145 — The worked count gives an upper bound from evidenced fields, or the missing field and its ask

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-OPS-11 session for Olly.
- **Context.** Criterion 2 asks for each site's MW under each definition from evidenced dossier fields.
  The dossier holds documents, not telemetry, so no definition's exact value is computable from it, and the
  data rule forbids computing a site MW from a guess.
- **Decision.** Per site and definition, one cell. `UpperBound`: an integer kW every admissible filling stays
  at or below, from evidenced fields only, each term cited and rounded up, with the conditions under which
  the inequality holds and the inputs it leaves out (A: parallel assets plus max(import capacity, off-grid
  assets); B: the recorded batteries' rating). `UnknownInputs`: each missing field with the dossier's asks,
  or a site-scoped ask minted for a term inside an evidenced field (an unrated asset, an unknown operation,
  two agreements). `NotInDossier`: the inputs that will supply it, with contract field and work packages
  (C always; B with no recorded battery). A register ceiling exists only when every site is bounded and no
  dossier-level ask is open. The tracked pack carries tallies only; the per-site file is written only where
  git ignores it.
- **Reason.** A bound is true for every filling the parties might pick, so it states something evidenced
  without choosing a definition. A point estimate would need a guess.
- **Alternatives considered.** A point value under an assumed filling (rejected: chooses and guesses). Only
  "unknown" (rejected: throws away what the documents do establish). Comparing totals with ~50MW (rejected:
  a verdict).
- **Consequences.** A property test evaluates 384 fillings of A on two synthetic series that keep the stated
  conditions and finds none above the bound. With no DPA registered, all 8 real sites are unknown under A
  and B and not in the dossier under C.

### D144 — Each term is bound in a separate table: frozen field, dossier field, or not yet measurable with its roadmap product

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-OPS-11 session for Olly.
- **Context.** Criterion 1 needs each term mapped to a telemetry-contract field, a ledger field or a
  verifier method, or marked not yet measurable with the product that would measure it. NEW-IC-14
  (telemetry) and IC-8 (verifier) are not frozen yet.
- **Decision.** `BindingTable` per definition, apart from the formula and naming it by hash, with exactly one
  binding per term: `FrozenContractField` (JSON pointers into the current, unarchived committed schema,
  checked in the file and the live model, with the unit suffix), `DossierFieldBinding` (a path under the
  evidenced value, with the IC-1 field it carries), or `NotYetMeasurable` (category, expected contract and
  schema prefix, data product, roadmap work packages). The work packages are quoted from a pinned Atlas
  commit, verified by file sha256 and YAML. A tripwire fails the day an `ic14_` or `ic8_` schema or a named
  product package appears; a drift check fails if Atlas HEAD drops a pinned product.
- **Reason.** Re-binding a term when NEW-IC-14 or IC-8 lands must not change the formula hash an
  executed agreement cites.
- **Alternatives considered.** Sources inside the formula tree (rejected: re-binding would change the agreed
  hash). Free-text sources (rejected: not checkable). Inventing an `ic14_` stub now (rejected: pre-empts
  WP-TEL-01).
- **Consequences.** The ledger fields name no contract (the roadmap names none); their product is the
  source. The tests found and fixed two wrong bindings while being written (`/scope` is an object in IC-4;
  `b.asset_enveloped` needed WP-TEL-01, which freezes NEW-IC-14).

### D143 — A candidate definition is a typed expression tree, evaluated exactly

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-OPS-11 session for Olly.
- **Context.** Each definition must be a formula a verifier could recompute: quantity, measurement point,
  averaging period and qualifying conditions.
- **Decision.** `MwDefinition` is a frozen, unit-, grain- and time-checked tree of typed terms, exact
  rational constants and operators (add, subtract, scale, clip, sum over sites, assets or services, time
  aggregate over a local-time window, restrict to named conditions, choose by slot). `evaluate` uses
  `Fraction`, treats a missing sample as a gap and never zero, applies the gap policy, uses three-valued
  logic for conditions, and lists every missing term when the result is unknown. The four KPI keys
  (`quantity`, `measurement_point`, `averaging_period`, `qualifying_conditions`) are derived from the tree.
- **Reason.** A formula the code can evaluate is one a verifier can recompute and a test can pin; prose
  cannot be either.
- **Alternatives considered.** Prose definitions (rejected: not recomputable). A general expression
  language or SQL (rejected: units and grains unchecked). Floats (rejected: hashes and results would vary).
- **Consequences.** No resampling: a series at another resolution is unknown. An empty asset sum is zero; an
  empty site sum has no figure.

### D142 — Site eligibility: P511 per MSID pair, no site test of REG-04's minimum, fixed timeout, verbatim reasons

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation of WP-UW-02 design choices; adopted by the
  session for Olly that built WP-UW-02.
- **Context.** Choices the engine would otherwise make silently.
- **Decision.** (1) P511's band is read per MSID pair on the largest gross half-hourly export
  (`facts:msid_export`, a meter operator's metered data record). The `between` band is always undetermined,
  with the thirty-day adjusted net-export data named as a data request. (2) REG-04's minimum size binds the
  BM Unit, not the site, so the engine does not test site capacity against it. (3) Fixture rows keep the
  real register's verification statuses; only sign-offs are synthetic. (4) Data requests are a de-duplicated
  list per site (`DossierAsk`, sorted by id); owner actions are a separate list. (5) `as_of` is a `date`
  everywhere and crosses to the bridge as ISO text. (6) The bridge timeout is a constant in the rule table
  (`BRIDGE_TIMEOUT_S`), so it enters the table hash. (7) Bridge and evaluator reasons are reported verbatim.
  (8) An unknown contracting-entity customer list is passed to the Atlas evaluator as an absent key, never
  an empty list.
- **Reason (Fable).** Each is a place where a quiet default would turn an unknown into a pass. The row-level
  P511 test is per MSID pair, the minimum binds the unit, and the Atlas rule withholds its provisional pass
  only when the list is absent.
- **Implementation consequences and deviations.** Fable also allowed interval reasoning from the dossier's
  net-export measures: metered peak net export as a lower bound (only with its measure definition and window
  evidenced) and agreed export capacity as an upper bound (only under stated one-MSID-pair and
  within-capacity assumptions). Neither precondition is evidenced in an OPS-01 dossier today, so the engine
  records the net-export field as read with "net export cannot place a P511 band" and asks for the
  per-MSID-pair data instead. Using the bounds once their preconditions are evidenced is a rule-table
  version change. Fable's fallback for REG-04 was an undetermined check reading "aggregation basis not
  stated"; the engine has no such check at all, since there is nothing to test until the row's threshold
  names the quantity it binds.
- **Alternatives considered.** Use net export as the P511 figure (rejected: net, not gross, and not per
  pair). Test site capacity against REG-04 (rejected: wrong quantity). A configurable timeout (rejected:
  run identity would depend on a setting).
- **Consequences.** Today P511-BAND is undetermined for every site. The thirty-day data is a named request.

### D141 — Site eligibility: the dossier, the facts and the run share one as_of

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation of WP-UW-02 design choices; adopted by the
  session for Olly that built WP-UW-02.
- **Context.** The OPS-01 dossier, the per-site eligibility facts and the eligibility run each carry a date.
- **Decision.** All three must be equal. `build_eligibility` raises `EligibilityInputError` before any
  evaluation otherwise, and also when a facts record names a site the dossier does not hold, or when a
  synthetic site or synthetic input record reaches a `registered_only` run.
- **Reason (Fable).** The dossier is a cheap deterministic build, so rebuild it at the run's date. Allowing
  an older one would judge evidence admitted at one date against register validity at another, and an "age
  reported" field would be ignored downstream.
- **Alternatives considered.** Accept an older dossier and report its age; treat the mismatch as an
  undetermined table.
- **Consequences.** A mismatch is a usage error (CLI exit 2), not an undetermined table. The CLI builds the
  dossier at `--as-of`.

### D140 — Site eligibility: the provisional overlay, stand-alone wiring, and what the hash covers

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation of WP-UW-02 design choices; adopted by the
  session for Olly that built WP-UW-02.
- **Context.** Acceptance criterion 4 (provisional is never exempt), how the product is consumed, and run
  identity.
- **Decision.** (i) Overlay: when the loaded rule's status is not `final`, an `eligible` from the Atlas
  evaluator is reported undetermined ("PROVISIONAL RULE, NEVER EXEMPT"), citing WP-GRC-07. Ineligible passes
  through. Any status other than `final`, including an unrecognised one, cannot yield eligible. (ii) The
  engine stands alone: nothing is wired into the UW-01 gate, `gridq run` or the app (WP-UW-04's job). The
  product states its consumption contract: tighten-only, undetermined is not eligible, and it never gates
  `all_streams_enabled_study_v1` (D57). (iii) `SiteEligibilityTable.content_hash` covers `as_of`, the
  admission mode, the dossier, facts and declaration hashes, the rule-table hash, the bridge script's
  sha256, the four Atlas file sha256 values, every row verdict, and the load outcome. Paths, timings and
  host details are excluded.
- **Reason (Fable).** The Atlas loader already asserts that a provisional rule never yields eligible. The
  overlay makes GridQ hold the same line even against a loader that breaks it. Wiring belongs to WP-UW-04,
  and a hash that ignores the register would let two different answers share an identity.
- **Alternatives considered.** Trust the evaluator's status (rejected: one Atlas bug away from a false
  pass); wire into the UW-01 gate now (rejected: moves numbers this WP must not move).
- **Consequences.** Tests: the overlay against a broken evaluator; a changed register changes the hash;
  the Atlas path stays out of the hash; nothing in `src/gridq` imports the engine and it imports no study,
  basis-gate or app code.

### D139 — Site eligibility: facts the dossier lacks are a typed record; controlled assets are a product declaration

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation of WP-UW-02 design choices; adopted by the
  session for Olly that built WP-UW-02.
- **Context.** The streams need facts the OPS-01 dossier has no field for: consumer classes, GSP groups,
  per-MSID-pair export, CM unit type, DFS route and DNO flexibility zone. The load-control rule also needs
  the asset classes the product would control.
- **Decision.** A typed, hashed `EligibilityFacts` record per site (`gridq.eligibility_facts` 1.0.0). Each
  fact is evidenced by IC-4 documents that pass the dossier's admission bar for that fact
  (`FACT_EVIDENCE`), or declared by a named person, or absent. Absent or unadmitted means unknown, with a
  data request. A separate `ControlledAssetsDeclaration` (`gridq.controlled_assets` 1.0.0) holds the Atlas
  asset-class ids; it is product scope, not site evidence. The consumer class goes to the Atlas evaluator as
  declared: a refused class (domestic, small business) is refused without evidence by the Atlas rule, while a
  class that would pass needs admitted evidence. GridQ adds no consumer guard of its own: if the rule does
  not load, the stream is undetermined, noting that the declared class was not evaluated.
- **Reason (Fable).** Extending the dossier would move its pinned golden. Leaving the facts out cannot
  express criterion 3 honestly.
- **Implementation consequences.** IC-4 has no evidence type for a consumer class, so `FACT_EVIDENCE`
  admits none for either consumer-class fact, and under `registered_only` a passing class is undetermined
  today. `mpan_registration` is not stretched to cover it. In `synthetic_fixtures_test_only` mode, on a
  SYNTHETIC- site only, a fact whose admission map is empty may be evidenced by a synthetic fixture of any
  type (so the criterion 3 and 4 tests can run); a registered run never admits one. An empty or missing
  declaration is undetermined, never "no controlled assets".
- **Alternatives considered.** Add the fields to the dossier (rejected: golden); infer the consumer class
  from the MPAN (rejected: not what the document shows).
- **Consequences.** Owner action IC4-CONSUMER-CLASS (CORE): an IC-4 type for a consumer-class record.

### D138 — Site eligibility lives in the UW-01 plug-in with its own hashed rule table

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation of WP-UW-02 design choices; adopted by the
  session for Olly that built WP-UW-02.
- **Context.** Where the engine lives and what identifies its rules.
- **Decision.** `gridq.underwriting.eligibility` (engine and CLI), `eligibility_model` (the product
  `gridq.site_eligibility` 1.0.0, frozen, None-free, hashed), `eligibility_rules` (the table
  `gridq.underwriting.eligibility_rules` 1.0.0, content-hashed as `RULE_TABLE_HASH`) and
  `eligibility_bridge` (the subprocess script). No IC-12 change. CLI: `python -m
  gridq.underwriting.eligibility --as-of YYYY-MM-DD [--atlas-root] [--evidence-dir] [--register] [--out]`;
  output goes only to a git-ignored path (default `dossiers/eligibility/`), else exit 3.
- **Reason (Fable).** A chassis contract would force a release for something not yet wired; a new package
  splits underwriting for no gain. A separate table satisfies criterion 1 structurally and leaves UW-01's
  `gridq.underwriting.rules` hash unmoved.
- **Alternatives considered.** An IC-12 field; a new top-level package.
- **Consequences.** Tests: every check cites at least one row and every cited id is in the register at the
  pinned Atlas commit (and in the live register when the checkout is present); no condition carries a
  figure; the table hash is its payload's; the CLI refuses a tracked path and prints no site id.

### D137 — Site eligibility: seven streams, ordered checks, a classification per stream

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation of WP-UW-02 design choices; adopted by the
  session for Olly that built WP-UW-02.
- **Context.** The shape of the eligibility table (acceptance criterion 2).
- **Decision.** Seven streams in a fixed order: metering class and GSP group, P511 export band, ANM zone, CM
  class, DFS, DNO flexibility zone, load-control exemption class. Each has ordered checks; the stream status
  combines them (any ineligible, else any undetermined, else eligible; not-applicable checks are ignored).
  Each stream carries a classification (a value, or unknown with the reason). Ineligible needs an exclusion
  stated by a row that passes the gate, or the Atlas evaluator's verdict. A classification resting on a
  proposed row is undetermined. REG-18 (a market statistic) is not cited.
- **Reason (Fable).** One question per stream keeps the status meaningful; a stream whose rows state no
  exclusion can only be eligible or undetermined.
- **Deviations from the ruling.** Fable placed REG-07 under DFS and cited REG-39 and REG-40 for the CM class.
  REG-07's register row is "DNO flexibility" (FMAR registration), so it is cited by FLEX-ZONE, not DFS.
  REG-39 (the delivery year's start) and REG-40 (a termination fee for a failed DSR test) state nothing about
  which class a unit takes, and R5's own consequence says a condition with no supporting quote is not
  written, so CM-CLASS cites REG-16 and REG-15 only.
- **Alternatives considered.** One flat verdict per site; per-check output only.
- **Consequences.** Today CM-CLASS is undetermined whatever the facts (REG-16 is a proposal), and ANM-TERMS
  can never be ineligible (REG-19 states no exclusion).

### D136 — Site eligibility: a refused row makes its check undetermined, never ineligible

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation of WP-UW-02 design choices; adopted by the
  session for Olly that built WP-UW-02.
- **Context.** Today no register row is signed off, so the gate refuses every row.
- **Decision.** A check whose cited row the gate refuses is undetermined, with the refusal class and
  message. The only ineligible verdict available without a passing gate is the one the Atlas evaluator
  returns from a loaded rule. Each undetermined check carries `blocked_only_by_gate` (true when every fact
  and threshold it needs is present) and `unblocked_by` (the rows to sign off or re-verify, and the missing
  fields).
- **Reason (Fable).** A categorical condition taken from an unverified or changed row would still be an
  unverified claim, and a wrong ineligible kills a site on it.
- **Alternatives considered.** Apply categorical conditions from refused rows.
- **Consequences.** Today every real-site check outside load control is undetermined. That is the correct
  output. The owner can see which sign-offs would release which checks without the engine leaking a
  would-be verdict.

### D135 — Site eligibility: regulatory thresholds come only from a machine-readable field on the register row

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation of WP-UW-02 design choices; adopted by the
  session for Olly that built WP-UW-02.
- **Context.** P511's band test needs two figures. The task forbids quoting a threshold from anywhere but a
  register row, and no row has a machine-readable threshold.
- **Decision.** The engine reads an optional `thresholds` mapping from the row `require_verified` returns:
  `{name: {value, unit, comparator, evidence_index, quote}}`. It uses one only when the quote is a verbatim
  substring of that evidence's quotes and carries the figure and unit. Units are MW or kW, converted exactly
  (Decimal); the comparator comes from the row. Without the field the check is undetermined: "REG-xx
  carries no machine-readable threshold".
- **Reason (Fable).** Parsing prose invents semantics ("above" and "at least" cannot be told apart safely),
  and any other source quotes a threshold from outside the register.
- **Alternatives considered.** Parse figures from the claim text; keep the figures in GridQ.
- **Consequences.** Owner action ATLAS-THRESHOLDS: add `thresholds` to `rows.yaml` and
  `build_register.py`, with a build check that the quote is verbatim and carries the figure and unit.
  Until then every numeric check is undetermined. The test fixtures' figures are deliberately not the real
  ones and their quotes say so.

### D134 — Site eligibility: tests use pinned byte copies of the Atlas gate and loader; a registered run relies only on an adopted loader

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation of WP-UW-02 design choices; adopted by the
  session for Olly that built WP-UW-02.
- **Context.** The tests must run without an Atlas checkout. Criterion 4 needs a final rule, which no Atlas
  loader can load today (LC-4).
- **Decision.** `tests/fixtures/atlas_load_control/` holds byte copies of `register.py` and
  `load_control/rule.py` from Atlas commit 0cc045be, pinned by sha256 in `PIN.json` with the register's row
  ids. Tests build SYNTHETIC TEST FIXTURE trees around them. The final-rule test uses a labelled stand-in for
  the WP-GRC-07 loader with the same signatures. A drift test compares the pin with the sibling checkout
  (both the commit and the working tree) and skips when it is absent. Live tests assert invariants only.
  `ADOPTED_LOADERS` lists the adopted (loader, register module) sha256 pair. Under `registered_only` any
  other pair gives "not an adopted pair": every row is `not_relied_on` and every check undetermined, with an
  owner action. `synthetic_fixtures_test_only` admits any loader and stamps the table synthetic.
- **Reason (Fable).** Without the pin, pointing the engine at a stand-in tree could yield a real-looking
  pass. With it, adopting the WP-GRC-07 loader is an explicit, recorded act.
- **Alternatives considered.** Skip tests when Atlas is absent; mock the loader in-process.
- **Consequences.** Adopting a new loader adds an `ADOPTED_LOADERS` entry with its decision record; existing
  entries are never replaced. The drift test fails when Atlas changes either file, until that is done.

### D133 — Site eligibility reads the Atlas register and segment rule through a fresh subprocess per run

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation of WP-UW-02 design choices; adopted by the
  session for Olly that built WP-UW-02.
- **Context.** Criterion 5: the segment rule is obtained only through `load_rule` with the run's `as_of`,
  on every run, in a process that re-reads the register file. Atlas caches the register (`_load` is
  `lru_cache`d) and `rule.py` reuses `sys.modules['neura_regulatory_register']`.
- **Decision.** Each run starts one `sys.executable -I -B` subprocess on `eligibility_bridge.py`, a GridQ
  script with no rule logic and no GridQ imports. It calls `load_rule` once, `evaluate` per site and
  `require_verified` per cited row, each in its own `try`, and returns one JSON document with the sha256 of
  the four files it read, hashed before and after. The engine API takes `atlas_root` explicitly. Only the
  CLI resolves it (`--atlas-root`, then `GRIDQ_ATLAS_ROOT`, then sibling `../atlas`) and prints which was
  used. A missing root, a non-zero exit, a timeout, unparsable output, an import failure, a reported `as_of`
  other than the run's, or a file changed during the read: each makes every dependent check undetermined
  with the reason. The engine never raises on these and never passes.
- **Reason (Fable).** Clearing Atlas caches in-process depends on internals that can change silently. A new
  process makes "re-reads the register file" true by construction. Reading the files directly would bypass
  `load_rule` and fail the criterion.
- **Implementation consequences.** `-B` keeps the bridge from writing bytecode into the Atlas tree. The
  machine's Atlas path is replaced by `<atlas>` in reasons and stays out of the hash. A run takes about 0.1 s
  of subprocess time.
- **Alternatives considered.** Import the loader in-process and clear its caches; read the YAML and JSON
  directly.
- **Consequences.** Tests: the register changed between two runs in one parent process changes the
  verdicts and the hash; a released-on date after the run's `as_of` is refused; a stale-dated run gets
  RowStale; seven failure modes and a timeout are each undetermined.

### D132 — Reconciliation: one record, one release, dated equity and the edge cases the four rulings left open

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-17). Executed for Olly.
- **Context.** D128 to D131 were ruled separately. Built together they meet at the record, the identity
  hash, the dated timeline and the debt sculpt, and six points none of them covered.
- **Decision.**
  1. **R1, the record.** `FinancialCase` gains `lifecycle_inputs` (an input: dropped from the canonical dump
     while `None`, present when set) and `lifecycle` (`LifecycleRecord`, derived, always populated, wholly
     excluded from the dump as a derived echo). `PriceRecommendation` gains `levelisation_basis`,
     `measured_irr_with_price_pct` and `equity_closed_loop_gbp_per_kwh`, also derived echoes. All five go
     out as **IC-12 3.1.0**, an additive minor on the D79 template: the three `v3.0.0` files archived at
     their SHA-256, `v3.1.0` rendered, `SiteModel` and `OptimisationResult` byte-identical. (The branch
     released this as 2.2.0 over 2.1.0; it became 3.1.0 at the integration merge, after WP-UW-07's 3.0.0.)
  2. **R2.** A user `real_at_as_of` £ escalates at `(1+c)^(t-1)` in operating year `t`; a
     `nominal_as_entered` £ never escalates. No escalation is applied between `as_of` and COD, and the
     run says so.
  3. **R3, the dated price.** On the dated basis the price levelises the PRE-DEBT flows (no IDC), with
     numerator and volume discounted to one shared anchor, the first month; the closed-loop equity flows
     include the IDC (in the principal) and every lifecycle flow. `levelisation_basis` is `dated` or `annual`.
  4. **R4, the IDC rate.** A stated `construction.idc_rate_pct` wins. Otherwise the term rate of the debt
     that was sized stands in, labelled by where it came from (lender terms with their provenance; credit
     mapping; or "caller-injected DebtTerms, provenance unstated"), with the warning "IDC rate assumed
     equal to term rate" and a data request. No debt, no IDC.
  5. **R6.** When lifecycle inputs are supplied the case says "SIZE SELECTED WITHOUT LIFECYCLE FLOWS": the
     sizing loop does not see them. Nothing is said when none are supplied.
  6. **R7.** On a key-absent release the numbers are bit-identical; the warnings are identical except the
     one "CONSTRUCTION PHASE EXCLUDED" line and the set-level "NO COD-DELAY SENSITIVITY" line, which now
     appear on every run without the input.
  7. **R8.** `horizon_end` is operating year `N` (month `D + 12N` on the dated timeline); the escalation
     exponent uses the operating year.
  8. **R9.** The price levelises every modelled lifecycle flow (they sit in the P50 series it prices); the
     run names each excluded or beyond-horizon flow as not levelised.
  9. **Missed point 1.** A COD delay consumes contracted in-term months pro rata (delivery years are
     calendar-fixed), warned with each stream's before and after term; termination or penalty risk from a
     late start is labelled not modelled.
  10. **Missed point 2.** A sculpted schedule (D77) meeting a non-positive CFADS year after an in-tenor
      decommissioning or replacement is floored at zero service that year, warned "SCULPT FLOORED", never a
      negative service or principal. Without lifecycle flows the D77 refusal is unchanged.
  11. **Missed point 3.** On the dated path the real ledger and real IRR are dated too: deflator 1.0
      through construction, `(1+c)^(t-1)` in operating year `t`; the return basis is stated on both.
  12. **Missed point 4.** Equity flows include the terminal value (CFADS excludes it), so the loop closes.
  13. **Missed point 5.** The price is labelled "FLAT-NOMINAL £/kWh, not indexed" unless an index vector is
      supplied (none is, today).
  14. **Missed point 6.** D129's decision covers size selection on academic runs as well as the
      headline.
- **Tests.** `tests/contracts/test_ic12_chassis.py::test_3_1_0_is_3_0_0_plus_the_stated_optional_members`,
  `::test_a_3_0_0_reader_refuses_a_payload_that_sets_a_3_1_0_member`,
  `tests/contracts/test_schema_archive.py::test_the_archive_pins_every_tagged_file`,
  `tests/test_dscr_on_every_run.py::test_a_run_without_terms_keeps_its_pre_wp_uw_03_quote_identity` (now also
  names the five WP-UW-17 fields), `tests/test_finance_lifecycle.py::test_without_a_stated_construction_rate_the_term_rate_stands_in_and_is_labelled`,
  `::test_a_cod_delay_moves_operation_and_consumes_the_contract_term`,
  `::test_a_sculpt_is_floored_at_zero_service_in_a_negative_lifecycle_year`,
  `::test_a_real_lifecycle_cost_is_escalated_on_an_indexed_run`,
  `::test_a_stated_cod_delay_alone_leaves_the_central_series_untouched`,
  `::test_lifecycle_inputs_split_the_case_identity_and_absence_keeps_it`.
- **Direction of effect.** Neutral on a run without inputs. With inputs: the IDC raises the principal at COD;
  the dated basis lowers the headline IRR; a delay consumes contracted months.
- **Alternatives considered.** Put the derived record in the identity hash (rejected: it is a function of
  inputs already hashed, and would move every identity for no new information). Clip a beyond-horizon flow
  into year `N` (rejected: it moves a cost in time). Refuse a sculpt with a negative lifecycle year (rejected:
  it would drop every sculpted case with a mid-tenor inverter swap to an annuity silently).
- **Consequences.** IC-12 is at 3.1.0; a 3.0.0 reader refuses a 3.1.0 record that carries a financial case.
  The sizer is still not threaded with lifecycle inputs (R6 stays open, labelled on the case).

### D131 — Terminal value, decommissioning and replacements are modelled from typed inputs or excluded by name; the ledger is always populated

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-17). Executed for Olly.
- **Context.** N14: the free-cashflow series carried no terminal value, no decommissioning and no
  replacement, and said so in one warning. Acceptance criterion 4 asks that each be modelled or excluded by a
  labelled warning that reaches the pack.
- **Decision (ruling D3, hardened).**
  1. Typed inputs in `schemas/lifecycle.py`, each £ with a required `price_basis` (`real_at_as_of` |
     `nominal_as_entered`): `TerminalValue` (`user_gbp_at_horizon` | `residual_fraction_of_capex` |
     `declared_nil`, optional P90 amount; no perpetuity, which would invent growth for a finite asset);
     `Decommissioning` (cost, year or `horizon_end`, `net_of_salvage` required); `Replacement` (year,
     component from a fixed list, `description` required for `other`, cost > 0; `()` means declared none;
     battery modules are augmentation, not a component). No figure is defaulted.
  2. `fcf[t] = ebitda[t] + cap[t]`; CFADS = EBITDA − decommissioning − replacement, never the terminal value
     (a lender does not credit a residual). Replacement reduces CFADS in-year; no maintenance reserve is
     modelled (that is a lender input).
  3. A flow dated beyond the horizon is `excluded_beyond_horizon`: recorded and warned, never clipped and
     never refused.
  4. The ledger (`LifecycleRecord.flows`) is always populated: with no input, three `excluded_no_input` rows
     with their data requests, and the legacy N14 warning byte-identical; with any input, one labelled
     warning per flow ("LIFECYCLE — … MODELLED / EXCLUDED (N14)") and a PRICE BASIS line.
  5. The underwriting pack renders a "Lifecycle capital flows" table before the warnings.
- **Reason.** Each flow is a real cash flow a lender asks about; modelling it only from a stated, sourced
  input keeps the data rule; an always-present ledger means an omission is a row a reader sees, not prose.
- **Tests.** `tests/test_finance_lifecycle.py::test_with_no_flow_input_the_ledger_shows_all_three_excluded_with_data_requests`,
  `::test_a_terminal_value_is_in_the_irr_and_never_in_cfads`,
  `::test_decommissioning_and_replacement_reduce_cfads_in_their_year`;
  `tests/test_lifecycle_entry_points.py::test_the_ledger_reaches_the_underwriting_pack`.
- **Direction of effect.** Terminal value raises the IRR (never the DSCR); decommissioning and replacement
  lower IRR and DSCR. None moves a run that states none.
- **Alternatives considered.** D1 (warning only, the status quo: fails criterion 4's "reaches the pack" as a
  row); D2 (library defaults for each flow: no cited source exists, so they would be invented); a perpetuity
  terminal value (rejected above).
- **Consequences.** Data requests: the terminal-value basis (resale, re-contract or second life) with price
  date and evidence, or a declared nil; the decommissioning obligation in the host-site agreement; the OEM or
  O&M replacement schedule with warranty terms; the lender's maintenance reserve.

### D130 — A dated monthly timeline: construction draws, IDC rolled into the principal at COD, and the COD delay applied as a zero-weight row

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-17). Executed for Olly.
- **Context.** H-16 and N18: capex was booked at COD with no construction period or IDC, and
  `cod_delay_months` reached no line of `finance/`, so the delayed sensitivity row was withheld.
- **Decision (ruling C3, IDC option (i) amended).**
  1. `ConstructionPhase` (months, a drawdown profile that sums to 1 with no uniform default, optional
     `idc_rate_pct`) under `LifecycleInputs`, with provenance.
  2. Monthly rate `i = (1 + r/100)^(1/12) − 1`; accumulation factor `A = Σ w_j (1+i)^(n_j + D)`. Debt is
     drawn pro rata with capex, so the gearing cap at COD is `round(g × C × A, 2)` (`A = 1.0` is the legacy
     sizing, bit-identical); `IDC = P_COD − P_COD/A`. Project flows: `−w_j C` at month `−n_j`, FCF at
     `D + 12t`. Equity flows: `−(1−d) w_j C` at `−n_j`, FCF − debt service from actual COD.
  3. With a programme or a delay the headline project and equity IRRs are measured on the dated timeline
     (`returns.dated_irr`) and say so; payback, the capital-recovery account and `p50_cashflows` stay annual
     and COD-anchored, labelled. Without either, the annual path is untouched and the case says
     "CONSTRUCTION PHASE EXCLUDED … This flatters IRR."
  4. The COD delay is applied. `lifecycle.cod_delay_months` adds a zero-weight CUSTOM `cod_delay` row to the
     standard scenario set (label "COD delay +Dm"); `run_financial_model` reads `scenario.cod_delay_months`
     and runs that row dated, with its own figures, note ("APPLIED … NOT modelled: holding costs …") and
     case identity. The N18 withholding is deleted. No delay stated: no row, and the set says "NO COD-DELAY
     SENSITIVITY: no delay was supplied; none is assumed."
  5. Entry points: `generate_quote(lifecycle=)`, `gridq run --lifecycle FILE` (JSON or YAML, refused with exit
     2 before solving if invalid), and the app's `lifecycle_json` field on both forms (400 if invalid).
- **Reason.** C1 (annual with capex at year 0) hides the time value of construction equity; C2 (annual
  periods) cannot express months. Option (i) keeps D75's meaning of gearing; (ii) would redefine it and (iii)
  invent a funding order. One factor covers both gearing and the D77 target sizing. A zero-weight row
  isolates the delay without moving the central case.
- **Tests.** `tests/test_finance_lifecycle.py::test_a_construction_phase_precedes_operation_with_idc_by_hand`,
  `::test_without_a_programme_construction_is_excluded_and_labelled`,
  `::test_a_cod_delay_moves_operation_and_consumes_the_contract_term`;
  `tests/test_truth_in_labelling.py::test_n18_a_delayed_row_reports_its_own_applied_figures`;
  `tests/test_lifecycle_entry_points.py::test_the_stated_cod_delay_is_an_applied_zero_weight_row`,
  `::test_the_standard_set_adds_the_delay_row_at_zero_weight`, `::test_cli_run_with_lifecycle_records_and_prints_it`,
  `::test_app_wizard_hands_the_lifecycle_to_the_pipeline`, `::test_app_real_form_hands_the_lifecycle_to_the_pipeline`.
- **Direction of effect.** A programme or a delay lowers the headline IRR and raises the principal at COD.
- **Alternatives considered.** C1 and C2 above; withholding the delayed row (the N18 interim, now superseded);
  a default construction duration or IDC rate (no cited source; the data rule forbids it).
- **Consequences.** Data requests: construction months and drawdown (EPC programme); the construction
  facility rate; the expected COD delay and holding costs during it; the CM delivery-year start.

### D129 — The CPI key is read: a nominal ledger with selective escalation, per-line basis tags, a real view, and the CM payment indexed per the published Regulations

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-17). Executed for Olly.
- **Context.** N13/H-15: `financing.cpi_pct_per_year` (gb_v1_7 null; gb_v1_8 to gb_v1_12 2.0, needs_review,
  OBR EFO March 2026) had no reader, so the headline was "neither real nor nominal". D-33 holds a tenored CM
  payment flat on its MW, and its indexation was unmodelled.
- **Decision (ruling B5).**
  1. Absent key (gb_v0_1 to gb_v1_6, the golden demo's gb_v1_4): every number bit-identical; every line
     tagged `flat_nominal_unescalated`. Null key (gb_v1_7): the same arithmetic plus one D45 warning,
     "financing.cpi_pct_per_year is null: escalation NOT applied (not read as 0%)". Numeric key: the
     nominal, selectively escalated ledger, `E_t = (1+c)^(t-1)`, year 1 the price base.
  2. Escalated (`nominal_cpi_escalated`): opex, insurance, metering/IDNO, DNO, business rates, the lines the
     library's own note names; one additive term `−B (E_t − 1)` in all three EBITDA series. Flat
     (`nominal_flat`): levies on charge, parasitic load, dispatch charging, the windowed-levy credit, every
     merchant and firm revenue, post-expiry re-contracting.
  3. A tenored T-4 CM payment is `nominal_cm_cpi_indexed`: `CM_t = base × in_term_t × U × E_t`, on P50, P90,
     the lender series and the penalty line, with `U = CPI_x/CPI_base` from a typed
     `CapacityAgreementIndexation` (auction type, delivery year, the two CPI averages, reference). The rule,
     quoted from the Regulations (retrieved 2026-11-26): The Electricity Capacity Regulations 2014
     (SI 2014/2043) Sch 1 para 3(5), "If the relevant capacity auction was a T-4 auction, PE_ix is to be
     calculated in accordance with the formula— PE_ix = CCP_i × CPI_x / CPI_base", and para 3(7), CPI_base
     "the average of the monthly values of CPI for the months of the base period referred to in regulation
     11", CPI_x "the average of the monthly values of CPI for the months of the winter ending on the 30th
     April preceding the start of year X". Sources:
     https://www.legislation.gov.uk/uksi/2014/2043/schedule/1 (data.xml sha256 0d7893ce…a048db);
     https://www.legislation.gov.uk/uksi/2014/2043/regulation/11 (reg 11(1)(f), sha256 095bff9c…48aa).
  4. A T-1 agreement is held un-indexed: para 3(6), "If the relevant capacity auction was a T-1 auction or a
     DSR transitional auction, PE_ix is equal to CCP_i", stood until 22.7.2025
     (https://www.legislation.gov.uk/uksi/2014/2043/schedule/1/2025-07-22, sha256 fefff8ad…7a787) and was
     omitted from 23.7.2025 by SI 2025/917 Sch para 18(a)(ii) (https://www.legislation.gov.uk/uksi/2025/917,
     sha256 5476cf3f…48aa); the current text states no T-1 formula, so the pre-amendment rule is applied and
     warned.
  5. No agreement supplied: the type is ASSUMED T-4, because the library's CM price is the 2029-30 T-4 clear,
     and warned; `U = 1.0` with the warning "CM year-1 uplift CPI_x/CPI_base NOT applied (ratio 1.0) …
     Understates CM if CPI rose", naming the library's base period (Winter 2024/2025, needs_review) and the
     ONS ask. An un-tenored CM stays faded and un-indexed, warned.
  6. Every line states its basis (`CashflowModel.line_basis`, `IndexationRecord.line_basis`); a real ledger
     (`nominal_t / E_t`, year 0 undeflated) and a real project IRR are reported alongside the nominal one, in
     one "BASIS —" warning.
- **Hand recomputation (synthetic gb_v1_12 site, no CM, debt 50% at 7% over 10 years).** The year-1 indexed
  base is B = opex 12,000.00 + insurance 3,507.78 + business rates 8,000.00 + metering 0 + DNO 0 =
  £23,507.78. Year 15: E_15 = 1.02^14 = 1.3194788, so the EBITDA change is −23,507.78 × 0.3194788 =
  −£7,510.24; the model's change against the nulled twin is −£7,510.24 (to the pence of the rounded series).
  Effect on that site: project IRR 12.2708% to 11.7605% nominal (9.9776% real); min DSCR 1.3890 to 1.2972.
  Pinned year by year in `test_a_numeric_cpi_escalates_exactly_the_indexed_cost_lines_by_hand`.
- **Golden pins.** None moves: the golden demo runs gb_v1_4, which carries no CPI key (point 1). The academic
  headline on gb_v1_8 to gb_v1_12 moves under this decision (cost escalation lowers IRR, DSCR and payback on
  every site, and may change the selected size, missed point 6); no committed test pins an academic site
  figure on those releases outside the DPA-quarantined modules.
- **Tests.** `tests/test_finance_lifecycle.py::test_an_absent_cpi_key_keeps_every_line_flat_and_unescalated`,
  `::test_a_null_cpi_key_is_warned_and_never_read_as_zero`,
  `::test_a_numeric_cpi_escalates_exactly_the_indexed_cost_lines_by_hand`,
  `::test_every_line_states_whether_it_is_real_or_nominal`,
  `::test_a_t4_cm_payment_is_indexed_u_times_e_t_per_the_published_rule`,
  `::test_without_an_agreement_the_cm_is_assumed_t4_and_the_uplift_is_withheld_and_asked`,
  `::test_an_untenored_cm_is_not_indexed_and_says_why`.
- **Direction of effect.** On gb_v1_8 to gb_v1_12: costs rise from year 2, so IRR and DSCR fall; a tenored T-4
  CM rises with CPI, partly offsetting. No effect on releases without the key.
- **Alternatives considered.** B1 opt-in flag (rejected: an unread key fails criterion 2); B2 escalate all
  revenue (no cited merchant escalation); B3 a real ledger with nominal debt (debt service is contractually
  nominal); B4 (tightened into B5).
- **Consequences.** Data requests: ONS CPI (All Items) monthly for the auction's base period and for the
  winter before the first delivery year; the DESNZ base-period letter for 2029-30; the actual CM agreement
  (type, delivery year, term); whether the 12% target is nominal (Sim); whether the customer price is indexed;
  confirmation of the indexed-line list. Model_v5 escalates all revenue at 2.5%: a named reconciling item for
  the WP-UW-13 export.

### D128 — The price is levelised over the modelled volume on the run's own series, and `hits_target_return` is measured in a closed loop

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-17). Executed for Olly.
- **Context.** N12: the fixed £/kWh price divided a capital-recovery requirement by flat year-1 volume, so it
  under-priced a fading asset, and `hits_target_return` was not a measurement of the price it was attached to.
- **Decision (ruling (e) = (b) plus (d) as a diagnostic).**
  1. `P = round(max(0, −NPV_r(priced flows)) / Σ DF_t × E_t, 6)` at `r = financing.target_equity_irr_pct`,
     on the run's own P50 pre-debt series, with `E_t` the modelled delivered volume
     (`discharge × (1−fade)^(t−1) × availability`, `CashflowModel.delivered_energy_kwh_by_year`). Flat-nominal,
     unlevered, pre-tax, floored at £0 ("customer price is £0, not negative"), labelled each way.
  2. Closed loop: the run's IRR is re-measured with `P × E_t` added (the equity IRR when debt is sized, else
     the project IRR). Below target by more than 0.01 pp → `False` with the equity-solved price; at or above
     → `True` only if the target is CONFIRMED, else `None` (placeholder); unmeasurable → `None` with an UNKNOWN
     warning.
  3. `equity_closed_loop_gbp_per_kwh`: the price at which the equity IRR equals the target, by bisection on
     `[0, 4P]` with the debt fixed. Diagnostic only, never the headline.
  4. "DSCR and debt sizing exclude customer price revenue." The legacy scalar CRF path runs only when the
     volume is unknown.
- **Reason.** The run's own series picks up every feature (fade, availability, escalation, lifecycle flows,
  construction) without a second formula. An equity-solved headline flips basis with the lender terms and is
  circular under a D77 sculpt.
- **Tests.** `tests/test_finance_lifecycle.py::test_the_delivered_volume_fades_and_is_availability_scaled_each_year`,
  `::test_the_price_is_levelised_over_the_modelled_volume_not_flat_year_one`,
  `::test_hits_target_return_is_true_only_when_the_measured_equity_irr_clears_the_target`,
  `::test_hits_target_return_is_false_when_the_measured_equity_irr_misses`,
  `::test_hits_target_return_stays_unknown_on_a_placeholder_target`,
  `::test_the_levelisation_is_wired_into_the_production_run`,
  `::test_a_stack_that_clears_the_target_is_priced_at_zero_not_negative`.
- **Direction of effect.** The price rises where volume fades (it was under-stated); `hits_target_return` is
  now `False` where cheap price meets dear debt.
- **Alternatives considered.** (a) keep flat year-1 volume (the defect); (c) a WACC rate (needs gearing real
  sites lack); (d) equity-solved headline (rejected, reason above).
- **Consequences.** The golden demo's price is not pinned; it is recomputed on the levelised basis. The price
  revenue enters no DSCR.
  The synthetic rich-quote fixture (gb_v0_1) was priced at £0/kWh, although its project IRR is 7.41% against a
  12% target. It is now priced at £0.021967/kWh. tests/test_consulting_report.py no longer pins the zero, and it
  still checks that the target-return state sits beside the price.

### D127 — IC-9 reconciliation: limits on the class, rebound split by fact, a stored timeline hash, per-curve units, seconds, cell states, `fold=0`, `stack_at` only, a caller-stated duration

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.** A Fable 5.2 chair reconciled nine conflicts between D122 to D126. Everything else
  in those rulings stands.
  - (a) **Call limits live on the SLA class only.** Every envelope of a class draws on one budget
    (`CallLimits`: events per window, minutes per window, minimum rest). Per-envelope limits are dropped.
  - (b) **Rebound is split by fact.** The class says whether energy returns (`energy_return`). The envelope
    says how much (`rebound: ReboundSize{recovery_permille, max_rebound_kw} | NotApplicable | Unknown`).
    A cross-guard ties the two: `NoReturn` needs `NotApplicable`, `ReturnsLater` needs a size or
    `Unknown`, and an unknown return needs `Unknown`.
  - (c) **`resolved_hash` is stored.** The compiled timeline is a separate schema
    (`ResolvedTimeline`) and is not embedded.
  - (d) **Absolute or relative per curve, not per point.** `AbsoluteCurve` (kW) or `RelativeCurve` (basis
    points of running IT load, with a kW cap). Pareto order is checked in the curve's own unit.
    `stack_at(declaration, t, duration_min, it_load_kw)` takes the load. The "cap can bind" guard runs only
    when every curve is absolute.
  - (e) **Notice and ramp in seconds.** Holds stay in minutes, 1 to 1,440.
  - (f) **Cells hold `Offered{envelope_ids}`, `Unavailable{reason_code}` or `Unknown`, in IC-3's frame.**
    One dated mechanism, `overrides`, replaces the state wholesale. A template `Unavailable` reports
    `NOT_OFFERED`; an override `Unavailable` reports `BLACKOUT`. An envelope offered nowhere is refused.
  - (g) **The naive-local reader is PEP 495 `fold=0`.** The spring-forward hour is read, not left as a hole.
    The repeated autumn hour is read at its first occurrence. `intervals_in_force` is counted with the same
    reader.
  - (h) **Several envelopes: kW only from `stack_at`.** The overlay's total is `stack_at`'s total, and its
    per-envelope layers are that answer's layers, so they sum to the total by construction.
  - (i) **The caller states the duration.** `turndown_overlay(synthesis, *, duration_min)` has no default,
    and `duration_min` enters the overlay hash.
- **Reason.**
  - One shared budget is the no-double-booking rule that D57 leaves standing.
  - A stored hash turns a silently different clock-change reading into a conformance failure.
  - Seconds keep the property that decides which services an envelope can reach.
  - "Lower value" is undefined for a cell state, and "not in force" would put a hole in the overlay that
    the operator never declared.
  - Merit order and the concurrent cap are declared facts, so summing curves would double-count.
  - Picking a duration would be an invented assumption about how the envelope is called.
- **Alternatives considered.** Per-envelope event limits; rebound only on the envelope; a timeline that
  is neither stored nor hash-bound; absolute kW only; minutes for notice; IC-3 `terms_id` cells; a
  not-in-force or lower-value clock-change rule; per-envelope overlays without stacking; a fixed 30-minute
  or longest-point duration. Each was rejected for the reason above.
- **Consequences.** `compute_turndown.py` and `gridq.datacentre.turndown` are built to these field
  lists. The tests are `test_ic9_compute_turndown.py` (clock-change oracle, `stack_at`, `check_call` and
  budget tests, mutants) and `test_datacentre_ic9_untouched.py` (overlay tests).

### D126 — The load model takes IC-9 as an optional input: a blind baseline, a detached binding, and an overlay on request

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option L2, built as an L1 core plus a detached overlay.
  1. `synthesise(inputs, *, seed, draws, workload_flex=NO_ENVELOPE_DECLARED)`. The same keyword is on
     `DataCentreArchetype.synthesise` and `derive_data_centre_half_hourly`. The default is a frozen
     singleton sentinel (`gridq.datacentre.workload_flex`), not `None` and not a number.
  2. **The baseline is structurally blind.** The former body of `synthesise` moved, unchanged, to
     `_synthesise_baseline(inputs, *, seed, draws)`. `synthesise` calls it and, only when a declaration is
     given, returns `dataclasses.replace(base, workload_flex=binding)`. The declaration never reaches
     `_Engine`, `evaluate`, the manifest, flags, warnings or `DcLoadInputs`. `METHOD_VERSION` is unchanged.
  3. **The binding** (`WorkloadFlexBinding`) holds the declaration, its content hash, the contract
     version, the half-hours in force and offered, notes, and
     `binding_hash = sha256({series_hash, declaration_hash, contract_version})`. A declaration for another
     site raises. One that does not overlap the horizon is bound with a note, not refused.
  4. **The overlay** (`gridq.datacentre.turndown`) is computed on request only.
     - `turndown_overlay(synthesis, *, duration_min)` gives, per draw and half-hour, the IT turn-down
       (`stack_at` at the draw's IT load floored to a whole kW) and each envelope's layer.
     - `meter_instant_kw` is the IT turn-down plus the UPS and distribution losses that fall with it, with
       cooling held.
     - `meter_settled_kw` is facility power at the declared load minus facility power at the reduced load,
       both through `evaluate`.
     - The reduction is allocated pro rata to phase load (`pro_rata_phase_load/v1`).
     - Every quantity has an expected series and P05 to P95 bands. Everything is labelled
       `firmness="conditional"`, and the overlay has a hash.
     - `turndown_overlay_minutes` gives the IT turn-down at one minute.
  5. **`derive.py`.** The four WP-SITE-01 files are byte-identical. A declaration adds
     `<site>_dc_workload_flex_<decl16>.json` and, for each duration the caller names in
     `overlay_durations_min` (none by default), one overlay CSV. The stamped site and the derived record
     are unchanged.
- **Reason.**
  - B8-03: WP-SIZE-11 needs a conditional quantity at the meter. Only this package holds the component
    model that turns IT kW into meter kW and caps it by the load actually running. L1 alone would hand
    that physics downstream.
  - Acceptance 2 holds by construction, because the baseline function has no declaration in scope.
  - The instant/settled pair brackets the cooling lag without inventing a call pattern (A2-04).
- **Alternatives considered.**
  - L1 alone: safe, but hands the physics downstream.
  - L3 (simulated calls): invents calls, against A2-04.
  - L4 (load reduced in the series): breaks acceptance 2.
  - The declaration inside `DcLoadInputs`: moves `inputs_sha256` and every series hash.
  - The binding kept outside `Synthesis`: then the load model has not accepted it.
  - A pinned golden instead of a git oracle: WP-SITE-01 pinned none, and floats differ across platforms.
- **Consequences.**
  - Acceptance 2 is proved in `tests/test_datacentre_ic9_untouched.py`:
    - three-way equality (omitted, explicit sentinel, declared) over the half-hourly, band and one-minute
      paths;
    - an `evaluate` spy;
    - a git oracle that runs commit `d155e91` in a fresh interpreter;
    - byte-identity of the four files;
    - an AST test on the engine, plant, inputs and baseline builder;
    - mutants M1 to M7 with a no-op control.
  - `test_seed_and_draws_have_no_default` asserts that the default `is NO_ENVELOPE_DECLARED`.
  - **Built differently from the ruling.**
    - The one-minute overlay carries IT turn-down only. The meter conversion is half-hourly in this
      version, as its notes say.
    - The sidecar is one CSV per duration, holding the expected series and bands of all three quantities,
      not a separate bands, members and manifest file each.
    - The binding also counts the half-hours at which something is offered.

### D125 — IC-9 price: a structured `PriceOffer` per envelope, in whole pence, with a typed basis

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  - Each envelope has `price: PriceOffer | Unknown`. `Unknown` means "on request" or "withheld".
  - A `PriceOffer` has:
    - `currency: "GBP"`;
    - `availability` (`pence_per_mw_per_hour`), `utilisation` (`pence_per_mwh`) and `per_event` (`pence`),
      each a positive integer, `NotApplicable` ("not charged") or `Unknown`. Zero and negative amounts are
      refused.
    - `price_basis`: `Indicative{stated_on}`, `FirmOffer{evidence: IC-4 EvidenceRef}` or
      `SyntheticPlaceholder`.
  - **Guards.**
    - Three unknown components must be written as a whole-price `Unknown`.
    - A firm offer has no unknown component.
    - `SyntheticPlaceholder` is required exactly when the declaration is a `synthetic_fixture`.
  - **Readers.**
    - `call_cost_pence` and `availability_cost_pence` return an exact `Fraction`, or `Unknown`, which is
      never zero.
    - `pence_to_pounds` converts the result.
    - `price_basis_label` prints "SYNTHETIC PLACEHOLDER, not an offer" for a fixture.
  - No validator or reader refuses, zeroes or ranks an envelope on its price (D57).
- **Reason.**
  - Availability, utilisation and event fees are how flexibility is priced.
  - Integer pence with integer kW and minutes gives exact arithmetic.
  - The basis union makes a number say whether it is indicative, evidenced or synthetic. Evidence guards
    the word "firm", not a stream.
- **Alternatives considered.**
  - A single utilisation number: forces operators who charge a holding fee to misdeclare.
  - Tranche price curves: duplicate the kW axis; several envelopes express the same thing.
  - An evidence reference alone: WP-SIZE-11 could compute nothing.
  - Open ISO 4217 codes: mixed-currency arithmetic with no FX rule.
- **Consequences.**
  - The 22 guard tests and price tests are in `test_ic9_compute_turndown.py`, with mutants "an all-unknown
    price is the whole price unknown", "a firm offer states every component", "a placeholder price is
    exactly a synthetic record" and "an unknown price component is never zero".
  - Every fixture price is a synthetic placeholder or `Unknown`.

### D124 — IC-9 SLA class: declared once per declaration, referenced by envelopes, owning a shared call budget

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  - `sla_classes` sit at declaration level. Each `SlaClass` has:
    - `class_id` and `operator_label` (opaque: never parsed, compared or ranked);
    - `workload_action: deferred | slowed | moved | dropped | Unknown`;
    - `energy_return: ReturnsLater{return_window_min} | NoReturn | Unknown`;
    - `call_limits: CallLimits`;
    - `sla_evidence: EvidenceRef | Unknown`.
  - Every envelope names exactly one class. A dangling, duplicate or unused class is refused.
  - The action bounds the return: `deferred` refuses `NoReturn`; `moved` and `dropped` refuse
    `ReturnsLater`; `slowed` admits either.
  - Every limit is an integer, `NotApplicable` or `Unknown`.
  - `call_budget_used` and `check_call` count every envelope of the class against one budget. A limit that
    cannot be checked is reported as `unchecked`, never passed.
  - `resolve_sla_evidence` resolves the evidence as an IC-4 `service_level_agreement`. It never admits a
    self-issued record and refuses a synthetic one by default.
  - The class carries no firmness, rank or "protected" value.
- **Reason.**
  - A shared budget is the no-double-booking arithmetic: two envelopes on one workload pool cannot each
    spend the full allowance.
  - Whether energy returns is the physical fact the sizer needs; a class name cannot say it.
  - Nothing is invented: the label is opaque, every quantity may be `Unknown`, and evidence is optional.
- **Alternatives considered.**
  - A closed class enum: invents SLA meanings.
  - An open string with mandatory numbers: forces invention.
  - Ranked classes: a merit order is dispatch policy, and a rank would act as a soft gate.
- **Consequences.** The guards have mutants: deferred work returns; moved or dropped work does not
  return; the class's event budget is shared; the minimum rest; the duration budget; the half-open budget
  window. A spy test proves `resolve_sla_evidence` asks IC-4 for the right type and admission.

### D123 — IC-9 units and time base: integers at IT load, seconds for notice, IC-3's GB-clock template compiled to UTC and bound by hash, a `fold=0` naive-local reader

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  - **Units.**
    - Every quantity is IT electrical load (`measurement_point: "it_load"`), in integers: kW, basis points
      (1/10,000) of running IT load, hold minutes 1 to 1,440, and notice and ramp in seconds.
    - `turn_down_kw` clamps every amount to the running load.
    - Producers round to the safe side: amounts and holds down, notice and ramp up. They refuse a zero
      offer.
  - **Time base (IC-3's hybrid).**
    - `CLOCK = "Europe/London"` and `RESOLUTION_MIN = 30`.
    - `seasons` x `day_groups` x `bands` cells, with dated UTC `overrides` on the grid.
    - Validity is `UtcInstant`, half-open and on the grid.
    - `resolve` compiles to a `ResolvedTimeline` whose hash is stored as `resolved_hash`.
  - **The load model's reader.**
    - `declared_at_local(declaration, local_date, half_hour)` maps a naive local half-hour to UTC under
      PEP 495 `fold=0`, then reads the timeline.
    - Every day yields exactly 48 answers.
    - Minute `m` reads half-hour `m // 30`.
- **Reason.**
  - Integers give one JSON form and exact comparisons, as in IC-3 and IC-11.
  - A relative basis is right during phase ramp, when a multi-year absolute kW would overstate the
    turn-down.
  - GPU power capping acts in seconds, so rounding notice up to a minute would hide the envelope's main
    property.
  - IC-3 consumes IC-9, so the same template and timeline let it read IC-9 per half-hour with no clock
    arithmetic.
- **Alternatives considered.**
  - Float MW: needs tolerances.
  - Decimal strings: put parsing on every consumer.
  - Absolute kW only: wrong during the ramp.
  - Minutes for notice: loses sub-minute notice.
  - Absolute intervals: unreadable over years.
  - A recurring template only: cannot state a dated outage.
  - Finer grids: many encodings of one policy.
  - Reading the template from naive time directly: bypasses overrides and validity.
- **Consequences.**
  - A `fold=0` oracle that shares no code with the contract runs at every half-hour of five declarations,
    including the 23-hour and 25-hour nights of 2027.
  - Mutants: "the resolver walks the clock change a half-hour at a time", "the naive-local reader is
    fold=0", "the resolved hash binds the compilation".
  - The spring and autumn fixtures span the 2027 clock changes.

### D122 — IC-9 shape: one declaration per site and operator, a tiered menu of envelopes, a recurring availability template, declared stacking, and a generated specification

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option C.
  - `ComputeTurndownDeclaration` covers one site and one operator, and holds many envelopes.
  - "No declaration" is the absence of an instance, never an empty one.
  - **Envelopes.** Each `TurndownEnvelope` is a Pareto duration curve (holds strictly up, amounts strictly
    down) with response terms, a price, an SLA class, a rebound size and an optional `exclusion_group`.
  - **Availability.** `availability` is an IC-3-style GB-clock template.
  - **Stacking.**
    - `stacking` names a full merit order and a concurrent cap. The cap must be able to bind.
    - `it_reference` names a retained IT floor.
    - `stack_at` takes envelopes in merit order, one per exclusion group, up to the cap and above the
      floor. Consumers take kW from `stack_at` or `check_call`, never by summing curves.
  - **Supersession.** `active_at` drops superseded declarations and refuses two in force for one site.
  - **Publication.** The specification `specs/ic9_compute_turndown_declaration.v1.0.0.md` is generated from
    the module and its committed schemas by `_specdoc.py`, and `--check` fails on drift.
  - The contract has no firmness field. It states that consumers classify IC-9 kW as CONDITIONAL (B8-03),
    outside the priced floor. L4 has no instruction or acknowledgement message (ADR-D2).
- **Reason.**
  - The spec's six nouns (MW, duration, notice, price, SLA class, validity) describe a product, and tiers
    carry them directly.
  - Explicit stacking is the only place the D57 arithmetic can live; a flat list invites summing.
  - Recurrence on the GB clock matches IC-3, its consumer.
  - A generated specification cannot drift from the code.
- **Alternatives considered.**
  - (A) Absolute intervals: constant reissue, and no stacking or budget semantics.
  - (B) One curve per cell: duplicates terms, has no weekday axis, and cannot hold two SLA tiers.
  - (D) A producer-shipped compiled timeline: a second source of truth. Ruling D127 (c) keeps
    only its hash.
  - A hand-written specification: drifts.
- **Consequences.**
  - Schemas `ic9_compute_turndown_declaration.v1.0.0` and `ic9_resolved_timeline.v1.0.0`; tag
    `contracts/ic9-compute-turndown/v1.0.0`.
  - Six synthetic fixtures with pinned golden hashes, and 42 mutants in `test_ic9_compute_turndown.py`.
  - Out of v1:
    - instruction and acknowledgement messages;
    - per-cell price;
    - sub-site granularity;
    - rebound time profiles;
    - holds over 1,440 minutes;
    - a bank-holiday day type.

### D121 — IC-5 reference simulator: a relay and two twins in a sibling package, over stdlib HTTP

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-EDGE-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option C of three: (A) fixtures only, (B) a twin inside `gridq.contracts`, (C) a relay and two
  twins in their own package.
  1. `gridq.edge_reference`, beside the contracts package, importing only the standard library, pydantic and
     `gridq.contracts` (an AST test and a fresh-interpreter test enforce it). Never installed on the gateway.
  2. `machines.py`: `EdgeReference` and `CloudReference` call the contract's pure functions for every decision.
     `Simulation` holds one role (`relay`, `edge-twin`, `cloud-twin`), mailboxes, fault switches and a canonical
     transcript.
  3. `http.py`: `POST /ic5/v1/frames` (202; 200 on an identical replay; 400, 409, 413, 415, and 422 with
     `{code, loc, detail}` including `non_synthetic_site`, `anm_not_local`, `unsupported_contract_version`),
     `GET /ic5/v1/frames`, `/ic5/v1/schema`, `/ic5/v1/state`, and `/sim/v1/clock|faults|anm|reset`. Loopback only,
     `X-IC5-Reference: simulator` on every response, `SYNTHETIC-` sites only.
  4. Simulated time moves only through `/sim/v1/clock`; tests use port 0 and no sleeps.
- **Reason (Fable).** A server inside the leaf would put a socket into every contracts-only install. A relay
  that refuses with a reason teaches the contract to both sides; twins alone only test each side against
  Neura's own reading. Fault controls built once are reused by WP-EDGE-04 and WP-TWIN.
- **Alternatives considered.** (A) not runnable; fails acceptance 3. (B) socket code in frozen-contract territory
  and on the gateway footprint. B alone: no cross-validation of two real clients and no fault surface.
- **Consequences.** Left out of v1: MQTT and OPC UA bindings, TLS, auth, non-loopback bind, physics, real DNO
  protocols, persistence and the 7-day store-and-forward buffer. Executor's choices inside the decision: the
  machines are classes with `advance` and `receive` rather than functional reducers (same determinism, pinned by
  the transcript test); the cloud twin issues an idle schedule from the envelope on each MPC cadence rather than
  replaying a script; the faults are `drop_to_edge` and `hold_to_cloud`; there is no `--realtime`; the 413 limit
  is the profile's `max_frame_bytes`. Tests (`test_ic5_reference_endpoint.py`):
  `test_a_cloud_client_and_an_edge_client_talk_through_the_relay`,
  `test_a_cloud_client_develops_against_the_edge_twin`, `test_an_edge_client_develops_against_the_cloud_twin`,
  `test_dropped_frames_past_the_lease_enter_fail_safe_and_a_fresh_schedule_exits`,
  `test_every_refusal_has_its_status_and_code`,
  `test_the_http_endpoint_and_the_in_process_simulation_write_the_same_transcript`.

### D120 — IC-5 latency budget: a required per-path quantile budget with explicit clock skew

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-EDGE-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option C of three: (A) a scalar bound, (B) a quantile bound that assumes synchronised clocks,
  (C) a budget, a per-frame deadline and a compliance function.
  1. `LinkProfile.latency_budgets`: `cloud_to_edge_schedule` is a required `LatencyBudget`; `edge_to_cloud_ack`
     and `heartbeat` may be not applicable. `LatencyBudget` is `{path, quantile_per_mille, bound_ms,
     min_samples}`, with `min_samples * (1000 - q) >= 1000` when q < 1000 (a p99 needs 100 samples).
     `max_clock_skew_ms` is below every bound. No default anywhere.
  2. Each schedule carries `deliver_by == sent_at + bound_ms`; the edge refuses a mismatch
     (`deliver_by_mismatch`) and flags `late` when `received_at > deliver_by`, strictly.
  3. A sample is the floor to the millisecond; a negative value within the skew reads 0, beyond it the sample is
     excluded. The quantile is the nearest rank, `(q*n + 999) // 1000`, in integers.
  4. `check_latency` returns `insufficient_samples`, `unverifiable_clock`, `pass` (observed + skew ≤ bound),
     `fail` (observed − skew > bound) or `indeterminate_within_skew`. Only `pass` passes.
  5. The path set has no ANM member: ANM timing comes from IC-1 only.
- **Reason (Fable).** "A tested contract field" (acceptance 2) needs a function and report everyone computes
  identically, not a number nobody can fail. One-way latency without a clock bound is unfalsifiable; the skew
  guard band gives conservative verdicts in exact integers. `deliver_by` keeps budget arithmetic off the edge.
  `min_samples` stops a pass on three samples.
- **Alternatives considered.** (A) no quantile, measurement points or test semantics. (B) silently assumes
  synchronised clocks. Refusing late schedules: turns jitter into lost dispatch and duplicates staleness.
  Interpolated quantiles: floats.
- **Consequences.** Left out of v1: round-trip estimation, a rolling-window policy (the caller picks the
  window), several quantiles per path, alert thresholds, the clock-sync protocol. Executor's choices inside the
  decision: `LatencySample.clocks_synchronised` stands in for a per-ack offset bound; `insufficient_samples`
  counts eligible samples, and `unverifiable_clock` fires when the clock-excluded samples exceed what the
  quantile's tail allows (`excluded * 1000 > (1000 - q) * total`), so a single bad clock cannot void a large
  window. No published source sets an edge–cloud schedule latency, so the spec's "≤5 s p99" (§10A IC-5 row)
  appears only in the `SYNTHETIC-` profile fixture. Tests: `test_the_schedule_latency_budget_is_a_required_contract_field`,
  `test_check_latency_verdicts`, `test_nearest_rank_is_exact`, `test_a_quantile_needs_enough_samples_to_mean_itself`,
  `test_deliver_by_is_sent_at_plus_the_bound_and_the_edge_checks_it`.

### D119 — IC-5 ANM is edge-local by construction: unrepresentable cloud to edge, and the cloud only tightens

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-EDGE-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option C of three: (A) a documented rule, (B) a rule plus runtime checks, (C) structural absence
  plus a cap invariant, both proved by test.
  1. `LinkProfile.anm_authority` is `Literal["edge_local"]`, and the profile binds IC-1 by
     `ConnectionAgreementRef`. It copies no ANM response time.
  2. `AnmEvent` (edge to cloud only, hash-chained): the signal (`AnmSetpoint`, `AnmTrip`, `AnmRelease`),
     `received_at`, `enforced_at`, `response_elapsed_ms`, `dno_acknowledged_at`, `agreement_hash`,
     `path: "edge_local"` and `cloud_link`.
  3. `verify_anm_response(event, agreement)` compares `Fraction(elapsed_ms, 1000)` with IC-1's
     `anm_response_time_s` exactly: within, late or unverifiable.
  4. `effective_import_cap` is the minimum over the applicable terms: an ANM trip (0 kW), an ANM limit, the
     fail-safe firm limit (in fail-safe), the envelope and the schedule (in normal mode). The cloud can only
     tighten. Only a local release or a higher local setpoint loosens it.
- **Reason (Fable).** Spec §8(2) and WP-EDGE-05 need the ANM path to work with the cloud link down. Only a
  structural absence, proved by test, survives later schema edits. The min rule makes cloud staleness or error
  harmless. Hash-binding IC-1 with an exact rational comparison avoids a second source of truth.
- **Alternatives considered.** (A) unenforceable. (B) leaves the deadline source and the `Unknown` case
  undefined. Copying the response time into IC-5: a second source of truth and a rounding rule. Refusing closed
  loop when the time is unknown: a gate decision for ADR-D2, not for a wire contract.
- **Consequences.** Left out of v1: ramp-rate and reactive ANM terms, export-side ANM, LIFO attribution, DNO
  protocol mapping (WP-EDGE-05). Executor's choices inside the decision: a trip is `dno_trip` and an `Unknown` ANM
  state is `dno_signal_lost` (D118's codes), and a setpoint is not a trigger. Fable's
  `ScheduleSuspended` result is a 0 kW cap bound to `anm_trip`. The binding term is reported on the heartbeat's
  `import_cap`, not on the ack, because clamping changes from instant to instant. The schema walk pins an
  allow-list of six ANM-like words in the cloud-to-edge schema, each explained (IC-4 issuer roles and IC-3 import
  basis inside an embedded envelope, two trigger codes reachable only through the refused `ReleaseTrigger`, and
  the `planned_against_anm` hash), instead of Fable's blanket regex. Tests:
  `test_no_cloud_to_edge_message_can_carry_an_anm_signal_or_state`, `test_the_cloud_can_only_tighten_anm`
  (2 000 seeded cases), `test_the_anm_response_is_judged_exactly_against_the_ic1_agreement`, and the simulator's
  `test_a_dno_signal_with_the_link_down_is_enforced_at_once_and_replayed_unchanged`.

### D118 — IC-5 fail-safe: an episode holding a set of triggers from a frozen 17-code table

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-EDGE-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option C of three: (A) a flat code list, (B) single-cause enter and exit events, (C) episodes.
  1. `TriggerCode`, 17 codes in a frozen `TRIGGERS` table (category, IC-3 rank, detector, release):
     `host_override`, `operator_command`, `control_authority_withdrawn`, `no_envelope_in_force`,
     `no_envelope_held`, `clock_unsynchronised`, `chain_integrity_failure` (rank 0); `thermal_comms_lost` (1);
     `dno_trip`, `dno_signal_lost`, `site_meter_lost`, `power_device_comms_lost` (2); `cloud_link_lost`,
     `market_link_lost`, `schedule_stale`, `schedule_exhausted`, `edge_restart` (4).
  2. The edge is in fail-safe exactly when the active set is non-empty; entry needs no cloud message. The
     primary trigger is the lowest rank, ties by enum order, derived and validated.
  3. One exit rule: no trigger active, every non-automatic trigger cleared by an audited release (IC-13
     `command_id` plus audit event hash), and a schedule accepted after the last clear. This is the anti-flap,
     with no invented dwell time.
  4. `FailSafeEvent` is hash-chained per episode: `entered`, `trigger_raised`, `trigger_cleared`,
     `target_changed`, `exited`. `fail_safe_target` computes B1-02's target from the IC-3 fail-safe block, the
     ANM state and the authority stage; with no envelope ever held, and below L2, the edge issues no writes.
- **Reason (Fable).** B1-02 faults overlap (a link drop brings a stale schedule), so a single-cause log gives
  false exits. A computed target cannot drift from IC-3. Edge-alone detection is spec §8(2), and each table row
  becomes one fault-injection test for WP-EDGE-04. The IC-3 rank scale makes triggers and violations comparable.
- **Alternatives considered.** (A) no severity, no exit semantics, no concurrent triggers. (B) the first trigger
  to clear looks like an exit.
- **Consequences.** Left out of v1: partial fail-safe, `thermal_band_breach` as a trigger, OEM protection trips,
  dwell numbers, cyber-intrusion codes, multi-site episodes. Executor's choices inside the decision: Fable's one
  `operator_release` exit rule is split into `host_release`, `neura_ops_release` and `new_grant`, and the cloud's
  `ReleaseTrigger` may release only `NEURA_OPS_RELEASABLE` (`operator_command`, `chain_integrity_failure`). Tests:
  `test_the_trigger_table_is_frozen`, `test_the_primary_trigger_is_the_most_severe`,
  `test_an_exit_needs_every_trigger_cleared_and_a_schedule_after_the_last_clear`,
  `test_the_cloud_may_release_only_neura_ops_codes`, `test_the_fail_safe_target_is_b1_02`.

### D117 — IC-5 acknowledgement: a hash-chained ack per state transition; late is flagged, not refused

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-EDGE-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option C of three, amended: (A) a single accept/reject reply, (B) partial acceptance, (C) a
  lifecycle of acks.
  1. `ScheduleAck` per transition, chained per schedule (`seq`, `prev_ack_hash`), carrying `received_at` (the
     edge's receipt, fixed for the chain), `at`, `late`, `clock_offset_bound_ms` and the envelope.
  2. States: `accepted` (mode), `rejected` (sorted reasons; IC-3 violations exactly when `envelope_violation`
     is a reason), `active` (closed loop only), `shadowing` (shadow only), `superseded` (by hash), `completed`,
     `lapsed`, `aborted` (trigger code). Terminal: rejected, superseded, completed, lapsed, aborted.
  3. Reasons: `wrong_site`, `not_authorised`, `authority_not_granted`, `outside_write_scope`, `id_reused`,
     `out_of_order`, `supersedes_mismatch`, `stale_on_arrival`, `horizon_elapsed`, `deliver_by_mismatch`,
     `envelope_unknown`, `envelope_violation`.
  4. A schedule past its latency budget is accepted and flagged `late`; only one past the staleness threshold
     is refused. Redelivery of the same hash re-emits the latest ack.
  5. `verify_ack_chain` and `governing_timeline` (which schedule governed when; refuses overlap and a
     supersession whose successor did not go active at that instant).
- **Reason (Fable).** `governing_timeline` answers "which schedule governed instant t" for disputes (B1-04).
  `received_at` on the verdict gives the latency sample without a separate `received` message. No partial
  acceptance keeps the edge from authoring a plan. Shadow schedules get the same validation and cannot reach
  `active`.
- **Alternatives considered.** (A) records nothing about activation, supersession or abort. (B) the edge edits
  the plan, the MPC's state-of-energy trajectory is invalid and the executed plan matches no hashed artefact.
  Unamended C: a standalone `received` message adds traffic for one timestamp, and no state for shadow mode.
- **Consequences.** Left out of v1: cloud acks of acks, a cancel message, per-period execution reports
  (WP-TEL-01), ack deadlines, resumption after `aborted`. Executor's choices inside the decision:
  `unsupported_contract_version` is a frame-level refusal (the frame does not validate), not an ack reason;
  `supersedes_mismatch` and `deliver_by_mismatch` come from D115 and D120; `admit_schedule` is the
  one pure function the edge calls on receipt. Tests: `test_the_ack_state_machine`,
  `test_a_bad_transition_or_late_flag_breaks_the_ack_chain`,
  `test_governing_timeline_refuses_overlap_and_a_supersession_that_did_not_happen`,
  `test_a_late_schedule_is_flagged_never_refused`.

### D116 — IC-5 heartbeat and staleness: receiver-clock thresholds in a hashed profile, one pure rule

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-EDGE-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option C of three: (A) staleness from `now - issued_at`, (B) prose thresholds, (C) a profile of
  required parameters plus one pure function.
  1. Every duration is integer milliseconds (`_ms`). `LinkProfile` holds, all required with no default: cloud
     and edge heartbeat intervals, `control_lease_ms`, `schedule_max_age_ms`, `schedule_required_lookahead_ms`,
     `mpc_cadence_ms`, `edge_silent_after_ms`, `resume_hold_ms`, `max_clock_skew_ms`, `max_frame_bytes`.
  2. Invariants: heartbeat < lease ≤ max age; MPC cadence < max age; look-ahead ≥ MPC cadence; edge heartbeat <
     silence threshold.
  3. `link_state(observation, profile)` reports every trigger that fires, from the receiver's own monotonic
     clock: lease expired, no schedule held, schedule aged out, look-ahead insufficient, wall clock untrusted,
     resume hold. Equality always resolves to fail-safe. The cloud's `edge_silent` follows the same rule.
  4. The lease length lives only in the profile, so a faulty cloud cannot extend its own authority.
- **Reason (Fable).** Receiver-clock measurement means skew can neither cause nor mask a lost link. The one
  wall-clock dependency (UTC look-ahead) is made explicit and fail-safe. Edge, cloud, twin and simulator share
  one function, so fault-injection results replay offline. Nothing has a default.
- **Alternatives considered.** (A) compares two clocks, so skew can mask a dead link or trip a healthy one. (B)
  each consumer re-implements the prose and thresholds diverge silently.
- **Consequences.** Left out of v1: per-message lease shortening, negotiated thresholds, in-band profile
  rollover, degraded modes between normal and fail-safe. Executor's choices inside the decision: "no schedule
  held" is D118's `edge_restart`. **Open item:** the spec's staleness "default 2 min" (§10A IC-5 row,
  B1-02) and B2-02's 5-minute MPC cadence cannot both hold under the invariant MPC cadence < max age. Either
  schedules are re-issued every 2 minutes or less, or the threshold is longer
  (`test_the_spec_two_minute_staleness_and_five_minute_mpc_cannot_both_hold`). Tests:
  `test_each_threshold_and_its_equality_resolves_to_fail_safe`, `test_the_profile_is_internally_consistent`,
  `test_no_profile_number_has_a_default`.

### D115 — IC-5 setpoint schedule: an attributed command checked against the IC-3 timeline it names

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-EDGE-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option C of three: (A) reuse IC-3 `ScheduledPeriod` as the wire command, (B) a bare setpoint
  per period, (C) an attributed command bound to the envelope timeline by hash.
  1. `SetpointSchedule`: `schedule_id`, `site_id`, `issued_at`, `deliver_by`, `envelope` (IC-3 `EnvelopeRef`),
     `resolved_hash`, `execution` (`shadow` or `closed_loop`), `supersedes`, `planned_against_anm` (a hash for
     information only), `soe_start_kwh` and contiguous `periods`.
  2. `SetpointPeriod`: whole minutes inside one half-hour; `bess` is `idle` or a `dispatch` whose merchant slice
     plus service deliveries equal its kW exactly, all in one direction; `site_import_cap_kw`; `chiller_band`
     (the command, or not applicable); and the `expected` state of energy, import, export and temperature.
  3. `check_setpoint_schedule(timeline, schedule, stage)` applies IC-3's own per-period rule over
     `timeline_terms_at`, with the import cap tested as the import limit and a chiller band inside the cell band.
     A differential test pins it to IC-3 `check_schedule` on the projection `to_scheduled_periods`.
  4. Authority is staged (ADR-D2): nothing at L0, shadow from L1, closed loop from L2, a closed-loop chiller band
     from L3. One violation or refusal rejects the whole schedule; the edge keeps what it had.
- **Reason (Fable).** Exact attribution gives both the device command and settlement provenance in one record.
  Binding by `resolved_hash` keeps clock logic off the gateway (acceptance 4). Staged authority becomes a
  structural refusal. Whole-schedule rejection keeps "the cloud optimises, the edge holds constraints": the
  edge never invents a plan. Carrying only each period's end state of energy removes inconsistent schedules.
- **Alternatives considered.** (A) a plan, not a command: no import cap, no execution mode, no hash binding,
  and its check needs the full envelope and GB-clock resolution on the gateway. (B) loses attribution, so
  service and merchant slices cannot be checked at the edge.
- **Consequences.** Left out of v1: ramp rates, reactive power, FFR, energy-balance checks (efficiency is not
  in IC-3), multi-envelope schedules, partial acceptance, L4 workload turn-down, a numeric cap on period count.
  Executor's choices inside the decision: Fable's refusal names `timeline_mismatch` and `authority_exceeded`
  take D117's names `envelope_unknown` and `authority_not_granted`; a chiller band where the cell has
  no thermal terms is `thermal_band`. Tests: `test_the_timeline_check_is_ic3s_own_rule` (300 seeded random
  schedules), `test_authority_is_staged`, `test_one_bad_period_rejects_the_whole_schedule`,
  `test_a_chiller_band_must_sit_inside_the_cell_band`.

### D114 — IC-5 message architecture: two direction-typed frame chains bound to a hashed link profile

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-EDGE-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option C of three: (A) independent message types with no common envelope, (B) a common envelope
  with ordering left to the transport, (C) a hash-chained frame per direction.
  1. Two frame models, `CloudToEdgeFrame` and `EdgeToCloudFrame`, each with its own `body` union on `kind`, so a
     body that is illegal in a direction is unrepresentable in that direction's JSON Schema. Three committed
     schemas: the two frames and `ic5_link_profile`.
  2. A flat, required header: `contract`, `contract_version`, `direction`, `site_id`, `sender_id`, `sent_at`,
     `link_epoch`, `seq`, `prev_hash` (`seq == 0` exactly when it is IC-13's `GENESIS_PREV_HASH`),
     `profile_hash` (the content hash of the site's `LinkProfile`) and `peer_head` (the sender's last verified
     frame from the other direction, or not applicable). Identity is `(site, direction, epoch, seq)` plus the
     frame hash. One canonical-JSON frame per transport message, bounded by the profile's `max_frame_bytes`.
  3. The receiver's verdict on every arrival is fixed: `in_order`, `duplicate`, `gap` (delivered anyway, the
     missing range recorded), `late_fill`, `fork` (refused; the whole epoch is refused until a higher epoch),
     `stale_epoch`, `profile_mismatch` (refused, does not renew the lease) and `new_epoch`.
  4. Chain health never gates fail-safe entry and never blocks a valid newer schedule: a refused frame only fails
     to refresh the lease, so the staleness path takes over.
- **Reason (Fable).** One pure mechanism covers every transport's failure modes (MQTT QoS-1 redelivery, HTTP
  retries, store-and-forward replay). Gap-tolerant, so one lost heartbeat cannot block schedules. `peer_head`
  gives each side evidence of what the other had received (B1-04 dispute provenance). `profile_hash` catches
  threshold drift between the two sides on the first frame. The direction split lets a non-Python gateway check
  legality from the schema alone (acceptance 4).
- **Alternatives considered.** (A) every message re-invents ordering and replay, with no command trail. (B)
  transports do not give replay and reorder detection. Strict C (refuse on a gap): one lost heartbeat blocks
  schedules. A per-frame MAC or signature: key management belongs to IC-13 and TLS.
- **Consequences.** Left out of v1: fragmentation, an explicit resend request, per-frame signatures, multi-writer
  chains, compression, anchoring chain heads in IC-13. Executor's choices inside the decision: Fable's
  `classify_frame(state, frame)` is the class `ChainTracker` with the same verdicts; a fork is also `sent_at`
  going backwards, or a late fill that does not link to a neighbour; a late fill is delivered and ordering is
  left to `admit_schedule`; `profile_mismatch` raises no trigger code (staleness catches it if it persists);
  `verify_frame_chain` is the offline audit. Tests: `test_the_receiver_verdicts_in_order_duplicate_gap_and_late_fill`,
  `test_a_fork_is_refused_and_the_epoch_stays_refused`, `test_verify_frame_chain_finds_a_tampered_frame`,
  `test_a_new_epoch_resyncs_a_lower_one_is_stale_and_another_profile_is_refused`.

### D113 — BESS owner and BESS funder are separate fields; the funder stays unknown until IC-4 has a type for it

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation of WP-OPS-01 design choices; adopted by the
  session for Olly that built WP-OPS-01.
- **Context.** The roadmap asks for "BESS owner/funder" per candidate site. IC-4 1.2.0 types a host's
  `site_access_installation_agreement`, which can carry an ownership clause, but no evidence type records a
  funding commitment.
- **Decision.** Two fields. `bess_owner` is evidenced only by a host-issued site access and installation
  agreement plus an owner transcription citing the ownership clause. `bess_funder` is always unknown, with a
  dossier-level funder ask and a `contract_gaps` entry. IC4_INVENTORY gains gap G9 (owner WP-BO-02, next IC-4
  minor, types only).
- **Reason.** Ownership is evidenceable today and should not be thrown away. A funder claim with no document
  type would read as evidence it is not.
- **Alternatives considered.** One combined field (rejected: forces both facts to one bar). Evidencing the
  funder with an existing type such as `lender_reliance_letter` (rejected: distorts IC-4's meaning). Dropping
  ownership until a funder type exists (rejected: discards an evidenceable fact).
- **Consequences.** D-4 criterion (iv) reads both fields, so it stays "inputs_open" for every site until
  WP-BO-02 adds the type.

### D112 — The register is the DPA manifest's sites, extended by an owner file; output is written only where git ignores it

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-OPS-01 session for Olly.
- **Context.** The candidate list is not written down anywhere except the DPA manifest's customers
  (Grundon x6, Harwell, Robert Thomas Farms). Chainergy sites are expected but not identified. A dossier for
  real sites holds customer facts and must not be committed.
- **Decision.** `default_register()` takes every customer site in `CUSTOMERS`, with its SiteModel records
  from the manifest (a record belongs to the site the manifest maps first). An owner file
  `dossiers/candidate_register.json` may add a site or exclude one with a reason shown in the dossier. With
  no Chainergy entry, the dossier carries a dossier-level ask to the partner, never a placeholder site. The
  CLI writes a dossier naming any real site only when `git check-ignore` confirms the path is ignored (no
  git, or not a repo, refuses). `dossiers/` is added to `.gitignore`. The Markdown is rendered only from the
  canonical JSON and carries its hash. Stdout prints counts and paths, never values. A SYNTHETIC-only dossier
  may be written anywhere (the tests pin one as a golden hash).
- **Reason.** The manifest is the only authoritative list of customer sites, and the owner can correct it
  without code. Refusing by default makes a misplaced output impossible rather than unlikely.
- **Alternatives considered.** An owner register only (rejected: empty today, so the dossier would show
  nothing to ask for). A hard-coded list (rejected: rots). A warning instead of a refusal (rejected: a
  warning does not stop a commit).
- **Consequences.** `python -m gridq.ops.dossier --as-of YYYY-MM-DD` exits 3 on a tracked path.

### D111 — The DNO licence area comes from a cited public boundary dataset, looked up locally; it is not the connecting DNO

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-OPS-01 session for Olly.
- **Context.** The data rule lets a public fact fill a field only when retrieved and cited, for example the
  DNO licence area from published service-area data. Placing a site needs its location, which is customer
  data unless the owner registers it from a public source.
- **Decision.** A separate field, `dno_licence_area`, filled by a public-source channel: the owner registers
  a downloaded licence-area boundary GeoJSON (WGS84) with its URL, retrieval date and byte sha256; the
  channel verifies the hash and runs a local point-in-polygon. Exactly one containing area and a 1 km
  margin from any boundary are required; otherwise the field is unknown with an ask. For a real site the
  channel runs only on an owner-registered location (operator-published address or survey); a customer
  postcode is never sent to a web service. `dno` (the connecting operator) stays an ask until a connection
  document or MPAN registration closes it. Tests use synthetic fixture bytes and no network.
- **Reason.** A site may sit behind an IDNO or private network, so the licence area is not the connecting
  operator. Inferring a location from the site id would commit a customer-linked guess.
- **Alternatives considered.** Geocoding the site name (rejected: a guess, and it sends customer-linked
  text to a third party). Filling `dno` from the licence area (rejected: reads as evidenced while the
  connection document is still asked for).
- **Consequences.** No public source has been retrieved for any real site yet: the owner has registered no
  location and no boundary dataset, so the dossier asks for both. The DPA-released postcode route needs a
  local postcode directory and is not built.

### D110 — Connection terms are IC-1 instances, embedded whole and named by their chassis reference

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-OPS-01 session for Olly.
- **Context.** Acceptance criterion 2: connection terms captured as IC-1 instances. IC-1 1.1.0 is frozen;
  IC-12 2.x carries `ConnectionAgreementRef` by content hash.
- **Decision.** IC-1 instances live in `<evidence registry>/connection_agreements/<site_id>/` (git-ignored;
  the registry's non-recursive glob is unaffected). Each admitted instance is embedded whole beside a
  `ConnectionAgreementRef` whose hash must equal the recomputed one. An `EvidenceBacked` source is put to the
  field's IC-4 bar; a `SyntheticArchetype` source is admitted only on a SYNTHETIC- site (an error elsewhere);
  an `Unknown` source evidences nothing. A released record whose `connection.agreement` names no filed
  instance makes the type and terms unknown with a discrepancy ask. Instances that disagree on archetype
  leave `connection_type` unknown with the ask "which connection document is current". An instance whose
  `network_operator` is Unknown does not evidence the DNO.
- **Reason.** Embedding the instance keeps the terms readable; the reference makes them citable by hash.
- **Alternatives considered.** A new IC-1 field or version (rejected: re-versions a frozen contract). Only
  the hash (rejected: captures no terms).
- **Consequences.** IC-1, IC-4 and IC-12 schemas are unchanged (`python -m gridq.contracts --check`).

### D109 — The dossier maps fields to ADR-D4's criteria but states no verdict, score or ranking

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-OPS-01 session for Olly.
- **Context.** Acceptance criterion 3: no go/no-go statement; that is D-4. D-4 still needs to know which
  fields bear on which criterion.
- **Decision.** A D-4 input map per site: for each ADR-D4 criterion (i) to (vi), plus the spec's net-export
  and R13 on-site-generation inputs, which fields it reads and whether they are all evidenced, with the open
  asks and their raw count. Criterion (v) is "not characterised by this dossier". ADR-D4 is cited by path,
  file sha256 and atlas commit (`f0d0d3ce…` at `9a94f16c…`) and its thresholds are not restated. No
  cross-site ordering, aggregate score, boolean or "readiness" wording. The one fixed statement is "This
  dossier makes no go/no-go statement; D-4 decides (ADR-D4)." A test walks every JSON key and string and
  the Markdown for verdict words, exempting only that statement.
- **Reason.** D-4 should not rediscover the field-to-criterion map, and anything that orders sites is a
  proto-verdict.
- **Alternatives considered.** No map (rejected: D-4 redoes the work). A per-site readiness rank (rejected:
  a verdict by another name).
- **Consequences.** The ask ledger orders asks, never sites.

### D108 — Data asks are typed, content-addressed, de-duplicated and ranked by leverage

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-OPS-01 session for Olly.
- **Context.** Every unknown field must carry its data ask. With no DPA registered, the same DPA ask blocks
  every customer-sourced field of every site of a customer.
- **Decision.** `DossierAsk`: request, holder (customer, host, network operator, meter operator, funder,
  partner, Neura owner), holder name, scope (customer, site or dossier), the IC-4 types that would close it,
  and prerequisite ask ids. `ask_id` is the sha256 of the canonical identity, so the same ask from two
  fields is one ask. The DPA ask reads exactly "DPA for <customer> not registered (WP-GRC-05)" and is the
  prerequisite of every customer document ask on a site that is not released. A dossier-level ledger lists
  each ask once: prerequisites first, then by how many unknown fields it serves, then by id.
- **Reason.** The asks are the deliverable for the owner. Typing makes them closable by a named document
  type; the prerequisite shows one owner action (a DPA) unlocks most of the dossier.
- **Alternatives considered.** Free-text asks (rejected: cannot be de-duplicated or ranked). Reusing the
  quote pipeline's `DataAsk` (rejected: mutable, rank-carrying and quote-specific).
- **Consequences.** Today the ledger has 70 asks for 8 sites; the Grundon DPA ask serves 48 unknown fields.

### D107 — The dossier is a frozen, hashed, None-free data product; a field is evidenced only by IC-4 evidence at the DPA guard's bar

- **Date:** 2026-11-26
- **Author:** Fable 5.2 (advisor), under the owner's delegation; adopted by the WP-OPS-01 session for Olly.
- **Context.** Acceptance criterion 1: each field evidenced, or unknown with the data ask. Customer data is
  quarantined until a DPA is registered (WP-GRC-05). Non-negotiable 7: no invented data.
- **Decision.** `gridq.ops.dossier_model`: frozen, strict, closed `ContractModel`s with no None anywhere,
  a canonical content hash and its own schema name and version (`gridq.candidate_site_dossier` 1.0.0). It is
  a data product, not a registered frozen contract. A field is `Evidenced` (typed value plus evidence) or
  `UnknownField` (reason plus at least one ask). IC-4 evidence is admitted only when it is registered, at the
  referenced version, third-party, of a type and issuer role the field admits (`FIELD_EVIDENCE`), scoped to
  the site, VALID as of the dossier date and verified; a synthetic fixture on a real site is an error.
  Anything less is shown as candidate evidence with a specific ask (verify with the issuer, record the
  dates, or the document itself). Values reach a field by three routes: IC-1 instances; the site record,
  read only through the guarded loader, evidenced only where its `evidence_refs` link the field to an
  admissible document; and owner transcriptions (value, cited IC-4 refs, page or clause locator). A released
  record's value without such a link is shown as `recorded_unevidenced` beside the ask; it never counts as
  evidence and never renders in a value column. The build reads no clock.
- **Reason.** A hashed instance is something D-4 can cite exactly. One admission bar, the guard's own,
  avoids a weaker second meaning of "evidenced". Showing a released recorded value tells the owner which
  document to ask for.
- **Alternatives considered.** A plain dict report (rejected: breaks no-None and is unauditable). A
  registered frozen contract (rejected: freezes a product D-4 has not used yet). Admitting unverified or
  validity-unknown documents (rejected: they would read as evidence). Discarding released record values
  (rejected: loses a fact) or labelling them "declared" (rejected: reads as half-evidenced).
- **Consequences.** With no DPA registered, all 8 real sites are fully unknown, with asks.

---

### D106 — Reactive power is a dispatch variable on metered sites: an inscribed P-Q octagon, a kVA connection polygon and the LC14 excess charge

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  1. Built only when the site's half-hourly file meters **both** reactive channels
     (`reactive_import_kvarh` / `reactive_export_kvarh`, or the `_kvar` forms and their aliases) on every
     solved half-hour. No partial fill. Otherwise no variable, row or term exists and the model is the model
     it was, unity-power-factor warning included.
  2. Variable `reactive_comp` = `c_t ∈ [0, min(S, m_t)]`: the kVAr the inverter supplies against the site's
     own reactive, with `m_t = max(RI, RE)` as average kVAr. It has no sign, and the bound forbids
     over-compensation.
  3. Inverter: an inscribed octagon, `k = √2 − 1`: `A + k·c ≤ S − forced` and `k·A + c ≤ S − k·forced`
     (`_add_inverter_pq_rows`). Connection: `AI + k·r ≤ L` and `k·AI + r ≤ L`, with `r = m − c` and
     `L = min(MIC, ASC)`, built only where the half-hour can leave the polygon. Where the baseline is already
     outside it, charging is capped at 0 (`_reactive_connection_screen`, `_add_connection_kva_rows`).
  4. With a published CDCM excess reactive rate on the stack, add the epigraph
     `x ≥ (m − c)·dt − 0.33·AI·dt`, `x ≥ 0` (`_add_reactive_excess_epigraph`). The objective gains
     `− rate·Σw·x` (`_reactive_charge_term`). The bill prices the **exact** LC14 rule (kVArh above 0.33 × kWh
     at times of active import only) before and after, through
     `hv_tariff_model.chargeable_reactive_kvarh`.
  5. Warnings: "REACTIVE MODELLED (…the unity-power-factor assumption is retired for this run)". With a
     series and no rate, a narrow note says no reactive unit rate is declared (EDCM has none). A released
     `assumed_power_factor` is not applied on top of a metered series, and a note says so.
- **Reason (Fable).** Discharging lowers active import, which lowers the 0.33 × kWh allowance, so a battery
  can *create* a reactive charge. Only joint optimisation sees this. The inscribed octagon never overstates
  the inverter's capability and never lets a real MIC breach through. About 2 variables and 3 rows per
  half-hour, against roughly 7 for a two-sided form. The AI = 0 rule is non-convex. The objective
  over-charges there, which is conservative, and the reported £ uses the exact rule.
- **Alternatives considered (Fable).**
  - A two-sided `q` with a ± epigraph: double the rows, and over-compensation is never optimal.
  - A circumscribed octagon: it overstates kVA capability and allows a real breach.
  - An exceeded-capacity epigraph in the objective: no exceeded-capacity rate is on the MILP path. The seam
    is named `_add_exceeded_capacity_epigraph`.
  - Binaries for the AI = 0 rule: 17,520 per candidate.
  - Pricing ENWL's 0.9-lag estimate as a `DerivedAssumption`: it manufactures a saving, and it would
    substitute a method under the data-access rule.
  - Leaving the warning in place: fails acceptance 4.
- **Consequences, and deviations from the advice (recorded here, not silent).**
  - The rate is read from the published row (`network_tariff.reactive_p_per_kvarh`, a `CdcmTariff`) rather
    than from a new flat field, so a reactive rate exists only where a DNO row stands behind it.
  - Both channels are required. A file with one channel keeps the unity warning and says why.
  - **No finance cost line yet.** The reactive avoidance reaches the customer bill (`reactive_before_gbp`,
    `reactive_after_gbp`, both inside the totals) and the solve, but not `COST_LINE_KEYS` or the P&L. The
    warning states that gap. This is conservative: it understates the battery's saving and touches no
    golden dump.
  - The MPC full schedule and the BEFORE-only bill carry no reactive line.
  - `chargeable_reactive_kvarh` lives in the pure `gridq.tariffs.hv_tariff_model`, not `schedule.py`.
  - Exceeded capacity is computed and tested in the pure module (`exceeded_capacity_kva`,
    `billing_determinants_from_series`) but not reported on the bill.
  - The timing across the sizing sweep was not measured here.
  - Tests: `tests/test_reactive_power.py` (11).

### D105 — Every energy band priced as itself: a no-spread envelope, stack-own windows, a typed CDCM/EDCM row, and a pure tariff package

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  1. **Envelope, never a mean.** `tariff_calendar.energy_price_envelope` resolves each half-hour to a quoted
     band. Where it does, buy = sell = bill = that band's rate. Where it does not (a band not quoted, or labels
     with no timing), buy = the highest quoted rate and sell = bill = the lowest. The MILP's energy term is
     `Σw(sell·discharge − buy·charge)dt` (`_energy_revenue_term`), and only when the stack is not
     spread-neutral. A fully resolved stack builds the historic single-price model. The mean-of-bands branch is
     deleted. Each unpriced band is a `DerivedAssumption` on `tariff_stack.import_rates.<band>`, and the bill's
     energy line there is labelled a LOWER BOUND.
  2. **Windows on the stack.** `TariffStack.import_rate_windows: dict[str, BandWindows]`, validated to cover
     exactly the quoted labels, with no overlap and at most one residual band. Without them:
     - red/amber/green take the area's sourced DUoS calendar;
     - day/night keeps 23:00–07:00 as a declared `DerivedAssumption` on `tariff_stack.import_rate_windows`;
     - peak/off-peak is no longer mapped onto DUoS red, so every half-hour is unresolved.
  3. **A typed row.** `TariffStack.network_tariff: CdcmTariff | EdcmTariff | None`, discriminated on
     `methodology`, in the statement's own units (pence, `Decimal`).
     - It wins over the flat `duos_*` lines on the MILP path (`network_rate_series_with_absences`).
     - A CDCM row must agree with any flat line set beside it.
     - An EDCM row refuses red/amber/green lines (EDCM has only super red) and a disagreeing capacity line.
  4. **A pure package.** `gridq.tariffs.hv_tariff_model` imports schemas only, so `optimisation` imports it and
     never the reverse. It holds:
     - the published CDCM algebra (`cdcm_annual_charges`);
     - the half-hourly bill (`cdcm_charges_from_series`, `edcm_charges_from_series`);
     - the LC14 billing determinants (exceeded capacity as the month's maximum, charged for every day of that
       month on the UK clock; chargeable kVArh);
     - H-02 avoidability on every `ChargeLine`;
     - `network_unit_rate_series`.
- **Reason (Fable).** The envelope meets "no mean-of-bands price enters the objective". It also meets D44/D45
  (the run completes, the assumption is declared) and D57 (no gate is added). No cycle touching an unpriced
  half-hour can earn spread from it. Inventing the timing invents the spread as surely as inventing the price.
  The union stops EDCM and CDCM fields coexisting. One pure module serves the published-example tests, the bill
  and H-02, and WP-SIZE-02 can lift the MILP constraint set without untangling it.
- **Alternatives considered (Fable).**
  - Refusing on a missing band: a data-reason block (D44) and a gate (D57).
  - The highest band as the single price: one price cannot be spread-neutral.
  - The release DUoS band as the price: it mixes supplier energy with network rates.
  - Peak kept on DUoS red with a note: it fabricates timing.
  - Flat optional fields for the row: CDCM and EDCM fields could coexist, and there is no home for LLFC.
  - The module under `optimisation/`.
  - Float pounds for rates: penny reproduction becomes rounding luck.
- **Consequences, and deviations.**
  - `resolve_network_tariff` was not built: a row is used only where the stack carries one, so no release
    value is ever promoted into a published-row claim.
  - The bill's standing and capacity lines still read the flat stack fields, which the validator holds equal to
    the row when both are set.
  - `_in_windows` now handles a window that wraps midnight (none of the sourced ones does).
  - Day/night timing is declared once as a `DerivedAssumption`, with no per-solve note.
  - The priced-floor identity holds with the envelope: arbitrage + DUoS == `priced_floor_terms_gbp`.
  - Tests:
    - `tests/test_hv_tariff_model.py`: the published ENWL LV and HV Site Specific Band 1 net revenue to the
      penny, and each band at its own rate;
    - `tests/test_energy_price_envelope.py`: no mean in the objective, no cycle in the unpriced band;
    - `tests/test_optimisation_tariff_calendar.py`.
  - Acceptance 1 (EDCM) stays unmet: data request DR-UW07-EDCM.

### D104 — Tariff provenance is stated per component; "its own tariff stack" is rendered only when it is

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  1. Required `TariffStack.provenance: TariffProvenance` gives `commodity`, `network` and `levies`, each one of
     `own_invoice | published_for_site | by_analogy | assumed | not_charged`.
  2. `TariffStack.evidence` is an optional `EvidenceLink` tuple.
  3. One function, `tariff_calendar.tariff_provenance_phrase`, may say "priced on its own tariff stack", and
     only when every component is own (own invoice, published for the site, or not charged).
     - Otherwise it names each component that is not own and says "not the site's own tariff".
     - An own-invoice claim with no evidence object says so.
  4. The rendered sentences read it:
     - the solved bill basis (opens "Tariff: the site is …");
     - the no-spec BEFORE-only bill basis, which is R8's sentence;
     - the consulting report's tariff finding.
     The view model's BEFORE-only reason no longer calls a modelled bill "the site's own delivered cost".
  5. Site assignments:

     | Sites | commodity | network | levies |
     |---|---|---|---|
     | Grid sites | `own_invoice` | `published_for_site` | `assumed` |
     | Colnbrook, Tanhouse | `own_invoice` | `not_charged` | `assumed` |
     | Harwell | `by_analogy` | `assumed` | `assumed` |
     | Synthetic stacks, Robert Thomas | `assumed` | `assumed` | `assumed` |

     Harwell's header governs: "Nothing in this stack is the site's own price".
- **Reason (Fable).** Every real stack is mixed: an invoiced commodity rate, published network rates and a
  constructed levy stack. A single enum would either force "assumed" onto invoiced commodity or let "own"
  cover assumed levies. A typed field is what the 2026-11-16 register said R8 needed, rather than reading the
  free-text `source`.
- **Alternatives considered (Fable).**
  - One stack-level enum.
  - Evidence-only: it would refuse or silence every site today.
  - Optional with a conservative reading when absent: an absent field invites a rendering default, and the
    major bump is already paid.
- **Consequences.**
  - Because the levies are assumed on every shipped site, no shipped site is described as "priced on its own
    tariff stack". That is true of every one of them today.
  - Tests: `tests/test_tariff_provenance_sentences.py`, including a source scan that no other string literal
    in `src/gridq` can say "own tariff stack".
  - Closes R8 (WP-CORE-03 triage) on the sentence. It moves no £.

### D103 — Every TariffStack states its VAT and levy basis; a VAT-inclusive stack is priced net once; IC-12 goes to 3.0.0

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  1. Required `TariffStack.basis: TariffBasis`, with:
     - `vat` excluded/included, plus `vat_rate_fraction` when included and never otherwise;
     - `vat_attestation` stated_by_record/derived;
     - `levies` itemised/in_unit_rates;
     - `network_charges` itemised/in_unit_rates/not_charged.
  2. Validators:
     - levies in the unit rates refuse an itemised levy line;
     - network charges in the unit rates refuse a DUoS line;
     - `not_charged` must agree with the provenance;
     - a `derived` VAT basis needs its `DerivedAssumption` on `tariff_stack.basis.vat` on the same site
       (`SiteModel._derived_vat_basis_is_recorded`).
  3. Every real site is `excluded`/`derived` under H-01, with the record stating the direction of the error:
     if the invoiced rates were gross, savings are overstated by the VAT rate. Synthetic stacks are
     `stated_by_record`.
  4. One reader: `tariff_calendar.resolved_price_basis`. `quotes/pipeline.py` prices `net_of_vat_stack(site)`
     once and records the site as supplied. A note says the rates included VAT at the record's own rate.
  5. IC-12 chassis 3.0.0 (major: two members became required).
     - `chassis.upgrade_site_2_to_3` takes the basis and provenance as arguments and never defaults them.
     - Every committed site, with exactly those members stripped, hashes to its 2.0.0 pin, so nothing else
       moved.
- **Reason (Fable).** A rate whose basis is unknown cannot be compared with any other. A VAT-inclusive invoice
  rate read as net overstates every saving by the VAT in it. Writing `stated_by_record` on a real site whose
  invoice was never read for VAT would be an invention; `derived` plus the record is the D45 shape.
- **Alternatives considered (Fable).**
  - (b) An optional basis refused late: an unstated basis stays loadable and hashable, which fails "a stack
    without that basis is refused".
  - (c) A basis per line: about ten times the churn, and mixed-basis stacks the MILP sum cannot honour.
  - A release VAT key: no release carries one, and the data rule bars typing one in.
  - `vat="not_stated"`: a basis-less stack by another name.
- **Consequences, and deviations.**
  - `_improve.html` carries the recorded basis and provenance as hidden inputs on a re-run. The advice was
    required selects with no preselected option; that remains on the real-site form.
  - The non-recoverable-VAT case (H-01's 1.20/1.05) stays a separate later flag.
  - Golden priced pins: unmoved.
  - Tag `contracts/ic12-chassis/v3.0.0` is deferred to integration over 2.1.0 (contracts CHANGELOG).
  - Tests:
    - `tests/test_tariff_basis.py` (24);
    - `tests/contracts/test_ic12_chassis.py` (the stripped-dump proof, the 3.0.0 pins, the upgrader, and the
      2-to-3 delta).

### D102 — `UnderwritingEngine` loads the plug-in when it is built; the CLI and app wiring waits for WP-UW-04

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** (b): the `UnderwritingEngine` constructor calls `gridq.extensions.load_plugin("gridq.underwriting")`,
  which is idempotent. Nothing is wired into `cli` or `app`.
- **Reason.** Any process that can produce an underwriting record then has the basis registered, and a fresh process
  without the plug-in already renders the label from the persisted note (pinned by a subprocess test). Wiring
  `load_plugin` into `cli`/`app` at import adds a global side effect for a run path that does not exist until
  WP-UW-04, and risks leaking a registration into `tests/test_chassis_extension_registries.py`.
- **Alternatives considered.** (a) Wire `load_plugin` into `cli` and `app` at import; (c) both. Rejected: cost
  without benefit today.
- **Consequences.** The constructor still registers the basis in any test worker that builds the engine, so the
  underwriting tests snapshot and restore the registry state. `test_building_the_engine_registers_the_plugin_once`
  builds the engine twice to pin idempotency. CLI and app wiring is recorded as a WP-UW-04 item. This narrows
  D99's note that the entry points call `load_plugin` explicitly.

### D101 — The default valuation's gate notes stay verbatim, under the basis verdict and a fixed delimiter

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** (a), applied to the block, not per sentence. The stream's warnings lead with the basis verdict
  ("UNDERWRITING BASIS (underwriting_basis_v1): …", which also states that the D57 assumed-approval register is not
  adopted), then the evidence findings and data asks, then the fixed line
  `DEFAULT-BASIS GATE NOTES, NOT ADOPTED ON underwriting_basis_v1:`, then the default valuation's notes, verbatim.
- **Reason.** (b) and (c) both depend on matching "D57" in text, so a reworded inner warning would slip through
  unmarked, and (b) also deletes provenance from an underwriting record. With (a) no text matching is involved,
  nothing is removed, and the superseded sentences are visibly scoped. The lead sentence's statement that the D57
  register is not adopted covers the site-consent "ASSUMED APPROVED under D57" notes.
- **Alternatives considered.** (b) Drop inner warnings that name D57 and replace them with one sentence; (c) prefix
  each D57-naming sentence with "[NOT ADOPTED ON underwriting_basis_v1]".
- **Consequences.** Fable's trigger was "the gate tightened this stream". The implementation applies the block to
  every stream the evidence gate reads, admitted or tightened, because an admitted stream's D57 notes are equally
  not adopted on this basis. That is an extension of the decision, stated here. Bill-side streams, which the gate
  does not read, carry the lead sentence only.

### D100 — A floor holding two or more services that share the inverter budget is demoted whole until WP-SIZE-03

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** (b), plus the sentence from (c). When two or more `POWER_BUDGET_STREAMS` (DC/DM/DR, BM, local
  flex) are in the priced floor, each one carrying the default valuation's "D57 ASSUMED APPROVED - STACKING
  EXCLUSIVITY AND THE SHARED POWER/ENERGY BUDGET" note is demoted whole to CONDITIONAL. The record names WP-SIZE-03
  as the release condition. If the condition holds but no stream carries a recognisable note, every such floor
  stream is demoted: the check fails closed.
- **Reason.** A floor that knowingly contains MW the budget would have cut is not an underwriting floor, so (c)
  alone is disclosure without enforcement. The whole stream is demoted, not the "cut to X%" fraction, because the
  de-rated MW is not on `OptimisationResult` and a partial figure would be invented.
- **Alternatives considered.** (a) Recompute the allocation from rated MW in the post-pass (rejected: a second
  allocation on the record that can disagree with the valuation's own claims). (c) Sentence only (rejected as the
  whole mechanism). (d) An opt-in parameter in the valuation (rejected: needs a hook `MilpEngine` does not have
  and touches a path every run uses).
- **Consequences.** Fable asked for the note to be matched on a constant from `value_stack.capacity_budget` if one
  exists. None does, so `gridq.underwriting.engine.D57_BUDGET_NOTE_PREFIX` copies the literal prefix; requesting a
  constant is an open item. Tests: `test_shared_power_budget_is_held_out_of_the_floor` (three cases),
  `test_one_budget_sharing_stream_is_not_held_for_the_budget`.

### D99 — The basis lives in a non-CORE package loaded through `gridq.extensions.load_plugin`

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** (a): new package `src/gridq/underwriting/` (`sources`, `rules`, `basis`, `gate`, `engine`) with
  `__version__` (1.0.0) and `register(host)`, registered only through `load_plugin("gridq.underwriting")`. Importing
  it has no side effects.
- **Reason.** Explicit loading keeps the registry deterministic and guarantees the owner and version are stamped
  into `study_basis_extension`. That stamp, with the distinct `BASIS_ID` and the basis dump, is a third key behind
  acceptance criterion 2. Auto-registration on import would make the registry's contents depend on import order and
  could bypass the stamp.
- **Alternatives considered.** (b) Register as a built-in beside D55 in `schemas/study.py` (rejected: a CORE edit,
  and the owner would be `gridq`, so no stamp).
- **Consequences.** An underwriting run's canonical input carries
  `study_basis_extension: {owner: "gridq.underwriting", version: "1.0.0"}`; the built-in bases carry no such key,
  so their hashes do not move. Where the entry points call `load_plugin` is decided by D102.

### D98 — Confidence ceiling LOW; the label names bill-side savings; framing as CORE ships it

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** `confidence_ceiling` is LOW, and the basis refuses any other value. The renderer framing ("Feasibility
  study, not an offer") stays as CORE ships it. LABEL, amended by Fable: "STUDY BASIS (UNDERWRITING_BASIS_V1) —
  EVIDENCE-GATED, MARKET STREAMS ENTER THE FLOOR ONLY ON VALID VERIFIED THIRD-PARTY EVIDENCE, BILL-SIDE SAVINGS ARE
  NOT EVIDENCE-GATED — NOT YET A LENDER-READY OUTPUT".
- **Reason.** LOW matches what CORE's confidence factor already does, so the basis and the output agree, and it is
  truthful while 0 of about 89 rate keys are confirmed (spec A6-04). The proposed label said only evidenced streams
  are priced, which D92 contradicts for bill-side streams.
- **Alternatives considered.** MEDIUM (rejected: CORE would still cap at LOW, so the two would disagree).
- **Consequences.** Defect logged, not fixed (CORE path): the reason text `quotes.confidence._study_basis_factor`
  prints for the cap describes the D46 derived export tariff, which is false for this basis. Request: a per-basis
  reason hook. The engine post-pass also holds every result NOT_BANKABLE, with a warning where the inner engine
  reported otherwise; that is an implementation consequence of "not yet a lender-ready output".

### D97 — `evidence_admission` is a required, hashed field; synthetic fixtures are all-or-nothing and labelled

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** (a): `evidence_admission: "registered_only" | "synthetic_fixtures_test_only"`, required, no
  default, in `input_hash`. `registered_only` refuses synthetic objects, as the IC-4 README requires.
  `synthetic_fixtures_test_only` admits them and adds "SYNTHETIC TEST FIXTURES — NOT EVIDENCE" to `note()` and every
  artefact. Self-issued records are refused in both modes.
- **Reason.** A synthetic run can never share a run id with a registered one, and the positive-control test runs
  through the real code path, with no monkeypatching and no out-of-hash flag.
- **Alternatives considered.** (b) An engine constructor flag outside the basis (rejected: out of the hash).
  (c) Tests monkeypatch IC-4 (rejected: not the real path).
- **Consequences.** Every test fixture is an IC-4 `synthetic_fixture` record issued by "SYNTHETIC-ISSUER (fixture;
  not a real company)". Test: `test_registered_only_refuses_synthetic_fixtures`.

### D96 — Only evidence the site names by content hash can price; `as_of` is required

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** (a): only objects named by content hash in `SiteModel.evidence_refs` can price. The objects are
  handed to the engine as an in-hand set and resolved by hash; an unreferenced object does nothing, and a reference
  whose object is not in hand is ABSENT (CONDITIONAL), never an error and never a scope scan. `as_of` is a required
  basis field with no wall-clock default. `OptimisationResult.evidence_refs` records, per priced stream, the objects
  relied on.
- **Reason.** Nothing outside `input_hash` can move a number, existing hashes are unchanged because the field is
  omitted when None, and expiry becomes a hashed input, so runs stay reproducible.
- **Alternatives considered.** (b) The basis carries the objects inline (rejected: bloats the basis and duplicates
  IC-12). (c) The engine scans a store by scope (rejected: leaks identity).
- **Consequences.** Tests: `test_an_object_in_hand_but_not_referenced_by_the_site_does_nothing`,
  `test_a_referenced_object_not_in_hand_is_absent_not_an_error`, `test_evidence_for_another_site_does_not_cover_this_one`,
  `test_admitted_streams_are_stamped_with_the_evidence_they_rest_on`.

### D95 — Stacking demotes the stream a published rule binds; a pair the rules are silent on is exclusive until NESO answers

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** (c) for the exclusive pair, (a) for the unresolved pair. DFS × BM: DFS Procurement Rules clause
  4.7.6 binds the DFS unit, so where the site holds a BMU registration DFS is demoted to CONDITIONAL with the clause
  quoted, and BM stays. A `bmu_registration` that cannot show a Supplier Base BMU is treated as non-base, with a data
  ask. DFS × DC/DM/DR: exclusive (DFS demoted) until NESO answers, with a data ask to the DFS team. DFS × CM and
  DFS × DNO flex: permitted, same direction only. DC/DM/DR × BM: permitted. Each row quotes its source.
- **Reason.** Demotion is rule-directed, not £-directed. Keeping the higher-£ stream would be optimistic selection,
  and demoting both would punish BM for a rule that does not bind it. A "permitted stacking list" that is silent on
  a pair is not permission.
- **Alternatives considered.** Exclusive pair: (a) keep the higher-£ stream and demote the other (rejected:
  optimistic selection); (b) demote both (rejected: BM is not bound by 4.7.6). Unresolved pair: (b) permitted on a
  literal reading of 4.7.9, with the ask (rejected: silence is not permission).
- **Consequences.** The DFS × DC/DM/DR row costs nothing in v1, because both streams are already UNKNOWN under the
  IC-4 G6 gap. Tests: `test_dfs_is_demoted_where_the_site_holds_a_bm_unit`,
  `test_every_stacking_rule_cites_retrieved_sources`.

### D94 — Size minimums: connection-bounded MW against the quoted minimum; below it, only a stream-scoped aggregation agreement admits

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** (a) as the test, with (b) as corroboration only. The site's connection-bounded MW is compared with
  the quoted minimum (BM 1 MW, DC/DM/DR 1 MW per response unit, CM 1 MW, DFS 0.1 MW on the rounded-down registered
  capacity). Below it, the stream passes only on a VALID, verified `aggregator_agreement` scoped to that stream,
  citing the aggregation route the quoted rule permits. A registration never substitutes for the size check, but a
  registration that contradicts the size verdict is warned. For local flex and wholesale the size gate is not armed,
  carries its data ask and does not itself block; the stream is priced on the counterparty's executed contract
  (`dno_flexibility_contract`, or the VLP `aggregator_agreement`).
- **Reason.** The counterparty's executed contract evidences acceptance without a threshold being invented.
- **Alternatives considered.** (b) A registrar's registration as the size evidence (adopted only as corroboration).
  (c) The site's own unit only, with no aggregation route.
- **Consequences.** The quoted minimums equal the engine's own constants
  (`test_the_quoted_minimums_equal_the_engine_constants`). Tests:
  `test_a_sub_minimum_unit_needs_a_stream_scoped_aggregation_route`, `test_a_unit_at_the_minimum_needs_no_route`,
  `test_dfs_minimum_is_read_on_the_rounded_down_registered_capacity`,
  `test_unarmed_minimums_do_not_block_and_carry_their_data_ask`.

### D93 — Admission: VALID and verified admits; unverified or undated evidence is conditional

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** A sharper (b), in two tiers. VALID on `as_of` and verified (any verifier role IC-4 records, printed
  on the artefact) admits the stream. VALID but unverified: CONDITIONAL, unlock "verify". VALIDITY_UNKNOWN is not
  admitted: CONDITIONAL with a data ask for the missing dates, because an unknown date is not a satisfied date. The
  rule sits in the hashed rule table (`ADMISSION_POLICY`).
- **Reason.** Requiring an independent third-party verifier would make every demo site unbankable on day one; that
  is WP-UW-05's firm tier. Because the rule is in the hashed table, tightening it is a visible version bump.
- **Alternatives considered.** (a) VALID only, verified or not, with unverified labelled. (c) VALID and verified by
  an independent third party (deferred to WP-UW-05).
- **Consequences.** Test: `test_held_but_not_admissible_evidence_is_conditional` (expired, not yet effective,
  unverified, validity unknown).

### D92 — A versioned, content-hashed evidence-requirement table, with four amendments

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** The proposed table (`gridq.underwriting.rules`, version 1.0.0; its hash is a basis field, so it is
  in `input_hash`), amended:
  1. **Row provenance.** Each requirement carries `row_basis`: `published_rule` (citation and quote, in
     `gridq.underwriting.sources`), `spec_route_table` or `underwriting_policy`.
  2. **Export permission.** `g99_approval` is required on every row whose delivery exports (wholesale P415, BM,
     DC/DM/DR), tagged `underwriting_policy`, because no G99 source was retrieved.
  3. **Bill-side streams** (tariff arbitrage, DUoS red band, MIC/ASC headroom, PPA/bill savings) carry no
     third-party evidence gate; PPA/bill savings and MIC/ASC headroom are accounting identities in the code.
  4. **Asset-level gates.** The blanket connection/G99/G100 gate is deferred to WP-UW-02.
  The other rows stand as proposed: CM needs a `capacity_agreement` (prequalification alone is conditional); local
  flex needs a `dno_flexibility_contract` ("conditional until contracted").
- **Reason.** A policy choice is never presented as a market rule. Applying the asset-level gate to every stream
  here would zero every demo site and trip ADR-D3's review trigger for a non-market reason.
- **Alternatives considered.** Require `connection_agreement`/`g99_approval` on every stream (deferred to
  WP-UW-02). Require `g100_compliance` where the site has an export limit (not adopted).
- **Consequences.** A basis naming another table version or hash is refused. The `underwriting_policy` rows
  (customer consent, G99 on exporting rows, the export MPAN on wholesale, the service-level and connection
  agreements) are listed for the owner to confirm. Tests: `test_every_row_says_where_it_comes_from`,
  `test_the_rule_table_hash_is_the_hash_of_the_table`, `test_every_source_is_a_quote_from_a_retrieved_document`.

### D91 — Missing evidence: conditional upside, unlock named; no IC-4 type: excluded at £0 with a data ask

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** (a) + (i). Missing, expired or not-yet-effective evidence gives CONDITIONAL, with
  `unlock_condition` naming each missing type and the status found. Acceptance criterion 1 is read as "contributes
  £0 to the priced floor and never carries FIRM or CONTRACTED firmness", and the tests assert exactly that. A
  requirement IC-4 has no type for (gap G6: DFS unit and Dynamic Response unit registration) gives UNKNOWN, excluded
  at £0, with a labelled data ask and a request to CORE for a types-only IC-4 minor.
- **Reason.** It gives WP-UW-05 its conditional tier for free. Proxying breaks the no-substitution rule and waiving
  recreates D57.
- **Alternatives considered.** Missing evidence: (b) CLOSED/EXCLUDED at £0; (c) UNKNOWN. No IC-4 type: (ii) proxy
  with another type (rejected: substitution); (iii) waive the requirement (rejected: recreates D57).
- **Consequences.** Tests: `test_acceptance_1_bm_without_its_evidence_is_not_priced`,
  `test_removing_any_one_required_object_holds_bm_out`,
  `test_a_requirement_ic4_cannot_type_excludes_the_stream_at_zero`, `test_acceptance_1_through_generate_quote`.

### D90 — A tighten-only gated registry handed to a plain `MilpEngine`; the basis is never passed as `study_basis=`

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.** (b), with the rule "the decorator can only tighten". Every stream of the default registry is
  wrapped so its gate is the stricter of the inner `check_gates` result and the evidence verdict: an inner CLOSED is
  never upgraded, and a D57 "assumed satisfied" OPEN is overridden. The registry goes to a plain
  `MilpEngine(registry=…)`. A thin wrapper (`UnderwritingEngine`) exposes `last_study_basis()`, forwards the other
  hooks and runs the final-result post-pass. The basis is never passed as `study_basis=`, which selects the D46
  export-in-floor solver.
- **Reason.** It is the only option that gates every sizing candidate as well as the final valuation, and it stays
  out of CORE and leaves the default and D57 paths (and their goldens) untouched.
- **Alternatives considered.** (a) A post-solve wrapper alone (rejected: sizing would still choose on ungated
  streams). (c) Edit the engine or solver internals (rejected: moves the default path).
- **Consequences.** `note()` states the known limitation that the dispatch objective still co-optimises DFS
  ungated until WP-SIZE-03. Tests: `test_tighten_never_loosens_any_gate`,
  `test_the_default_engine_is_a_milp_reading_the_gated_registry_on_every_solver`,
  `test_acceptance_3_the_plugin_moves_no_academic_identity_or_default_gate`.

### D89 — IC-3 source: a required input_hash bound to the run pins and the IC-12 quote, with a typed basis label

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SIZE-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option B, hardened, not C, of three: (A) `input_hash` alone, (B) `input_hash` with the run pins and
  typed references, (C) B plus the quote bound by its content hash.
  1. `source.input_hash` is required and is never unknown, not applicable or the hash of empty input.
  2. `source.run` restates the four run pins; `run_id` must re-derive from them (`derive_run_id`, the
     `gridq.provenance` derivation restated in the leaf and cross-tested). `source.assessment` names the IC-12
     quote by id and input hash (a `2.x` range), and that input hash must equal `source.input_hash`.
  3. `source.basis` is a `study` label (basis id, its own banner, a disclosure) or an `underwriting` label; an
     underwriting label whose id names a study is refused (D46). `basis_banner` is what every artifact shows.
  4. `source.connection_agreement` (IC-1) and `source.scenario_set` (IC-2) are references by hash, not
     applicable, or unknown; `source.constraint_library` pins a library version, hash and constraint ids, or is
     unknown. A `live` envelope may hold no unknown reference; `synthetic_fixture` exactly when the site id starts
     `SYNTHETIC-`.
  5. `check_source`, `check_constraint_library` and `envelope_ref` turn the binding into equality checks; a
     comparison against an unknown is `unverifiable`, never a pass.
- **Reason (Fable).** A `QuoteRecord` changes through its lifecycle, so binding it by content hash would break
  IC-15. Each acceptance line becomes an equality check. The IC-2 hash is stated apart because `input_hash` is
  one-way. The typed basis union makes the D57/D46 labelling structural. `run_id` is re-derivable.
- **Alternatives considered.** (A): no way to check which quote, agreement or scenario set an envelope came from.
  (C): breaks whenever the quote record moves on.
- **Consequences.** Left out: a quote content hash, a constraint-library content schema, signatures,
  multi-assessment envelopes, `created_at`, and a non-synthetic underwriting fixture (`underwriting_basis_v1` is
  not implemented). Executor's deviation: the contract is a leaf, so the model checks only the basis id's pattern;
  a test checks every fixture's basis id and banner against `gridq.extensions.study_bases`. The IC-11 surface is
  named at `thermal.surface`, not in `source`. Tests: `test_an_envelope_without_a_source_input_hash_is_invalid`
  (missing, null, unknown, not applicable and malformed, on all four envelopes), `test_an_input_hash_of_nothing_is_invalid`,
  `test_the_quote_input_hash_must_be_the_envelope_input_hash`, `test_run_id_must_derive_from_the_run_pins`,
  `test_derive_run_id_is_the_provenance_derivation`.

### D88 — IC-3 thermal: the IC-11 surface by hash, its binding band, and a band plus commitments in every cell

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SIZE-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option C, hardened, of three: (A) a reference to the IC-11 surface only, (B) one band for the
  whole envelope, (C) the surface reference plus a band and commitments per cell.
  1. `thermal` is a `ThermalEnvelope` (the IC-11 `AvailabilityRef` and the surface's binding IT-safety band,
     restated), not applicable, or unknown.
  2. With a surface, every cell carries `ThermalTerms`: an operating band at the binding point and inside the
     binding band (it may be tighter), and sorted thermal commitments (service, kW at least 1, duration). Without
     one, no cell does. They sit in `CellTerms`, so overrides carry them.
  3. `check_against_surface` proves the commitments against IC-11: the summed kW at the longest duration is
     deliverable from every start (the curve of the cell an event starts in), and no minute of the event touches an
     unavailable or unknown cell (IC-11's whole-event rule). Reason codes include `binding_mismatch`,
     `outside_surface_validity` and `synthetic_surface` (a non-fixture envelope on a fixture surface).
- **Reason (Fable).** The edge, the dashboard and IC-15 get self-contained hash coverage. Per-cell bands now avoid
  a major version later (WP-THERM-12 pre-cool). Two tiers match §8 (the binding band is the hard limit, the cell
  band the operating one). Committed flex is provable against IC-11. IC-11 does not change.
- **Alternatives considered.** (A): the edge would need the surface to know any band. (B): a pre-cool or a
  turn-down window could not tighten the band per cell without a new major version.
- **Consequences.** Left out: rebound kW (the sizer budgets it inside the import limits), turn-up and pre-cool,
  humidity, a per-cell measurement point, notice terms, more than one surface, live refusals. Tests:
  `test_the_dc_commitments_are_provable_against_the_ic11_surface`,
  `test_a_thermal_commitment_above_the_surface_is_refused`,
  `test_a_commitment_that_runs_into_an_unavailable_cell_is_refused`,
  `test_a_commitment_in_an_unknown_cell_is_refused`.

### D87 — IC-3 partition: per-direction power slices and an energy stack stored per cell; flat and per-service views derived

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SIZE-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option B stored, with A and C as derived views, of three: (A) flat limits per cell, (B) a
  partition per cell, (C) per-service windows.
  1. Each cell's `discharge` and `charge` ratings are partitioned exactly into resilience, one slice per committed
     service, merchant and a withheld tranche (with a reason code). The usable energy is stacked exactly: resilience
     floor, energy held for discharge services, merchant, headroom held for charge services, withheld energy.
  2. Every positive slice holds at least `kw x sustain_min / 60` kWh; a slice without held energy, or held energy
     without a slice, is refused. A firm import limit never exceeds `fail_safe.firm_import_limit_kw`; a non-firm or
     DNO-scheduled limit may.
  3. `fail_safe` (firm import limit, resilience floor, markets declared unavailable) is the edge's whole-validity
     fallback; its floor equals the lowest floor in force anywhere in the compiled timeline.
  4. `services` say what each commitment is: stream (an open upper-snake string), sustain time, counterparty role,
     reference, basis (`evidenced` by an IC-4 reference, `assumed_approved` with its confirmation ask, or unknown)
     and the underwritten value with its basis, for IC-15.
  5. Views: `flat_limits`, `merchant_headroom`, `reserved_kw`, `offered_kw`, `service_windows`,
     `underwritten_total` (evidenced and assumed kept apart), `check_schedule` (violations in spec §8 order).
- **Reason (Fable).** Each consumer's test becomes a lookup. "No double-booking" is a closure sum, which is all
  D57 lets it be. The field order follows the §8 safety hierarchy, and the fail-safe is the same cell with the
  slices dropped. "Availability windows" means both the service windows and the withheld tranche. The underwritten
  value is carried for IC-15 and is never an input to any invariant.
- **Alternatives considered.** (A) flat limits only: dispatch cannot tell a service slice from merchant room. (C)
  per-service windows only: closure cannot be checked per cell.
- **Consequences.** Nothing refuses a service on its basis, stream, size or value (D57): tested by
  `test_the_basis_of_a_service_never_gates_its_partition`. Left out: ramp, prices, penalties and tenor, stacking
  rules, probabilistic reserve, performance factors, efficiency, live setpoints, multi-asset (so the
  `gridq.extensions.resources` registry is not read). Executor's choices: `sustain_min` is a duration or not
  applicable (a cooling-only service has no battery slice) instead of allowing 0; withheld energy needs a withheld
  power tranche; a declared service with no slice or commitment anywhere is refused; the envelope-level connection
  reference sits in `source.connection_agreement`. `check_against_agreement` checks the fail-safe and every cell's
  limit against IC-1; the IC-1 time-of-day profile is not checked, because its settlement periods 1-48 do not
  define the 46- and 50-period clock-change days.

### D86 — IC-3 quantities are exact integers, with a stated rounding obligation on the producer

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SIZE-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option A of three: (A) integers, (B) floats with a tolerance, (C) exact rationals. Power is whole
  kW, energy whole kWh of absolute state of energy (never a fraction of capacity), durations whole minutes,
  temperatures tenths of a degree (IC-11 `TenthsC`). Every closure is an exact equality. The producer rounds to
  the safe side: floors and held energy up; limits, ceilings and service slices down; the merchant slice absorbs
  the residual. It snaps by 1e-6 before rounding, and refuses (never relaxes a term) if rounding makes the
  envelope infeasible.
- **Reason (Fable).** The edge may not be Python and should not have to reproduce float repr. The MPC property
  test needs one answer. Closure is exact, as IC-2's weights are. The ledger and IC-15 bind one JSON form. Watts
  would imply false precision; rationals would put bignum arithmetic on the edge.
- **Alternatives considered.** (B) floats: a tolerance on every check, and two languages can disagree. (C)
  rationals: exact but heavy for the edge and for every JSON reader.
- **Consequences.** Left out: SoC fractions, sub-kW resolution, efficiency, ramp rates and reactive power. IC-1's
  MW floats are read through their decimal text (`_mw_to_kw_floor`: 0.3 MW is 300 kW, not 299).

### D85 — IC-3 time base: a GB-clock template with dated overrides, compiled to UTC half-hours and bound by hash

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SIZE-07). Executed for Olly.
  It is not the owner's own words.
- **Decision.** Option C (hybrid) of three: (A) a flat list of absolute UTC intervals, (B) a recurring template
  only, (C) a template plus dated overrides plus a compiled timeline.
  1. The envelope holds a recurring template on `Europe/London`: `seasons` partition the twelve months,
     `day_groups` partition the ISO week, `bands` partition the local day on the 30-minute grid, and `cells` name
     one `terms_id` per (season, day group, band) in that order.
  2. Dated `overrides` replace a cell's terms wholesale over an absolute UTC interval on the grid (maintenance,
     a stress event). They are sorted, disjoint, inside the validity, and two adjacent overrides never carry the
     same terms.
  3. `resolve` maps each UTC half-hour to its local cell (never a local time to UTC, so the 23- and 25-hour days
     need no rule of their own) and step-encodes the result as `ResolvedTimeline`, keyed by each terms' own
     content hash. The envelope carries `resolved_hash`; validation recomputes it.
  4. `valid_from` and `valid_until` are half-open on the grid; `supersedes` names a predecessor by content hash or
     is not applicable; `active_at` refuses two envelopes in force at once, envelopes of more than one site, and a
     successor that starts before what it supersedes.
- **Reason (Fable).** The sizer and the IC-15 legal templates get a compact, readable multi-year instrument bound
  by one hash. The MPC and auctions get per-interval checks with no clock arithmetic. The edge verifies the
  compiled timeline by hash and needs no timezone logic. The clock-change risk is held in one tested function
  (the IC-11 review F1 lesson). The cells align with IC-11's seasons x bands.
- **Alternatives considered.** (A) absolute intervals only: exact but unreadable over a year, and every consumer
  re-derives the recurrence. (B) template only: no way to state a dated outage without a new envelope.
- **Consequences.** Left out of v1: a bank-holiday day type, 5- and 1-minute grids, partial and recurring
  overrides, live DNO setpoints, in-place amendment. Executor's choices inside the decision: one canonical
  encoding per policy (adjacent bands, day groups or seasons with identical terms everywhere are refused, so two
  producers of one policy write one hash); the timeline fixture `ic3_timeline_clock_change_week` lets an edge-style
  consumer work from the timeline alone; a self-supersede needs no guard (a SHA-256 fixed point); `TimeBase` and
  `resolved_hash_of` let a producer compute `resolved_hash` from a draft. Tests:
  `test_the_resolver_matches_an_independent_oracle_at_every_half_hour` (every half-hour of every fixture against
  a reading that shares no code with `resolve`), `test_the_23_hour_day_has_a_six_hour_night_in_utc`,
  `test_the_25_hour_day_has_an_eight_hour_night_in_utc`, `test_active_at_follows_supersession_and_refuses_ambiguity`.

### D84 — `data_centre` is a `ReferenceProfile` subclass with no day shape, loaded explicitly, and selected through a WS-SITE route

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  1. `gridq.datacentre.archetype.DataCentreArchetype(ReferenceProfile)` is a plain subclass with no new fields.
     It is registered under the id `data_centre` with source `archetype_pending_source`. Its weekday,
     saturday and sunday shapes are 48 zeros each, and `day_shape()` raises `SynthesisError`. Its
     `.synthesise(inputs, *, seed, draws)` method is the D73 hook.
  2. Loading is explicit, never on import. `gridq.datacentre.plugin` has `register(host)` and `__version__`,
     and `load_plugin("gridq.datacentre.plugin")` registers it once. No entry point is declared, because
     GridQ's `pyproject.toml` must not name the plug-in.
  3. The archetype is selected through `gridq.datacentre.derive.derive_data_centre_half_hourly(site, inputs,
     *, seed, draws, out_dir, release)`. The function:
     - loads the plug-in and checks the registry entry is a `DataCentreArchetype`;
     - writes the expected series as CORE's `timestamp,import_kwh` CSV, with bands, members and manifest as
       sidecar files;
     - stamps exactly CORE's fields: `half_hourly_basis="modelled"`,
       `half_hourly_reference_profile="data_centre (archetype_pending_source)"`, and a `half_hourly_scaled_to`
       that names the inputs hash and series hash and says no measured total exists;
     - appends a `DerivedAssumption` under `load_data.half_hourly_reference_profile` with ceiling MEDIUM, the
       consumed citations as `sources`, and every data request as `confirmation_ask`.
  4. The record's `derivation_method` is `sourced_default` only when a cited preset was consumed. Otherwise it
     is `archetype_pending_source`.
  5. Modelled energy is never written to `load_data.annual_kwh` or `annual_consumption_kwh`.
- **Reason.**
  - Zero shapes plus the override refuse twice. CORE calls `ref.day_shape()`, so the override fires.
    Any reader of the raw tuples hits CORE's own "zero weight" refusal. A flat filler would have been an
    uncited shape.
  - An entry point is ruled out because a CORE test scans `pyproject.toml` for the plug-in name.
  - Keeping modelled energy out of the annual fields stops it reading as measured, and stops it reopening
    CORE's scale-to-total route.
  - On the record label, Fable's Q1 ruling (sourced only when a preset is used) was preferred to its Q4 view
    (always `sourced_default`). A run built wholly on the user's own figures cites nothing, so calling it a
    sourced default would overstate it.
- **Alternatives considered.**
  - A new chassis field on `SiteModel`: rejected, because it needs a new IC-12 version and edits CORE.
  - Scaling a 48-value shape to a measured total (CORE's route): rejected. It discards the ramp and ambient
    detail, and a pre-operational site has no measured total.
  - Registration on import: rejected, because it breaks registry isolation.
- **Consequences.**
  - Tests in `tests/test_datacentre_registration.py`: `test_registering_touches_no_core_owned_path`,
    `test_unloaded_the_id_is_unknown_and_importing_registers_nothing`, `test_loading_twice_registers_once`,
    `test_core_scaling_route_refuses_the_archetype`, `test_data_centre_is_selectable_in_site_model`,
    `test_a_preset_makes_the_record_a_sourced_default`, `test_a_foreign_data_centre_registration_is_refused`
    and `test_inputs_for_another_site_are_refused`.
  - No CORE path, no `pyproject.toml` line and no golden changed.

### D83 — Uncertainty: a two-layer seeded Latin hypercube, pointwise quantile bands with simulation error, expected = mean

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  1. **Epistemic layer.** Every uncertain input is a stated distribution: `Fixed`, `Uniform`, `Triangular`,
     `Normal`, `Discrete` or `Quantiles`. Each carries either a citation or the user's own statement.
     - The layer is sampled by a seeded Latin hypercube with one value per draw, held for the whole horizon.
     - The samplers use raw 53-bit PCG64 uniforms from a `SeedSequence` keyed by stream. The package applies
       its own inverse transforms; no numpy or scipy distribution sampler is used.
     - The draw count N is required, with no default. It is refused below 72, the smallest N for which the
       binomial order-statistic interval on P05 and P95 exists.
  2. **Schedule slip.** Slip is a stated distribution per phase, sampled as non-negative cumulative increments,
     so phases never reorder.
  3. **Aleatory layer.** The within-half-hour process (D82) runs on a separate stream keyed by
     (seed, draw, day).
  4. **Bands.** Bands are pointwise P05, P10, P50, P90 and P95, computed per interval with numpy's
     `inverted_cdf`. Each band edge has a distribution-free 95 % binomial order-statistic interval.
  5. **Central series.** The central series is the per-interval mean, labelled "expected", never "P50". The
     medoid draw is published as a path that can actually occur.
  6. **Aggregates.** Horizon, year and month energy, peak half-hourly kW and peak one-minute kW are computed
     per draw, then quantiled. Partial periods are labelled.
  7. **Attribution.** First-order variance shares use a correlation ratio, binned on the hypercube strata of the
     same draws. They carry an adjusted-R²-style bias correction, and the remainder and binning bias are
     reported.
  8. **Conditioning.** Bands are conditional on the supplied ambient series.
  9. **Series hash.** The hash covers the rounded members, the expected series and every band. It also covers a
     canonical manifest: seed, N, scheme id, method version, levels, quantile definition, the canonical inputs,
     the ambient hash and the block size.
- **Reason.**
  - The delta method fails at the free-cooling threshold T* and at energisation steps.
  - The mean preserves expected energy and commutes with aggregation from one minute to half-hourly; the
    median does neither.
  - Attribution turns the band into a ranked data request at no extra model runs.
  - Replayable draws avoid storing N × 525,600 values, and give WP-SITE-04 real chronological paths.
- **Alternatives considered.**
  - Analytic error propagation (the delta method): rejected for the reason above.
  - Plain Monte Carlo: rejected, because the hypercube gives better coverage of the input space.
  - Band widths or a default N: rejected under the data rule.
- **Consequences.**
  - The bands are pointwise. They must not be summed, differenced or read as simultaneous.
  - They are uncalibrated until the spec 16.2 post-energisation back-test.
  - S2's Sobol indices are not quoted as this package's own.
  - Tests:
    - `tests/test_datacentre_series.py`: `test_bands_are_ordered_and_bracketed_by_their_simulation_error`,
      `test_aggregates_are_per_draw_then_quantiled`, `test_attribution_ranks_the_data_ask`,
      `test_certain_inputs_give_zero_width_bands`, `test_widening_a_stated_range_widens_the_band` and
      `test_too_few_draws_are_refused`.
    - `tests/test_datacentre_model.py`: `test_latin_hypercube_has_one_point_per_stratum_and_is_seeded`,
      `test_distribution_families_invert_as_stated` and `test_order_statistic_intervals`.
    - The whole of `tests/test_datacentre_reproducibility.py`.

### D82 — One-minute output: half-hourly primary, an exactly conditioned AR(1) per day, the exact model re-centred per block

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  1. **Primary series.** The half-hourly series is primary. It is described as "the model evaluated at
     half-hourly mean load and ambient".
  2. **One-minute IT load.** One-minute IT load is `L_min = L_hh × (1 + e)`. The deviation `e` is a stationary
     AR(1) over each calendar day, conditioned exactly on all 48 block means being zero, by Matheron's rule
     (`e = x − ΣAᵀ(AΣAᵀ)⁻¹Ax`).
  3. **Dips.** An optional dip train can be added: a square wave on the absolute minute index, with a phase per
     draw, block-de-meaned.
  4. **Keying.** Each day is keyed by `(seed, MINUTE_STREAM, draw, date ordinal)`, so any window is a slice of
     the full series.
  5. **Capacity.** A block whose minutes would leave [0, energised IT capacity] has its deviations shrunk by the
     smallest factor that fits. The block is counted.
  6. **One-minute facility power** (Fable's ruling on the Q1/Q2 conflict, option ii):
     - It is the exact component model at `(L_min, T_hh)`, plus a per-block constant
       `delta = P_hh − mean(f(L_min))`. This is the Jensen gap.
     - Block means therefore equal the half-hourly value to float tolerance, in every draw and for every
       component.
     - The gap is emitted, and flagged only against a user-set threshold.
     - Cooling capacity is flagged on the value before the shift. The shifted series is then capped, and any
       residual is reported.
     - A stated first-order cooling lag is applied with a daily reset, then re-centred.
  7. **Required inputs, all with no default.** Variability mode (`none`, `ar1` or `ar1_dips`), SD fraction,
     correlation time, and dip depth, duration and period.
  8. **The only preset.** S1 Table 3.1 SD/Pavg, chosen by named workload row and labelled
     `node_level_upper_bound`.
- **Reason.**
  - **Against one-minute primary.** Computing one-minute output for the whole horizon would make cost scale with
    horizon × draws. It would also make the half-hourly results depend on unsourced minute parameters.
  - **Against a deterministic step.** A step alone gives the WP-SITE-04 adequacy simulator no ramps.
  - **For exact conditioning.** Exact conditioning, unlike plain de-meaning, puts no jumps at block edges.
  - **Against a tangent-linear minute model.** It is wrong where the model has kinks, and kinks are present: the
    cooling cap and floor, the UPS curve knots, and the dip train. It would also need a second, hand-derived
    code path.
  - **Random access.** WP-SITE-04 can pull event windows only.
- **Alternatives considered.**
  - One-minute primary, with half-hourly as its mean (Fable's first Q1 constraint): withdrawn by Fable in the
    conflict ruling.
  - Linearisation about the half-hourly point.
  - A step with no variability.
- **Consequences.**
  - The midnight restart of the AR(1) is an independent restart, and a documented artefact.
  - WP-SITE-04 consumes member draws, never band edges.
  - The 16.2 back-test fits WP-THERM-04 coefficients on half-hourly means.
  - Tests in `tests/test_datacentre_series.py`: `test_one_minute_reproduces_every_half_hour_exactly`,
    `test_any_window_is_a_slice_of_the_same_series`,
    `test_jensen_gap_is_reported_and_flagged_only_against_a_stated_threshold`,
    `test_minutes_stay_inside_energised_capacity_by_shrinking_and_counting`,
    `test_no_variability_is_a_step_and_has_no_jensen_gap` and `test_windows_outside_the_horizon_are_refused`.
  - Also `tests/test_datacentre_reproducibility.py::test_one_minute_windows_reproduce_and_do_not_depend_on_call_order`.

### D81 — Facility load is a component model; PUE is an output, never an input

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-SITE-01). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  1. **Facility load.** `P_fac = L + P_ups_loss + P_dist + ΣP_aux + P_cool`, and `PUE = P_fac / L` is an output
     (NaN where L = 0).
  2. **IT load.** `L` is built from the ramp phases (racks × rated kW per rack, energisation date, fill period,
     slip) times the stated within-day utilisation shape and level.
  3. **Cooling form.** Cooling takes the WP-THERM-04 method v1 §6 forms:
     - C1 is `b0 + b1·x + b2·T`; C2 adds `b3·x·T`; C3 adds `b4·x²`.
     - T is the wet bulb with towers or evaporative heat rejection, otherwise the dry bulb.
     - There are two regimes, split at T*, when the topology includes free cooling.
     - Values are clipped at 0 and at the declared capacity, with a flag on every clip.
  4. **Cooling load basis.** The user declares the basis: `x = L` (Path E), or `x = Q` (Path T), where Q is L
     plus the in-hall share of UPS loss, distribution loss and in-hall auxiliaries.
  5. **Cooling coefficients.** Coefficients are given directly, or solved by least squares from design duty
     points, once per draw. The solve is refused when the points cannot identify the form, and its residuals
     are reported.
  6. **UPS.** The UPS works in loss space. Datasheet efficiency points become loss kW per module, interpolated
     against the load fraction per module. Modules in service are set by N, N+1 or 2N. Below the lowest point
     the loss is held as a stated upper bound; above full load it is extrapolated and flagged as an overload.
  7. **The only preset.** S1 Table 4.3's UPS efficiency range, chosen by space type. It is opt-in, labelled as
     US-derived, given a uniform shape that is labelled as this package's choice, and stated to make redundancy
     electrically inert.
  8. **Rack density.** Rack density enters only as phase capacity. No density-versus-topology check is made,
     because no cited threshold exists.
- **Reason.**
  - It is the only form that meets spec 16.2 through one code path, before and after energisation.
  - It gives redundancy a real mechanism.
  - Every number in it is either a user input or a quoted figure.
  - A PUE(T) input would need an uncited curve.
  - A designer holds duty points, not coefficients.
  - Extrapolating efficiency instead of loss misbehaves at light load.
- **Alternatives considered.**
  - A PUE-versus-temperature curve as input: rejected, because it cannot be back-tested and has no open source.
  - A first-principles thermodynamic plant model: rejected, because it adds parameters with no open source.
- **Consequences.**
  - Excluded figures:
    - no S3 80 % or ±10 % figure, and no Uptime 1.56;
    - no ASHRAE number;
    - no default for any coefficient, T*, distribution loss, auxiliary or UPS curve;
    - the spec's 7.8 and 120–190 kW figures are not enforced as thresholds.
  - Tests:
    - `tests/test_datacentre_model.py` (13 tests), among them
      `test_ups_curve_is_interpolated_in_loss_space_and_bounded_honestly`,
      `test_redundancy_sets_the_modules_in_service`, `test_cooling_bounds_are_flagged_not_silent`,
      `test_free_cooling_switches_regime_at_t_star_on_the_driving_temperature`,
      `test_the_heat_basis_adds_in_hall_losses_to_the_cooling_load` and
      `test_duty_points_are_solved_by_least_squares_and_refused_when_unidentifiable`;
    - `tests/test_datacentre_data_rule.py` (7 tests).

### D80 — A test outside the read waiver reaches customer data only through the guard, and waits for the DPA by a named skip

- **Date:** 2026-11-26
- **Author:** The session for Olly that reverted the waiver growth from the p1-foundations integration. It
  was directed by the owner's brief ("the waiver may only shrink ... agents may only tighten"). The design
  below is the session's, not the owner's own words.
- **Context.** The integration grew the WP-GRC-05 legacy read waiver from 12 to 14 modules so that two
  merged modules could keep reading quarantined site records. A scan then found the hole was wider. At the
  merge, 13 test modules outside the 12-module waiver opened files under a customer-data root directly
  (examples/real/sites, data/sites), with no DPA check and no access-log entry. The guard caught only reads
  through the loaders that call `check_load`.
- **Decision.**
  1. The waiver is never grown to make a test pass. It is back at the 12 modules WP-GRC-05 listed.
  2. A test whose rule is about structure moves onto a labelled SYNTHETIC fixture (`SYNTHETIC-` site ids,
     "not a real" names, no customer token, figures that are library values or invented).
  3. A test that is about the real record keeps its assertions, and reaches the record only through
     `tests/_dpa_support.py`: `dpa_released`, `dpa_released_site` or `dpa_released_sites`. These ask the
     guard's own `check_load` before a byte is read. Until the customer's DPA is registered they skip with
     `quarantined: no DPA registered for <customer> (WP-GRC-05); runs once the DPA is registered`, and then
     the test runs by itself. A test over a whole set is released whole or skipped whole, so it can never
     check a subset and pass.
  4. Golden identities are not re-minted to fit. IC-12's nine campaign-record pins stay byte for byte behind
     the gateway. The breadth they gave moves to five SYNTHETIC sites, whose pins were minted the way the
     originals were: by running the bfeafe4 models (`gridq_clean_baseline` schemas) over them.
  5. `tests/governance/test_no_direct_quarantined_reads.py` enforces this statically over every customer
     root in the manifest. That is wider than the brief's examples/real: data/sites was the same hole.
- **Reason.** A waiver entry widens access to customer data, which only the owner may do. A named skip
  widens nothing, states its blocker on every run, and lifts itself when the owner registers the DPA. A
  synthetic fixture keeps a structural test running on every run without the customer's data.
- **Alternatives considered.** Keep the waiver at 14 for the owner to decide (rejected: an agent may not
  widen a data gate, even pending a decision). Delete the real-record pins and tests (rejected: they lose the
  check the owner will want once a DPA exists). Fabricate DPA evidence for the real sites in tests (rejected:
  `fixture_dpa` refuses real customers by design). Scan examples/real only (rejected: data/sites is a
  customer root with the same exposure).
- **Consequences.** 82 real-record tests skip in the fast tier (94 skipped in all, against 12 before), each
  naming its blocker, until the grundon and harwell DPAs are registered. Registering one releases that customer's tests with no code
  change. Three SYNTHETIC-twin tests keep the record-disclosure detectors exercised meanwhile. The waiver's
  12 modules are unchanged and still due to shrink.

---

### D79 — WP-UW-03's `FinancialCase` additions are released as IC-12 2.1.0, an additive minor; the 2.0.0 schemas are archived, never edited

- **Date:** 2026-11-26
- **Author:** The integration session for Olly, which merged WP-CORE-03 into WP-UW-03 (`30acc23`) and then
  WP-UW-03 into `roadmap/p1-foundations`. The release was directed by the integration brief. It is not the
  owner's own words.
- **Context.** WP-UW-03 was built while WP-CORE-03 froze the chassis, and D75 point 5 kept `SiteModel`,
  `OptimisationResult` and `QuoteRecord` untouched. But `QuoteRecord` embeds `FinancialCase`, so the D75 to
  D77 members reached the IC-12 `QuoteRecord` schema anyway. After the merge, `python -m gridq.contracts --check`
  reported `DRIFT: QuoteRecord no longer matches ic12_quote_record.v2.0.0.schema.json`, and two tests failed on
  it: `tests/contracts/test_ic12_chassis.py::test_check_covers_the_three_chassis_schemas` and
  `tests/test_chassis_extension_registries.py::test_registering_touches_no_core_owned_path`, whose step 3 runs
  `--check`.
- **Decision.**
  1. IC-12 moves to **2.1.0** (`chassis.CONTRACT_VERSION`; `SUPPORTED_VERSIONS = ("1.0.0", "2.0.0", "2.1.0")`;
     tag `contracts/ic12-chassis/v2.1.0`). The release name stays `gridq_chassis_v2`.
  2. The three `v2.0.0` schema files are archived byte for byte in `contracts/__main__.ARCHIVED`, at their
     SHA-256 under the tag `contracts/ic12-chassis/v2.0.0`. `--write` refuses to change them.
  3. `v2.1.0` is rendered for all three models. `SiteModel` and `OptimisationResult` are byte-identical to
     2.0.0. `QuoteRecord` gains only optional, `null`-defaulted members (listed in the contracts `CHANGELOG.md`)
     and eight new definitions, and two descriptions are reworded.
  4. `test_2_1_0_is_2_0_0_plus_the_stated_optional_members` pins that delta exactly: anything else that moves,
     a member that becomes required, or a stated change that did not happen fails it.
- **Reason.** A minor, not a major: no existing member changed type or meaning, nothing became required, and
  every 2.0.0 instance is a valid 2.1.0 instance with the same identity hashes (D78). D71 point 4 made 2.0.0 a
  major only because a meaning moved, and stated that additions alone would not be. The contracts README rule
  "a new field on an object is a major" protects `ContractModel` hashes, which cover every field; the chassis
  hash sites are pinned separately (`test_ic12_chassis.py` §3, D78's identity tests) and none moved.
- **Alternatives considered.** Re-render the 2.0.0 file in place (rejected: a tagged schema is never edited,
  and `--check` exists to stop exactly that). Revert the `FinancialCase` additions or hide them from the schema
  (rejected: they are WP-UW-03's deliverable and are on the record). Call it 3.0.0 (rejected, reason above).
- **Consequences.** A 2.0.0 reader refuses a 2.1.0 record that carries a financial case, because the new
  members dump as `null` while unset (unlike the 2.0.0 members, which D72 omits). No golden re-pinned; no
  archived file edited. `tests/contracts/test_schema_delta_v110.py` now considers only `v1.0.0` archive entries,
  so a later archive is not mistaken for a 1.1.0 re-version (the same guard WP-GRC-09 added for IC-4 1.2.0).

### D78 — Lender terms enter the run's identity; runs without them keep theirs byte-for-byte; the sizer does not see them yet

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-03). Executed for Olly. Closes
  N25 (≡ MD-F-14).
- **Context.** `finance/financial_model._hash_input` built `case_id` without the debt terms or the price
  structure, so gearing 60 vs 40, or fixed price vs capacity fee, shared a `case_id` (N25). WP-UW-03 also adds
  fields to `FinancialCase` and three of its parts, and the quote `input_hash` dumps the whole case. A
  careless addition would move the hash, and so the `quote_id`, of every historical run without terms, and
  golden pins must not move for those runs.
- **Decision.**
  1. `_hash_input` takes `debt_terms` (only when injected by the caller), `lender_terms` and `price_structure`.
     Each key is added **only when present or non-default** (`financial_model.py:623`, `:625`, `:627`), so a
     run without them hashes exactly the payload it always did.
  2. The quote's canonical dump (`quotes/assembly._canonical_financial_case_dump`) always excludes the fields
     that are **derived echoes** of inputs already in the hash: `dscr_summary.{status, unavailable_reason,
     cashflow_case}`, `covenant_status.{source, lock_up_dscr, lock_up_passes, not_evaluated_reason}` and
     `debt_assumptions.{principal_gbp, sized_by}`. `lender_terms` is popped when `None` (the D42
     pop-if-None policy) and kept when supplied, so terms enter the `quote_id`.
  3. Warning text on the legacy paths (P90 case, credit mapping, demo `DebtTerms`) is byte-identical, because
     warnings are part of the canonical input.
  4. Lender terms are **not** threaded into `MilpEngine` sizing. Exposing the covenant to the sizer through
     `OptimisationResult` is WP-UW-09's acceptance line, and `OptimisationResult` is being re-frozen by
     WP-CORE-03, so this package leaves it untouched.
- **Tests.** `tests/test_lender_terms.py::test_debt_terms_lender_terms_and_price_structure_split_the_case_id`
  (each payload line deleted in turn turns it red) and `::test_a_run_without_terms_keeps_its_historical_case_hash_input`;
  `tests/test_dscr_on_every_run.py::test_a_run_without_terms_keeps_its_pre_wp_uw_03_quote_identity` (the dump
  equals the record minus exactly the new fields) and `::test_lender_terms_enter_the_quote_identity`. Golden and
  hash suites unchanged and green.
- **Direction of effect.** No figure moves on any run without terms; no golden re-pinned. A run with terms gets
  its own `case_id`, `input_hash` and `quote_id`.
- **Alternatives considered.** Hash every new field (rejected: re-keys every historical run for no information,
  since each echo is a function of inputs already hashed); thread the terms into the sizer now (rejected: it
  needs an `OptimisationResult` change during another agent's re-freeze, and is WP-UW-09's deliverable).

### D77 — The terms choose the cash-flow case; straight-line and a target-sized sculpt join the annuity

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-03). Executed for Olly.
  Advances H-03.
- **Context.** The DSCR was always measured on P90 operating cash flow, with an annuity or a P90/P50 flat
  sculpt at a fixed gearing. A term sheet names the case the lender sizes on and the repayment shape, and a
  sculpted facility is usually sized by a target DSCR rather than by gearing alone.
- **Decision.**
  1. `LenderTerms.cashflow_case` selects the CFADS series the debt is sized and tested on: **P50** = the P50
     free cash flow before debt, **P90** = the P90 free cash flow before debt (the historical basis), **LENDER**
     = the contracted P90 case bearing every operating cost (W8). The DSCR basis label follows the case
     (`P90 PRE-TAX`, `P50 PRE-TAX`, `LENDER CASE PRE-TAX`) and `DscrSummary.cashflow_case` records it.
  2. `amortisation` is one of: **annuity** (unchanged maths, and the same measurement as the legacy
     `DebtTerms` path for the same figures); **straight_line** (equal principal plus interest on the opening
     balance, `debt.straight_line_schedule`); **sculpted** (service shaped to the chosen case at a flat DSCR).
  3. A sculpted profile with a `sculpting_target_dscr` **sizes** the debt as well as shaping it:
     principal = min(capex × max gearing, PV(case CFADS / target, at the debt rate, over the tenor))
     (`debt.target_sculpted_schedule`). When the target binds, every year's DSCR is the target; when the cap
     binds, the flat DSCR is solved as before and sits at or above the target. `DebtAssumptions.sized_by`
     (`dscr_target` or `gearing`) and `principal_gbp` say which bound bit.
  4. The existing refusals hold for every case: a tenor past the horizon is unavailable, not a partial minimum
     (N10); a non-positive CFADS year inside the tenor falls back to the annuity and says so.
- **Tests.** `tests/test_lender_terms.py::test_the_cashflow_case_selects_the_series_the_dscr_is_measured_on`,
  `::test_annuity_terms_reproduce_the_legacy_debt_terms_measurement`, `::test_straight_line_amortisation_repays_equal_principal`,
  `::test_a_sculpting_target_sizes_the_debt_when_the_cfads_supports_less_than_the_cap`,
  `::test_a_sculpting_target_never_lends_past_the_gearing_cap`, `::test_a_tenor_past_the_horizon_is_unavailable_with_its_reason`.
- **Direction of effect.** None on runs without lender terms. With terms: the LENDER case counts contracted
  revenue only, so it usually gives the lowest DSCR of the three for the same loan, and a binding sculpting
  target lowers the principal (and raises equity) relative to capex × gearing.
- **Alternatives considered.** Keep P90 as the only case (rejected: a term sheet sizing on contracted revenue
  would be measured on the wrong series); size by target only (rejected: a lender also caps gearing, and
  exceeding the stated cap would lend more than the terms allow).

### D76 — Covenant tiers come from the lender's terms, with no fallback to the library placeholder; only an annual test is measured

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-03). Executed for Olly.
  Advances H-05.
- **Context.** The library holds one covenant key, `dscr_covenants.min_p90_dscr`, a placeholder. A term sheet
  states an event-of-default threshold and usually a distribution lock-up above it, and states the test period.
- **Decision.**
  1. `LenderTerms.covenant` (`DscrCovenantTerms`) states `default_dscr`, `lock_up_dscr` or both; an empty block
     is refused, and a lock-up below the default is refused.
  2. With lender terms, the tiers come **only** from the terms (`finance/lender_terms.covenant_tiers_from_lender_terms`,
     called at `financial_model.py:231`). Terms that state no covenant leave the verdict **not evaluated**, with
     the reason on `CovenantStatus.not_evaluated_reason`. The library placeholder is never substituted for a
     lender's silence: it would put a threshold on the record that no lender set.
  3. `CovenantStatus` records `source` (`lender_terms` or `assumption_library`), `lock_up_dscr` and
     `lock_up_passes` beside the default test. Runs without lender terms keep the library covenant exactly as
     before, labelled `assumption_library`.
  4. `calculation_period` is recorded. The model is annual, so a semi-annual or quarterly test is **refused**:
     `DscrStatus.period_not_modelled`, no debt sized, and a reason saying an annual ratio is not substituted.
  5. Supplied terms can make a case bankable only when they come from a term sheet **and** state a default
     covenant (`lender_terms_are_firm`). Indications and synthetic terms keep the case not bankable.
- **Tests.** `tests/test_lender_terms.py::test_covenant_tiers_come_from_the_terms_with_a_lock_up`,
  `::test_terms_without_a_covenant_are_never_tested_against_the_library_placeholder` (both red if line 231 is
  replaced), `::test_a_semi_annual_test_period_is_refused_not_annualised`, `::test_only_a_term_sheet_with_a_default_covenant_is_firm`,
  `::test_a_covenant_block_states_a_threshold_and_lock_up_sits_above_default`.
- **Direction of effect.** None on runs without terms. With terms, the covenant verdict may move either way,
  since it is now tested against the lender's own thresholds.
- **What stays open.** Sub-annual covenant tests need sub-annual CFADS (WP-UW-09).
- **Alternatives considered.** Fall back to the library covenant when the terms state none (rejected: an
  invented lender figure); annualise a semi-annual test (rejected: a substituted method, under the binding
  data-access rule).

### D75 — `LenderTerms` is the typed lender-terms input, and every DSCR on the record carries a status and a reason

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-03). Executed for Olly. Closes
  blocker B001.
- **Context.** B001: `min_dscr=None` on all 12 latest gb_v1_12 runs. No real site has a credit band, so the M9
  credit mapping supplies no rate or tenor, and neither the CLI nor the app could supply debt terms. The bare
  `None` did not say why.
- **Decision.**
  1. A frozen `LenderTerms` model in its own module (`schemas/lender_terms.py`), exported from `gridq.schemas`:
     tenor, all-in rate (percent), maximum gearing (percent of capex), amortisation, cash-flow case, covenant
     test period and provenance are **required**; the covenant and a sculpting target are optional. **No field
     has a default figure.** Percent fields refuse fractions (the F-52 guard).
  2. Provenance is part of the terms: `source` is `term_sheet`, `lender_indication` or **`synthetic`**, with a
     reference, optional date and optional evidence hash. Synthetic terms are labelled on every surface that
     shows the DSCR (record warnings, app finance panel, underwriting pack, CLI label), so a demonstration can
     never read as a lender's offer.
  3. Entry points: `gridq run --lender-terms FILE` (JSON or YAML; an invalid file exits 2 before solving); the
     app's optional lender-terms block on the wizard and the real form (all blank means no terms, a half-filled
     block is a 400); `generate_quote(lender_terms=)` and `run_financial_model(lender_terms=)`. Supplied terms
     replace the credit mapping; passing both `lender_terms` and the legacy `debt_terms` is refused.
  4. `DscrSummary` gains `status` (`DscrStatus`: measured, no_debt_terms, all_equity, tenor_beyond_horizon,
     period_not_modelled, no_service_years), `unavailable_reason` (None only when measured) and
     `cashflow_case`. A run without terms reads "No lender terms supplied: …", plus the credit band when the
     credit mapping could not help either. Never a silent `None`, never a default number.
  5. Changes to `FinancialCase` and its parts are additive, defaulted fields only. `SiteModel`,
     `OptimisationResult` and `QuoteRecord` are untouched (WP-CORE-03 is re-freezing them).
- **Tests.** `tests/test_lender_terms.py` (schema, engine, identity; synthetic terms only) and
  `tests/test_dscr_on_every_run.py` (pipeline, CLI, app and register, through the production entry points; each
  call site mutation-checked red).
- **Direction of effect.** No figure moves on a run without terms; each such run now states why it has no DSCR.
  A run with terms shows a measured DSCR (or a stated refusal).
- **What stays open.** Real lender terms per site are a data ask, not a code gap. The demo still uses its
  illustrative `DebtTerms`.
- **Alternatives considered.** Put the terms on `SiteModel` (rejected: they are a financing input, not a site
  fact, and `SiteModel` is being re-frozen); library default terms (rejected: an invented lender); extend the
  `DebtTerms` dataclass (rejected: no validation, no provenance, no place for a covenant or a case).

### D74 — A study basis is a registered class meeting a stated contract; a plug-in basis pins its package in `input_hash`

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-CORE-03). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  1. `gridq.schemas.study.NamedBasis` is the structural contract every basis meets (`BASIS_ID`, `LABEL`,
     `basis_id`, `confidence_ceiling`, `note()`, `model_dump`). `basis_class_problems` is the check a registry
     applies: a `GridQModel` subclass, a `Literal` `basis_id` equal to its registered id, a non-empty `LABEL` that
     no other basis uses, a `note()`, and a `confidence_ceiling` that defaults to `low` or `medium`, never `high`.
  2. `quotes/assembly._basis_registration` refuses a basis object whose id is not registered, or whose class is
     not the registered class. A basis that was never registered cannot price a record.
  3. The whole basis dump still enters `input_hash` under `study_basis`, unchanged. For a basis registered by a
     package other than `gridq`, the canonical input also carries `study_basis_extension: {owner, version}`,
     so an upgraded plug-in cannot reuse a `run_id`. The key is absent for the built-in bases, so their hashes
     do not move.
- **Reason.** D46 makes the basis label the whole mitigation for a second pricing basis. A registry that let a
  package register an unlabelled, uncapped or duplicate-labelled basis would open that hole from outside CORE.
  `underwriting_basis_v1` (WP-UW-01) registers beside the study bases through this path.
- **Alternatives considered.** Require a subclass of `StudyBasis` (rejected: it would give every plug-in basis
  the D46 export-pricing field and the D55 inheritance, which an underwriting basis must not have). Hash only
  the basis id (rejected: a plug-in could change what its basis does under the same id).
- **Consequences.** `STUDY_BASIS_CLASSES` and `STUDY_BASIS_LABELS` are unchanged and still list the built-ins;
  `study_basis_label` falls back to the registry. Tests: `tests/test_chassis_extension_registries.py`
  (`test_a_different_basis_is_a_different_input_hash`,
  `test_the_basis_key_is_in_the_canonical_input_and_removing_it_collides`,
  `test_a_plugin_basis_also_pins_its_package_version`, `test_an_unregistered_basis_cannot_reach_a_record`,
  `test_a_basis_weaker_than_the_built_ins_is_refused`). Registering a basis adds no gate to any stream on the
  D57 basis: D57 is untouched.

### D73 — Extension registries: `gridq.extensions`, plug-ins by entry point, an id is never overridden

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-CORE-03). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  1. Four registries in `gridq.extensions`: `load_archetypes`, `study_bases`, `resources`, `constraints`. One
     generic `Registry` (`extensions/registry.py`): ids match `^[a-z][a-z0-9_]*$`, an id is registered once and
     never overridden, each registration records its owning package and version, the owner `gridq` is reserved
     for built-ins, and every read is ordered by id, not by registration order.
  2. A plug-in is a module with `register(host)`. It is found through the entry-point group
     `gridq.extensions` in the plug-in's own packaging metadata, or loaded by name with
     `gridq.extensions.load_plugin`. Discovery is lazy, on the first miss or the first listing. A plug-in that
     raises is rolled back whole and the error is raised (`ExtensionLoadError`); it is never skipped.
  3. The built-ins register from the modules that already own them: `ingestion/hh_synthesis.py`
     (`PROFILE_LIBRARY`), `schemas/study.py` (the two study bases), `gridq.extensions` (`battery_storage`).
     No value moved.
  4. A registered constraint reaches a dispatch problem only when the caller names it:
     `dispatch.contract.dispatch_constraints_for(..., extension_constraints=[ids])`. The default is empty, so no
     run gains a constraint by a package being installed. It can add a descriptor and can never replace or
     remove a physical one.
  5. Registries hold code and schemas. No registry holds an assumption value.
- **Reason.** WP-SITE-01 (a `data_centre` archetype), WP-UW-01 (`underwriting_basis_v1`), WP-SIZE-02 (the
  constraint library) and the compute-envelope resources all need to add to the engine from their own
  packages. A closed dict or `Literal` in CORE makes each of them a CORE edit.
- **Alternatives considered.** Import-time side effects from a list of module names kept in CORE (rejected:
  the list is a CORE edit per plug-in). Last registration wins (rejected: a second package could silently
  replace the archetype or basis a hash was minted under). Skip a failing plug-in with a warning (rejected:
  the run would then price without a constraint its caller named).
- **Consequences.** Acceptance test: `tests/test_chassis_extension_registries.py`, with the dummy package
  `tests/plugins/dummy_chassis_plugin`; `test_registering_touches_no_core_owned_path` fails if a registry is
  closed again. **Left open, stated:** no production caller passes `extension_constraints` yet, and the MILP
  does not enforce a descriptor it does not know (WP-SIZE-02; `optimisation/milp_solver.py` is a one-agent
  file and was not touched). The `resources` registry is declarative: no engine reads it yet (compute
  envelopes, WP-SIZE-07). An archetype is a 48-value weekday and weekend shape, as before; a richer
  synthesiser hook is WP-SITE-01's to add.

### D72 — The free-text connection field stays as commentary; connection terms move to a typed IC-1 reference, with IC-4 evidence links

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-CORE-03). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  1. `ConnectionDetails.agreement: ConnectionAgreementRef | None` is the typed carrier of connection terms: the
     IC-1 agreement, by content hash. `QuoteRecord.connection_agreement` carries the same reference
     (`quotes/assembly.py`, `assemble_quote_record`), and `OptimisationResult.connection_agreement` is there for
     an engine that solves against an agreement.
  2. `anm_or_curtailment_notes` is **kept**, as commentary. It is displayed and quoted verbatim and no consumer
     may read a term out of it. `tests/contracts/test_ic12_chassis.py::test_no_new_module_reads_the_free_text_connection_field`
     pins the modules that name it.
  3. `SiteModel`, `OptimisationResult` and `QuoteRecord` gain `evidence_refs: tuple[EvidenceLink, ...] | None`.
     An `EvidenceLink` is `supports` (what the object evidences) plus an IC-4 `EvidenceRef`. Links are ordered
     by `(supports, hash)`, a repeated link is refused, an empty list is `None`.
  4. Each new field is **omitted from every dump while it is `None`** (a wrap serializer on each model). Every
     hash site reads a dump, so a record that sets none of them hashes to the bytes it had before. A set
     reference enters the dump and therefore `input_hash`.
- **Reason.** The work package says "in place of the free-text field". Deleting the field was measured against
  the repo first: it is set in nine committed real site files, it carries load-bearing disclosures there, and it
  is in every site's `input_hash`. Deleting it would move every `quote_id` in the repo and re-pin every golden,
  which acceptance item 1 forbids, and would refuse every stored site file under `extra="forbid"`. What the
  package needs is that the *contract* for connection terms is typed. That is done by moving the contract, not
  the bytes.
- **Alternatives considered.** Delete the field and migrate site files (rejected, above). Add the fields with
  an ordinary `None` default (rejected: `"agreement": null` in the dump moves every hash). Extend the D42
  pop-if-`None` list in assembly only (rejected: finance `case_id`, the schedule reference and persistence
  each read their own dump, so the hash would hold in one place and move in three).
- **Consequences.** No golden re-pinned. `tests/contracts/test_ic12_chassis.py` pins 24 site hashes and three
  fixture hashes minted from the models before any field was added. **Left open, stated:** no engine sets the
  two `OptimisationResult` fields yet, and no site file names an agreement yet (WP-CONN-06 registers them). The
  reference is not dereferenced at assembly: `schemas.references.resolve_connection_agreement` does it where
  the agreement is in hand.

### D71 — The chassis is IC-12; its 1.0.0 is rendered retroactively and 2.0.0 is release `gridq_chassis_v2`

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-CORE-03). Executed for Olly.
  It is not the owner's own words.
- **Decision.**
  1. IC-12 (roadmap NEW-IC-12) is `SiteModel`, `OptimisationResult` and `QuoteRecord` together, named in
     `gridq/schemas/chassis.py`. The models stay in `gridq.schemas`; the contract package's checker
     (`python -m gridq.contracts --check`) renders and checks their schemas like any other contract.
  2. **1.0.0** is the three models as they stood at the `gridq_clean_baseline` tag. Its schemas were rendered
     from that code, committed, and are archived by SHA-256. Tag `contracts/ic12-chassis/v1.0.0`.
  3. **2.0.0** adds the typed references of D72. Release name `gridq_chassis_v2`, tag
     `contracts/ic12-chassis/v2.0.0`.
  4. It is a **major** version although every added member is optional and every 1.0.0 instance is a valid
     2.0.0 instance with the same bytes: the meaning of an existing field changed (the free text stopped being
     where connection terms live), and a 1.0.0 reader refuses a 2.0.0 instance that sets a new member.
  5. `SCHEMA_VERSION` stays `"0.1.0"`. It is the instance stamp and enters every `run_id`; the contract version
     is a separate number and lives in `chassis.CONTRACT_VERSION`.
- **Reason.** The chassis was called frozen (`CLAUDE.md` §7) with no committed schema, so nothing failed when
  it drifted. A 2.0.0 with no 1.0.0 on record would have nothing to be compared against.
- **Alternatives considered.** Move the three models into `gridq.contracts` (rejected: they are not
  `ContractModel`s, they are mutable by measured necessity, and the contract modules must import nothing from
  the rest of `gridq`). Call it 1.1.0 (rejected, point 4). Bump `SCHEMA_VERSION` (rejected: it re-mints every
  `run_id`).
- **Consequences.** A change to any of the three models now fails `--check` until it is versioned.
  `tests/contracts/test_ic12_chassis.py` is the conformance suite. `OptimisationRequest` and `FinancialCase`
  are not in IC-12; they are reached only where `QuoteRecord` embeds them.

### D70 — D57 is scoped to the Grundon/Harwell academic study; `underwriting_basis_v1` is the underwriting basis (ADR-D3, option b)

- **Date:** 2026-11-26 (records ADR-D3, decided 2026-11-25)
- **Author:** Recorded by the WP-GRC-14 session. The decision is Fable 5.2's, made under the owner's
  delegation DEL-2026-11-25-1. It is not the owner's own words. Source: `atlas/roadmap/adr/ADR-D3.md`.
- **Decision.** ADR-D3 chose option (b). D57 stays, as the labelled basis `all_streams_enabled_study_v1`, for
  the Grundon/Harwell academic study, and every figure on it says so. `underwriting_basis_v1` is adopted as the
  underwriting basis (spec §7.6): the engine enforces gates, stacking exclusivity, size minimums and consent
  evidence (IC-4 evidence objects) on it.
- **Reason.** See the ADR. The review trigger there says nothing restores D57 as the operating basis.
- **Alternatives considered.** Option (a), keep D57 as the single basis (ADR-D3).
- **Consequences.** `CLAUDE.md` §2.0 now points here and at the ADR. **This entry changes no engine code.**
  `underwriting_basis_v1` is not yet implemented in this repo (WP-SIZE-03, WP-UW-01 unblock on it), so until it
  is, D57 is still what the code does. D57's text is unchanged and still governs the study basis.

### D69 — Operations boundary: Neura optimises, partners execute; an L4 compute turn-down runs in shadow only (ADR-D2, option d)

- **Date:** 2026-11-26 (records ADR-D2, decided 2026-11-25)
- **Author:** Recorded by the WP-GRC-14 session. The decision is Fable 5.2's, made under the owner's
  delegation DEL-2026-11-25-1. It is not the owner's own words. Source: `atlas/roadmap/adr/ADR-D2.md`.
- **Decision.** ADR-D2 chose option (d). Neura owns live optimisation and the dispatch envelope; the
  route-to-market partner owns market execution; the OEM owns certified control and protection; the edge holds
  the safety-critical constraints autonomously. Control authority is staged (L0 read-only, L1 shadow, L2 BESS
  closed loop, L3 chiller narrow band at one site), each step behind its own gate. A stage L4, workload
  (compute) turn-down, is advisory and shadow only, at a host that declares it, with closed-loop workload
  control left to its own ADR. Closed loop at any site also waits on XG-11 and XG-17.
- **Reason.** See the ADR. It reverses the "no autonomous dispatch/operations" boundary of
  `00_project_boundary.md` §2 and the "partner operates" reading of `north_star.md`.
- **Alternatives considered.** (a) keep the boundary; (b) the partner operates against GridQ's envelope;
  (c) Neura dispatches live (ADR-D2).
- **Consequences.** `docs/00_project_boundary.md` §2 and `docs/north_star.md` "Who operates" now point at the
  ADR. **No engine code changes.** The operating system is still not built in this repo. The ADR's review
  trigger: reverse to (b) if no load-control licence or exemption is in prospect by 31 Mar 2028, no host will
  sign a write path by 30 Jun 2028, dispatch-liability insurance is unobtainable, or the ADR-S14-1 capacity
  trigger fires twice with the cut ladder exhausted.

### D68 — The local-flex availability leg is priced GROSS and labelled so; a performance ratio applies only from a CONFIRMED, sourced entry

- **Date:** 2026-11-26
- **Author:** Fable 5.2, under the owner's delegation of design decisions (WP-UW-16, 2026-11-23). Executed for
  Olly. Closes WP-UW-16 acceptance item 4 by its second limb (findings D-116).
- **Context.** The ENA Monthly Utilisation Performance Factor claws back the availability payment when the asset
  under-delivers instructed utilisation. `gb_streams.local_flex_availability_value` applies it only when handed a
  `utilisation_performance_ratio`, and no caller ever passed one, so on every D54 site the leg was an upper bound
  with nothing in the quote saying so. No assumption release carries a sourced ratio.
- **Decision.**
  1. The availability leg is priced **gross** and **labelled** in `quote.warnings`
     (`gb_streams.LOCAL_FLEX_GROSS_LABEL`, appended by `quotes/pipeline.py:487` when
     `gb_streams.local_flex_gross_label` says so; exactly once, and only on a site holding an award).
  2. One accessor decides both the price and the label: `gb_streams.sourced_utilisation_performance_ratio`. It
     feeds the per-award call at `optimisation/valuation.py:799` and `local_flex_gross_label`, so the label cannot
     claim gross on a tapered leg or the reverse. The two `contracted_mw=None` call sites
     (`valuation.py:776`, `gb_streams.py:3554`) price nothing and are unchanged.
  3. A ratio counts only when its entry is `CONFIRMED` **and** has a non-empty source. A placeholder,
     needs-review or unsourced entry reads as `None` and the leg stays gross and labelled: an unsourced ratio is
     the fabricated number D-116 forbids, and it would silently retire the label.
  4. The DFS fleet delivery factor (K ≈ 0.765, a fleet-level settled/accepted ratio for a different product) is
     **not** substituted for the local-flex ratio.
- **Tests.** `tests/test_local_flex_gross_label.py`, T13 to T17. T13: an awarded quote carries the label once on a
  priced leg. T14: no award, no label. T15: an injected 0.9 ratio tapers the leg by exactly
  `payable_delivery_fraction(0.9)` = 0.80 and retires the label. T16: every `gb_v*` release returns `None` or a
  confirmed, sourced ratio. T17: placeholder, needs-review and blank-source entries are ignored; a confirmed,
  sourced entry is applied (positive control). Mutations, each red: M-T13 (label not appended), M-T14 (award
  guard removed), M-T15 (ratio not passed), M-T16 (accessor returns 0.9 unconditionally), M-T17a (status check
  deleted), M-T17b (source check deleted).
- **Direction of effect.** No £ moves: the ratio is `None` on every release, exactly as before. Every awarded
  quote gains one warning.
- **What stays open.** The D-116 MUST (apply the factor) stays open until a sourced ratio exists. Data ask: the
  DNO's settlement statements for the award, or a published delivery ratio for the product. Tracked under
  WP-UW-11.
- **Alternatives considered.** Substitute the DFS fleet factor (rejected: a different product and a fleet, not
  this asset); apply any library entry whatever its status (rejected: a placeholder would retire the label).

### D67 — The customer bill publishes the LOWEST peak among value-optimal schedules, by a second LP pass on the bill-producing solve only

- **Date:** 2026-11-25. Owner notice 2026-11-23, answer verbatim: "OK to re-mint".
- **Author:** Olly (design by Fable 5.2, `docs/shared_work/wp_uw16_d130_peak_decision.md` §2). The T-P2 test design
  and the limits wording below are Fable 5.2's decision under the owner's delegation (2026-11-25).
- **Context.** The bill's "peak import after" was whichever of several equally valuable schedules the solver
  returned: 278.4 kW on macOS and 260.2 kW on Linux at the same £ on the demo. The `_EPS_PEAK` tie-break (1e-6)
  is shared across a 2,540-half-hour plateau, about 4e-10 per half-hour, below HiGHS's 1e-7 dual tolerance.
- **Decision.** `DispatchProblem.minimise_published_peak` (default `False`) is set only on the solve whose schedule
  becomes the customer bill: `optimisation/engine.py:1357` and `app/demo.py:443` (`_dispatch_energy_for`). The
  solver then re-solves once, minimising `peak_import` with the first pass's objective (peak term removed) held
  within `_PEAK_PASS_ECON_SLACK_GBP = 1e-6`, and adopts the second schedule only if the peak falls by more than
  `_PEAK_PASS_MIN_GAIN_KW = 1e-3`; otherwise the first schedule is kept bit for bit. It runs only on a non-binary,
  zero-weight LP with no capacity price (`milp_solver.py`, the block after the gap check). Sizing solves never
  set the flag, so every candidate is byte-identical to before.
- **Pin.** `bill.peak_import_after_kw` 278.407 → **235.841 kW** (DOWN 42.566 kW), recomputed by four solver routes
  (second pass 235.84122 to 235.84125; dual tolerance 1e-10, `_EPS_PEAK` 1e-4/1e-3 and primal simplex each
  235.841364). No other pin moves. Before the battery the demo peaks at 248.45 kW, so the battery now lowers the
  published peak by 12.6 kW where the old figure said it raised it by 30 kW. The pin is provisional until the
  Linux golden job reports 235.841.
- **Fable's decision on T-P2 (the certificate).** The planned assertion, "capping the peak below the pin lowers the
  bill saving", was falsified by measurement: capping the peak 0.05 kW lower raises the saving by 1p, and 1 kW
  lower by 4p, while the dispatch objective falls. T-P2
  (`test_demo_published_peak_is_the_minimum_at_the_optimal_value`) instead brackets the pin on the VALUE
  objective, with literal thresholds: flag on, the peak is 235.841 ± 0.005, no higher than the flag-off peak, and
  the saving and every stream equal the flag-off solve's to the penny; capped 0.005 kW above the pin, the value
  objective is within 1e-5 of optimal (measured 7.7e-8); capped 0.05 kW below, it is infeasible or at least 5e-4
  lower (measured 1.90e-3).
- **Limits, stated.**
  1. The published peak is the lowest peak among schedules optimal for the dispatch objective (bill saving plus
     every priced stream, less wear). It is not the lowest peak at the highest bill saving. On the demo, capping
     the peak 1 kW lower raises the bill saving by 4p and lowers DFS revenue by 7p, a net loss of 3.8p
     (measured). No published £ moves.
  2. The second pass does not run on a binary solve (MPC, or the export-wash exclusivity binary). The plan assumed
     `_dispatch_energy_for` always re-dispatches on the plain LP; measured on the D57 study-basis engine
     (2026-11-26, `linopy.Model.solve` instrumented at each site's selected spec), that is false on 7 of the 9
     real sites: Bishops Cleeve ash plant and MRF, Ewelme, Ewelme invoice-anchored, Knowl Hill, Knowl Hill
     invoice-anchored and Tanhouse solve their bill as a MILP (export-wash binary), so their published peak is
     still the first solve's and is NOT certified minimal. On Colnbrook and Harwell the bill solve is an LP, the
     second pass ran, and it found no lower peak at the same value (915.077 and 213.81 kW, unchanged). The demo's
     bill solve is an LP (T-P2). OPEN (WP-CORE-01): carry the pass to the MILP bill, for example by fixing the
     binaries at their solved values and re-solving the peak pass as an LP.
  3. Sizing candidates are not re-solved, so the agreed-capacity overlay's note "FLOOR dispatch leaves X kW"
     (`milp_solver.py`) quotes the selected candidate's first-pass peak and can differ from the bill's peak. It
     prices nothing (the overlay's £ comes from its own solve, where the peak carries a real price). Reported, not
     fixed here.
- **Tests and mutations.** T-P1 (the re-pinned value; M-P1, demo flag `False`), T-P2 (M-P2,
  `_PEAK_PASS_MIN_GAIN_KW` 1e9; M-P3, `_PEAK_PASS_ECON_SLACK_GBP` 1.0), T-P3
  `test_peak_pass_leaves_a_measured_zero_untouched` (M-P4, always adopt), T-P4
  `test_only_the_bill_producing_solve_minimises_the_published_peak` (M-P5, engine flag `False`).
- **Direction of effect.** "Peak import after" falls or stays on every quote; no £, IRR or DSCR moves. Demo: DOWN
  42.566 kW. Real sites (check 5, D66 table's last column): on all nine the bill-producing solve gives the same
  peak, saving, charge and discharge with the flag off and on, so no real-site figure moves; on seven of them
  that is because the pass does not run (limit 2).
- **Alternatives considered.** Tighten the dual tolerance to 1e-10 (only about 4× clear of the plateau pressure);
  raise `_EPS_PEAK` (turns the tie-break into a price); two passes on every solve (about 2.4× solver time, and it
  leaked dispatch into four measured zeros); widen or drop the pin.

### D66 — An award in term counts in neither P90 nor the lender case, in any year (D-130 lands; the August rule stands)

- **Date:** 2026-11-23 (owner answer); implemented and measured 2026-11-25.
- **Author:** Olly. Answer to the corrected question (`docs/shared_work/wp_uw16_d130_peak_decision.md` §1.4), which
  told the owner that D65's option text was wrong about P90 and asked how the cautious (P90) and lender cases
  should treat a one-year award. Options: A, count none of it; B, count the contracted year only; C, leave it as
  today. Verbatim answer: "A. Count none (Recommended)".
- **Context.** Finding D-130: the award's `tenor_years` never reached finance on the production path, so P90 and
  the lender case counted 85% of a one-year award's income in all 15 years.
- **Decision.** `run_financial_model` reads `site.local_flex_awards` ahead of any caller-supplied commitments
  (`finance/financial_model.py:168`), so the tenor reaches finance on every path, `generate_quote` included. With
  the tenor known, the existing rule applies: `_NO_P90_IN_TERM` (D-139: a DNO flexibility contract can be ended on
  90 days' notice, and the award is an attestation, not a contract seen) zeroes LOCAL_FLEX_PICLO's P90 and lender
  lines in term, and `_NO_P90_AFTER_EXPIRY` removes them after expiry. P50 is unchanged (D65). The
  `_NO_P90_IN_TERM` reason text now names the availability leg, the 90 days' notice and the gross pricing (D68).
- **Tests.** `tests/test_finance_tenor_threading.py`: T10
  `test_site_award_tenor_reaches_finance_from_generate_quote`, T11
  `test_sizing_finance_runner_sees_the_award_tenor`, T12
  `test_one_year_award_leaves_the_cautious_and_lender_cases_in_every_year`. Deleting the line at
  `financial_model.py:168` turns T10 and T11 red (M-T10); M-T12a and M-T12b (the in-term and after-expiry
  exclusions disabled) turn T12 red.
- **Measured on all nine real sites** (D57 campaign engine; `generate_quote` at each site's selected spec, then the
  same finance call re-run with `local_flex_awards` stripped as the "before" arm; 2026-11-25, on a frozen
  snapshot of this change set before D68, which moves no £).
  Every site's P50 series is identical to the penny in every year, so the IRR and payback cannot move; every
  site's P90 and lender lines fall in every one of the 15 years; selection is unchanged (it ranks on P50); no
  quote carries the "prices no CONTRACTED revenue" warning, before or after. The last column is D67's check 5.

| Site | Spec kW / kWh | Local flex y1 £ | P50 EBITDA 15-yr £ (unchanged) | P90 EBITDA y1 £ | P90 EBITDA 15-yr £ | Lender y1 £ | Lender 15-yr £ | Peak after, kW (flag off / on) |
|---|---|---|---|---|---|---|---|---|
| bishops_cleeve_ash_plant | 4.2 / 16.8 | 4.76 | 9,943.82 | 658.44 → 654.59 | 7,936.20 → 7,887.62 | 241.57 → 237.72 | 2,494.33 → 2,445.73 | 46.304 / 46.304 |
| bishops_cleeve_mrf | 7.374 / 29.496 | 8.36 | 18,213.90 | 1,201.09 → 1,194.34 | 14,614.66 → 14,529.34 | 500.16 → 493.40 | 5,204.45 → 5,119.11 | 147.48 / 147.48 |
| colnbrook | 45.754 / 30.1976 | 846.33 | 36,635.94 | 2,386.81 → 1,703.40 | 28,126.86 → 19,489.11 | 2,332.99 → 1,649.58 | 24,028.26 → 15,390.50 | 915.077 / 915.077 |
| ewelme | 9.5 / 12.16 | 178.46 | 12,167.59 | 810.69 → 666.58 | 9,572.36 → 7,750.98 | 526.05 → 381.94 | 5,715.84 → 3,894.45 | 69.28 / 69.28 |
| ewelme_invoice_anchored | 20.24 / 18.8232 | 380.20 | 28,046.96 | 1,852.93 → 1,545.92 | 22,299.47 → 18,419.11 | 1,371.48 → 1,064.47 | 14,701.90 → 10,821.55 | 404.839 / 404.839 |
| harwell | 11.23 / 7.9733 | 210.81 | 14,160.79 | 934.76 → 764.53 | 11,190.74 → 9,039.19 | 738.00 → 567.77 | 7,905.22 → 5,753.67 | 213.81 / 213.81 |
| knowl_hill | 17.06 / 11.4302 | 316.05 | 13,453.17 | 886.59 → 631.38 | 10,069.27 → 6,843.61 | 789.56 → 534.35 | 7,994.15 → 4,768.50 | 227.2 / 227.2 |
| knowl_hill_invoice_anchored | 4.8 / 3.072 | 88.90 | 4,023.72 | 262.84 → 191.05 | 3,022.20 → 2,114.85 | 232.92 → 161.14 | 2,285.44 → 1,378.09 | 95.992 / 95.992 |
| tanhouse | 24.318 / 12.159 | 450.00 | 21,184.75 | 1,384.83 → 1,021.45 | 16,134.27 → 11,541.48 | 1,358.54 → 995.17 | 13,985.22 → 9,392.45 | 486.35 / 486.35 |

  The P90 year-1 fall is 0.8075 × the local-flex year-1 £ on every site, to rounding (Tanhouse: 450.00 × 0.8075
  = 363.38).
  The Tanhouse figures put to the owner (P90 y1 1,391.65 → 1,028.23) were measured on an earlier tree; the fall
  is the same to 4p.
- **Direction of effect.** DOWN on P90 and the lender case on every awarded site; P50, IRR and payback unchanged.
- **Alternatives considered.** B, count year 1 only (reverses the August rule for local flex); C, leave it (D-130
  stays open and the quote notes stay wrong).

### D65 — A one-year local-flex award keeps a faded re-contracting expectation in P50 after it expires

- **Date:** 2026-11-23
- **Author:** Olly. Answer to WP-UW-16 plan §11 Q1, as put: "a local-flex award has a one-year contract. After
  year 1, should the P50 (central) case still count expected re-contracting revenue, fading over time? P90 and the
  lender case already stop at contract end." Verbatim answer: "Keep faded renewal (Recommended)".
- **Decision.** Once the award's `tenor_years` reaches finance (D-130), P50 is unchanged: after expiry it carries
  the award's revenue as merchant-class re-contracting, faded and labelled. Population 3 in
  `finance/cashflows.py` is unchanged by design.
- **Correction.** The option text also said "P90 and the lender case count only the contracted year". That was
  inaccurate, and it was corrected and re-decided in D66.
- **Consequences.** P50, the project IRR and the payback do not move under D-130 (measured on all nine real
  sites, D66).

### D64 — D-numbers for three changes recorded only in the changelog: FIX 3, FIX 5 and the `12033d0` report reword

- **Date:** 2026-11-22 (records changes landed 2026-08-17 to 2026-08-19)
- **Author:** Recorded by the implementing session for WP-CORE-01 (triage §5 item 4). It documents changes that
  already landed. It makes no new choice, and the owner may amend it.
- **Decision.** (a) **FIX 3** (`f3aedd8`, resting on D49 (g)): the GBP 1,557 DNO assessment-and-design one-off is
  part of capex in both capex shapes. (b) **FIX 5** (`05133e4`): a consented CM rate is read as quoted on de-rated
  capacity at the 2 h reference bin. It is divided back out and de-rated once on the volume. (c) **`12033d0`**: a
  null `financial_case` renders as "No system was selected." with the engine's reason. It no longer reads as an
  economic verdict.
- **Reason.** Each one moves a priced or rendered figure. The triage found that none had a D-number, so the
  re-pins in triage #1–5, #9, #17 and #27 rested on changelog prose alone.
- **Consequences.** The triage re-pins cite D64 alongside the commits.

### D63 — The lock moves to highspy 1.15.1, the version the golden pins were minted and tested on

- **Date:** 2026-11-22
- **Author:** Olly. Answer to the WP-CORE-01 question "Bump lock to 1.15.1 or re-mint pins on 1.14.0":
  "Bump lock to 1.15.1".
- **Decision.** The dependency lock pins highspy 1.15.1, the same version as the venv and CI. The golden pins stay
  as minted.
- **Alternatives considered.** Re-mint the golden pins on 1.14.0.
- **Consequences.** A fresh install from the lock reproduces the pins. A solver bump is now a deliberate act that
  re-runs the golden tier.

### D62 — Tests whose premise DFS broke are restated as laws; the zero-dispatch note keys on NON-DFS discharge

- **Date:** 2026-11-22
- **Author:** Olly. Answer to the WP-CORE-01 triage §5 items 2–3: "Restate as laws".
- **Decision.**
  1. **#20–#22.** `valuation._zero_dispatch_note` (the "declined a loss-making trade" explanation) fires when
     discharge OUTSIDE DFS-called periods is zero, and it states the DFS delivery separately. With DFS in every
     objective (D57), total discharge is never zero on a site with called periods.
  2. **#24, #25.** The claims are restated as laws that hold with DFS competing. #24: the levy raises the
     in-window share against the no-levy arm. #25: Δyear-1 = credit × availability + Δfloor, with Δfloor ≤ 0 and
     Δyear-1 > 0. No judgement threshold is re-chosen to make a red test green.
  3. **#12.** The peak-tiebreak pin moves to a fixture with no DFS-called periods, which restores the premise it
     tests.
- **Rule.** Each restated test is mutation-checked: the behaviour it guards is reverted in memory and the test
  must go red.
- **Alternatives considered.** Redesign every fixture without DFS; leave the six tests red.

### D61 — The local-flex dispatch reservation is PER-CANDIDATE: the invoice MW equals the reserved MW at every size

- **Date:** 2026-11-22
- **Author:** Olly. Answer to WP-UW-16 / WP-CORE-01 triage §5 item 1: "Per-candidate reservation".
- **Context.** `339892c` (D60 era) made the availability invoice bill the SOLVED candidate's MW, as the owner
  directed ("its not right that every size would get the same amount for all battery size"). But the pre-solve
  dispatch reservation (`gb_streams.local_flex_award_constraints` → `clamped_local_flex_awards(site,
  assumptions)`) is still clamped at the search FLOOR. Triage #7: invoice 46.32 kW against 2.247 kW reserved.
  Triage #8: 213.81 kW against 10.37 kW. The rest of the battery is free to sell elsewhere, so the same MW is
  booked twice.
- **Decision.** Each sizing candidate's dispatch reserves that candidate's own awarded MW in the award windows.
  This is the same clamped volume the invoice bills, so the FIX 1 (`e005d08`) invariant, "invoice MW = reserved
  MW in every period", holds again at every size. Tests #7 and #8 pass as written.
- **Alternatives considered.**
  - (ii) Bill the floor-bound reservation. This is size-invariant, which the owner rejected.
  - (iii) Retire the invariant and state the double booking in the pack.
- **Consequences.** Every site with a local-flex award moves: its priced numbers, its sizing selection and its
  golden pins. Each moved pin is re-minted with the hand recomputation recorded (WP-UW-16 acceptance). The same
  package also closes D-130 (an award's `tenor_years` reaches finance from `generate_quote`; D66) and handles
  D-116 by labelling (D68): the availability leg is priced gross and labelled so in `quote.warnings`. The D-116
  MUST stays open until a sourced `utilisation_performance_ratio` exists.

### D60 — DFS is scaled to the EVIDENCE: the observed period count, the site's OWN zone, and this battery's own deliverable volume

- **Date:** 2026-08-19
- **Author:** Olly (owner-directed). Verbatim: "ok scale the DFS then to the evidence, scaling based on
  battery size and location to get a more accurate view."
- **Status:** Recorded by the implementing session because it moves the LARGEST single stream in the
  published campaign DOWN, and a figure that falls needs its reasoning on the record at least as much
  as one that rises. **It adds no gate and reverses nothing in D57.** Every eligibility condition DFS
  has in the real world stays ASSUMED APPROVED: the 0.1 MW Registered Unit Capacity minimum, DFS
  registration, zone membership, the baseline methodology, and stacking exclusivity with the BM and the
  response family. What changed is arithmetic, in three dimensions.
- **Decision.**
  1. **THE OBSERVED PERIOD COUNT, NOT AN ANNUALISATION OF IT.** The mask booked zone 6's 407 accepted
     settlement periods multiplied by 365/127 to 1,170 called half-hours a year, so 65% of the modelled
     calls were an extrapolation of a spring-and-summer observation across autumn and winter. It now
     books the count that was OBSERVED, per zone, unextrapolated. DIRECTION OF EFFECT: **DOWN**, 3.16x
     on the campaign's own zone 10 (370 periods against 1,170). It is a **MEASURED FLOOR** and is
     published as one: if the event rate persists year-round the figure understates a full year by up
     to 2.9x, and the extrapolation overstated it if the observed window was seasonally intense. The
     evidence does not settle which, and a full delivery year of the 2026/27 service does not exist -
     the redesign is four months old.
  2. **THE SITE'S OWN ZONE.** DFS is zonal, and price and call count move in OPPOSITE directions across
     the twelve zones: the thin zones clear GBP 195-222/MWh on almost no volume, the deep zones GBP
     157-164/MWh on nearly all of it. Every site was previously priced in zone 6, the most favourable
     of the twelve. Each zone is now resolved by point-in-polygon of the site's postcode-district
     centroid against NESO's published 12-zone GeoJSON, whose zip sha256 REPRODUCES the value the
     library already recorded. The campaign spans zone 10 (GL52, OX10, OX11, RG10) and zone 12 (SL3),
     and the twelve zones span GBP 8.60 to GBP 32.50/kW/yr at full delivery, median GBP 24.08.
  3. **THIS BATTERY'S OWN DELIVERABLE VOLUME.** DFS settles on the OFFERED Service Volume, so the money
     is bounded twice: by rated POWER within a half-hour and by usable ENERGY across an evening's
     called half-hours. The engine path already measured delivery from the dispatch and therefore
     already scaled; the PROXY path that ranks the coarse sweep did not. It paid a flat per-kW rate on
     the Registered Unit Capacity TRUNCATED to one decimal place, so a 0.25 MW unit was paid on 0.2 MW
     (a 20% haircut delivered by a registration formality, which D57 limb 3 expressly forbids) and a
     0.5 h battery was ranked as though it could serve a five-half-hour window at full power. It now
     prices the unit's own MW over `min(periods per event day x 0.5 h x rated kW, usable kWh) x event
     days`, every term measured.
- **Reason.** DFS contributed about GBP 60.85/kW/yr at Bishops Cleeve Ash Plant and the eight-site
  campaign totalled GBP 113-205/kW/yr, against Modo's GBP 60-80/kW/yr (GBP 110 long-term) index for a
  FULLY revenue-stacked grid-scale megawatt battery. An independent magnitude audit verified the RATE
  (GBP 159.711/MWh, zone 6, volume-weighted accepted, re-derived from the raw file) and the
  407-settlement-period count, and left the ANNUALISATION as "plausible with a stated extrapolation".
  The owner then asked for the evidence-scaled version. Two things came out of the re-derivation that
  were not in the brief: the window shape driving the mask was a count of Live **BID ROWS including
  REJECTED ones** rather than of procured settlement periods, and the `Event Type == Downwards` filter
  the engine's own measurement implies had not been applied (it moves zone 3 from 173 periods to 163).
- **Alternatives considered.** (a) **Keep the annualisation and label it harder** - refused: the owner
  asked for the observed figure, and a 2.87x extrapolation of a seasonal window is a modelling choice
  masquerading as a measurement. (b) **Read the utilisation CSV at solve time** instead of transcribing
  the table - refused: NESO grows that resource daily and `input_hash` pins no data content (F-06), so
  a re-fetch would silently move every DFS pound under an unchanged `run_id`. The table is transcribed,
  generated by `scripts/derive_dfs_zonal_basis.py`, and pinned to the source (at its recorded sha256)
  by test. (c) **Take the favourable zone under D56** - refused: D56 governs a RANGE of estimates of
  one quantity, and a location is a FACT about the site, not a range to choose from. Where the two
  conflict the site's own zone wins, and the note says so. (d) **Substitute nothing for an unresolved
  postcode** - refused as a D57 limb 5b violation: the best-evidenced zone is substituted, USED IN
  FULL, and declared. (e) **Apply the fleet's realised K = 0.3896** - refused, and it was never in
  scope: that statistic corrects OFFERED volume against SETTLED volume, and a dispatch-measured
  delivery has no such mismatch in it.
- **Consequences.** DFS falls on every site and stays NON-ZERO on every site. Tanhouse gains a
  postcode (`SL3`, a labelled `derived_input` from the repo's own evidence that it shares one private
  wire with Colnbrook), so all nine site records now resolve their own zone and none takes the
  substitution. `gb_v1_12` gains `ancillary_services.dfs_zonal_observed_2026_27`, the citable copy of
  the 12-zone table, so the D59 per-stream register no longer prints a traceability gap for the largest
  stream in the pack. Two contradictions in the packs are closed: the gate note that told every reader
  the record showed GBP 0 "only because no engine branch prices this stream yet" (false since D57 item
  4, and it sat three notes from a measured figure), and the declared option-set price of GBP
  93.41/kW/yr in the campaign runner, which is now the site's own zone's measured figure. Every
  `input_hash` moves, because the library, the zone and one site record are all real inputs.

### D59 — REPORT ORDER: the value story leads, the methodology follows, the assumptions register comes third, and no rendered deliverable carries an em dash

- **Date:** 2026-08-19
- **Author:** Olly (owner-directed, authoritative).
- **Decision, verbatim:** "For each of the sites from Grundon and Harwell you are to fully reproduce the
  reports - starting with the IRR and Revenue breakdown and graphs, then the methodology and then the
  assumptions register for each site - no em-dashes allowed at all in the reports." Read together with
  the standing earlier instruction: "ensure that this is clearly shows the full sizing surface in the
  report, and ensure that it is very clear all assumptions made and ensure that somewhere in the report
  it clearly shows the breakdown of revenues per stream and the assumptions made for each of the streams
  should be shown and it should also give graphs that show exactly how the battery has been dispatched
  on a given day within the report to show how the dispatch works and also graphs that clearly highlight
  how the MILP selection was done."
- **What it changes.** DOCUMENT STRUCTURE and what a reader is shown, in what order. No pricing or
  eligibility logic moves; D57 and D58 are untouched.
  1. **All four artifacts run the same movement:** the project IRR first, then revenue per stream, then
     the graphs; then the methodology; then the assumptions register; then the evidence sections.
  2. **The evidence sections move DOWN, not out.** Situation, Complication, Evidence and provenance and
     What would change this are all still rendered, in full, with every exhibit they carried before. The
     consulting pack runs eight sections instead of seven and nothing was deleted to make room.
  3. **Per-stream assumptions are now a register, not a scattering.** For each stream: its figure, the
     assumption-library keys THAT stream's own dispatch cited with the value and source of each, the
     approvals assumed for it under D57 quoted verbatim, and everything else the engine wrote onto it.
     A stream that cites no key states the traceability gap rather than printing a blank, because a blank
     reads as "no assumption was made", which is false.
  4. **The sizing surface is published FULL.** Every evaluated candidate as a row, with its return, its
     own priced-floor revenue, its stage and whether it was a proxy score, and the one row the record
     marks selected saying so. A new exhibit ranks the CONFIRMED candidates on the recorded objective
     and reproduces `selection_basis` and `selection_reason` verbatim, so "why this size" is answered by
     an ordering rather than asserted.
  5. **A methodology section exists.** It did not, in any of the four artifacts. Fixed mechanism copy
     (the half-hourly MILP, the two-stage search, the finance model) plus this run's own recorded
     identifiers, copied. It never asserts a solver, stage or objective the record does not name.
  6. **THE EM-DASH EXEMPTION IS WITHDRAWN.** The memo and the HTML report were deliberately exempt from
     `formatting.no_dashes` so that `quote.warnings` appeared byte-for-byte; the persisted campaign packs
     carried 100 and 101 em dashes as a result. All four documents are now scrubbed at the document seam.
- **Reason.** The pack opened with site and context exhibits and closed with the assumptions page, so a
  reader met the load duration curve before the return and the optimism before its label. The owner has
  asked for the value first three times and for no em dash three times.
- **Alternatives considered.** (a) Keep the memo and the HTML report exempt from the dash scrub to
  preserve byte-for-byte warnings — refused: `no_dashes` rewrites only the two dash characters, the
  RECORD is untouched, every warning still appears in full, and a typographic exemption does not outrank
  a standing owner instruction. The verbatim-warning test now compares the scrubbed form and says why.
  (b) Fold the methodology into the assumptions register — refused: they answer different questions, and
  the owner named them as two separate movements in a specific order. (c) Summarise the sizing surface as
  a triple with the map — refused: that is what the pack already did, and it is what the owner rejected.
- **Consequences.** Section anchors and heading text changed in the consulting pack, so anything linking
  to `#summary`, `#situation`, `#complication` or `#resolution` needs updating. The four artifacts grew
  (the campaign packs are now roughly 430 KB, 282 KB, 372 KB and 154 KB) because the surface table and
  the per-stream register are enumerations rather than summaries; that is the instruction, not an
  oversight. Order is pinned by `index()` assertions on the RENDERED BYTES of a record built in
  `tests/test_report_order_d59.py`, never loaded from the gitignored `runs/`.

### D58 — Under D57 the REGULATORY exclusivity is waived and the PHYSICS is not, and the CM auction's pound is booked once

- **Date:** 2026-08-18
- **Author:** Olly (owner-directed D57), recorded by the implementing session because it was
  challenged mid-drive and the reasoning must not have to be reconstructed.
- **Decision, two limbs.**
  1. **Regulatory exclusivity is ASSUMED WAIVED and STATED; one capability sold once is ENFORCED.**
     D57 limb 5b names "stacking rules would forbid earning two such streams on one meter point"
     among the conditions assumed satisfied, and the implementation brief directs that the DFS/BM and
     DFS/response waivers be "stated on the register, not enforced". So
     `value_stack.capacity_budget.allocate_capacity` pays every claim IN FULL and DISCLOSES the
     shortfall the constraint would have imposed, per stream, with its multiple. What is NOT waived is
     the physics, and it is enforced where physics belongs — in the dispatch: one discharge variable,
     one state of energy, one annual warranty throughput budget, every price component a DISJOINT
     array over the same half-hours, and DFS delivery paying the same £21.3/MWh marginal degradation
     cost as arbitrage. The optimiser therefore sells each half-hour ONCE, to whichever stream values
     it most, and the annuity layer above it is the owner's stated assumption with its size measured.
  2. **The Capacity Market auction's pound is booked ONCE.** The £63.00/kW/yr T-4 2026/27 clearing
     price is both what a CMU earns and the reason the supplier's CM pass-through stepped to
     0.30581 £/kWh, which the battery avoids by discharging through the CM peak window. D57's one
     preserved rule forbids a pound appearing "in both a cost line and a revenue line", so
     `finance.cashflows` nets `min(CAPACITY_MARKET revenue, windowed-levy credit)` off the REPORTED
     credit. Both streams stay enabled; the levy stays in the MILP objective, so no dispatch decision
     changes; only the reported credit moves.
- **Reason.** A data-sourcing pass measured the overlap at roughly £38/kW/yr on a real campaign site
  and argued that a per-half-hour option set should replace the annuity sum. The first half is
  arithmetic and is adopted. The second is eligibility: enforcing a regulatory option set zeroes the
  losing streams — the library's own note says DFS loses to BM by five to eight times and "the correct
  model outcome is that the optimiser declines it" — which is exactly what D57 limb 1 forbids and what
  produced null IRRs everywhere before D57.
- **Alternatives considered.** (a) Enforce the published exclusivity families as MILP constraints —
  refused: it re-imposes the eligibility D57 removes, and it would freeze the acceptance matrix at the
  streams that win. (b) Drop the CM revenue stream instead of netting the credit — refused: D57 limb 1
  requires every stream priced, and the revenue side is the one a reader reads as the Capacity Market's
  value. (c) Leave both CM legs at full — refused: it is not optimism, it is an accounting error, and
  it would corrupt the academic result on its own terms.
- **Consequences.** The stacking disclosure is now MEASURED rather than asserted
  (`CapacityBudget.constrained_awards`, `d57_uncut`), and it renders on the assumptions page with the
  multiple it is worth. The CM netting is the ONE figure in the D57 change set that moves DOWN, and it
  is reported as such: arithmetic integrity outranks "everything moves UP".

### D57 — ACADEMIC VALIDATION BASIS: every revenue stream is OPEN on every site, every contract, consent, gate and rule is ASSUMED APPROVED, and no real-world constraint may be imposed

- **Date:** 2026-08-18
- **Author:** Olly (owner-directed, authoritative). Verbatim: "For every single site you are to use every single revenue stream - whether indicated as contracted, conditional, gated or other, it no longer matters at all, you MUST assume that the site being underwritten has every contract, condition, and gate approved and that every single export consent, or rule has been approved for this site and battery size - you will NOT impose any restrictions on any of the revenue streams for the battery ... it should not add in any 'real world' constraints at all, that is forbidden. We need to massively simplify this down as an academic validation exercise."
- **Status:** **THIS DECISION SUPERSEDES EVERY OTHER INSTRUCTION IN THE CODEBASE ON STREAM ELIGIBILITY.** It is not negotiable by a later session and is not to be re-litigated. An agent that adds a gate, threshold, consent requirement, eligibility test, award requirement or "real world" constraint to a revenue stream is violating the owner's standing instruction, whatever local docstring appears to justify it.
- **Decision:**
  1. **Every revenue stream is OPEN and PRICED on every site.** PPA/bill savings, tariff arbitrage, DUoS red-band avoidance, MIC/ASC headroom, agreed-capacity reduction, Capacity Market, local flexibility, DFS, Balancing Mechanism, ancillary DC/DM/DR, wholesale/P415. No stream may be suppressed, zeroed, gated, held conditional, or excluded from the priced floor.
  2. **Every contract, consent, gate, award, registration, threshold and rule is ASSUMED APPROVED** for the site and for the battery size under evaluation. This includes export consent, export MPAN, G99/G100, route-to-market, credit assessment, CM prequalification, BM Unit registration, response-unit registration, DFS registration, DNO flexibility awards, aggregation, and every statutory or product minimum-size threshold.
  3. **No real-world constraint may be added.** Statutory thresholds (the 1 MW registered-unit minimum, the 0.1 MW DFS minimum), stacking-exclusivity rules, tender availability and consent status are all ASSUMED SATISFIED and must not reduce a figure.
  4. **The approvals are STATED, not hidden.** Each site's assumptions register enumerates every contract, consent, gate and rule assumed approved. This is the owner's own instruction ("You may state in the report what export consents and conditions, gates and contracts have been approved") and it is what makes limb 1 an academic result rather than an unexplained one.
  5. **Vehicle:** the named study basis `all_streams_enabled_study_v1` (`schemas/study.py`), which is explicit per run, enters `input_hash` so it can never share a `run_id` with any other basis, and renders its own label on every artifact.

**5b. AN AWARDED-CONTRACT PRICE IS USABLE. THIS IS THE LIMB MOST LIKELY TO BE MISREAD.**
Where a sourced price is an **awarded contract rate**, a participant-only rate, a tender-specific
clear, a registered-unit rate, or any figure that in the real world requires a contract, award,
registration, consent or membership the site does not hold: **it is used in full.** The award exists by
assumption under limb 2, so the award price applies. There is no price in this exercise that the model
is "not entitled to use".

None of the following may zero, reduce, defer or hold a stream conditional. Each is ASSUMED SATISFIED,
recorded on the register, and the stream is priced in full:
  * the rate is an awarded clear rather than a published tariff;
  * the site holds no award, contract, registration or consent;
  * the site is below a statutory or product minimum size (the 1 MW registered unit, the 0.1 MW DFS RUC);
  * no open tender exists in the site's zone, or the zone's volume is fully subscribed;
  * the price belongs to a different zone or licence area than the site sits in;
  * stacking rules would forbid earning two such streams on one meter point;
  * the supply contract is fixed-price, so an avoided cost would not be cash-real in-year.

What remains required, because it is correctness and not eligibility: every rate is **real and cited**,
and **labelled for what it is** (awarded clear / ceiling / requirement / average / forecast). That
label is metadata a reader needs; it is never a reason to withhold the figure. A fabricated or
misattributed citation remains the worst available outcome.
- **Reason:** This is an **ACADEMIC VALIDATION EXERCISE**. Its purpose is to establish what the asset earns when market access is not the binding constraint, so that the dispatch, sizing and finance layers can be validated against the literature. Successive agents each added a conservative restriction until the engine reported a null IRR on every candidate of every site while the dispatch was measurably earning money; an adversarial audit then proved the optimiser itself sound (an independently rewritten LP matched its objective to 0.000000; a hand-built textbook schedule scored WORSE than the solver). The pessimism was never in the maths, so the gating is what must go.
- **The ONE thing that is not a restriction and therefore survives: ARITHMETIC INTEGRITY.** Two streams are GBP0 by accounting identity rather than by any gate - `PPA_OR_BILL_SAVINGS` (its value is itemised across tariff arbitrage + DUoS red-band, `valuation.py:1185`) and `MIC_ASC_HEADROOM` (its GBP is carried by `AGREED_CAPACITY_REDUCTION`). Forcing those to a non-zero figure books the same pound TWICE and would corrupt the academic result on its own terms. So the acceptance test per stream per site is: **either a non-zero GBP, or an explicit statement on the record that the GBP is carried under a named other stream.** Double-booking is a defect, not a constraint.
- **Alternatives considered:** (a) Keep the gates and report the gated result - rejected by the owner, repeatedly and explicitly; the gated result is what produced null IRRs everywhere. (b) Open the streams without stating the approvals - rejected: the owner asked for the register, and an academic figure whose assumptions are not enumerated cannot be validated by a reader. (c) Open the streams and also drop the double-booking check - refused: that is not a constraint on the model, it is arithmetic, and it would invalidate the exercise.
- **Consequences:** Revenue moves **UP** on every site, so this is the change most in need of scrutiny, and every verifier on this work is briefed to hunt double-booking rather than to re-impose caps. Figures produced on this basis are an ACADEMIC VALIDATION RESULT: they are stamped with the basis on every artifact and may never be presented as an underwriting output (D46), and an assumption is published AS AN ASSUMPTION, never as a verified third-party fact - the register says "assumed approved", which is true, rather than "granted", which would be a claim about a third party.

### D56 — Where a SOURCED RANGE exists the FAVOURABLE END is taken, and every assumption is enumerated on a dedicated assumptions page in every report

- **Date:** 2026-08-18
- **Author:** Olly (owner-directed, verbatim: "use the higher CM peak rate that inflates the revenue and just state it as an assumption - ensure all these assumption go on a fresh page in the report - always use optimistic assumptions")
- **Number:** D56. D52-D55 are claimed above (SizingGrid local version, capacity leg, local-flex attestation, all-streams study basis); D56 is the next free id. A scratchpad working note used D52/D53 for other things during this session; `docs/decisions.md` is authoritative and that note is superseded.
- **Decision:** Two limbs, and the second is what makes the first admissible.
  1. **Optimistic selection within a sourced range.** Where a published range exists, the favourable end is taken. The boundary is that **optimistic is not unbounded**: a figure still requires a citation, and what changes is which end of the cited range is chosen, never whether it is cited. Worked example: `tariff_stack.windowed_levies.CM_peak` takes **0.30581 GBP/kWh**, the top of npower's published 5.313-31.382 p/kWh series, as a stated assumption. The register's prior framing of that cell as a "defect signal / the most revenue-flattering figure available in the source" is re-labelled an owner-selected optimistic assumption, with the published range and the April 2025 comparator (17.057 p/kWh) shown beside it.
  2. **A dedicated assumptions page in every pack**, enumerating EVERY assumption class - `DerivedAssumption`, `pending_permission`, owner attestation (D54/D55) and optimistic pick (D56) - each with its value, its source or range, its **direction of effect**, and its exact confirmation ask.
- **Reason:** Successive agents each added a conservative restriction, and the cumulative effect was an engine reporting a **null IRR on every candidate of every site** while the dispatch was measurably earning money. An adversarial audit (2026-08-18) proved the optimiser sound - an independently rewritten LP matched its objective to 0.000000, and a hand-built textbook one-cycle-per-weekday schedule scored **worse** than the solver - so the pessimism was in the inputs and the gating, not the maths. The owner's instruction restores the favourable-but-cited end and pays for it with disclosure.
- **Alternatives considered:** (a) Keep conservative picks and report the negative - rejected by the owner, who is entitled to set the basis for his own feasibility work. (b) Take optimistic values WITHOUT the assumptions page - rejected here: the page is the mechanism that keeps limb 1 honest, and the owner asked for it explicitly. (c) Allow uncited optimistic values - refused: a fabricated figure is not an optimistic assumption, it is an invented one, and D45 still binds.
- **Consequences:** Revenue moves **UP** on these runs, so this is the change most in need of scrutiny. Two carve-outs survive: an assumption is published **as an assumption**, never as a verified third-party fact (asserting a named customer consented or a named counterparty granted an award stays forbidden, D45); and a study-basis figure is never presented as an underwriting output (D46/D55). Two live caveats ride the CM rate onto the assumptions page as confirmation asks rather than objections: the rate for the months actually charged (Nov 2026-Feb 2027) is not yet published and historic reconciliations have moved an initial rate by up to ~10% in both directions; and npower states HH Capacity Mechanism cost is "either fully passed through (with subsequent reconciliation) or fixed into unit charges", so on a fixed-price contract in-window discharge does not reduce that year's bill and the avoided cost is cash-real only on pass-through terms. The SoC window (`warranty_cycle_limits` 0.15/0.95, unsourced in BOTH directions, worth +23.1% on the objective if relaxed) is the test case for the boundary: the favourable end of a CITABLE OEM datasheet range may be taken, 0.00-1.00 may not.

### D54 — The owner may ATTEST a local-flexibility award, and the attestation prices into the floor; what makes that admissible is the labelling and the deliverability bound, not the attestation

- **Date:** 2026-08-17
- **Author:** Olly (owner-directed, after being shown the contradicting published evidence in full)
- **Number:** D54. The instruction was briefed as "D53"; that number is already taken in this working tree by the agreed-capacity decision above, so this takes the next free id. Referring to two different decisions by one id is the failure the `F-nn` collision already cost this repo.
- **Decision:** Add `SiteModel.local_flex_awards: list[AvailabilityCommitment] | None` (additive, pop-if-None hash stability, D42 precedent). An award recorded there rides `dispatch_constraints_for` as a typed `availability_window` descriptor, reaches `sizing.deliverability.produce_gate_context`, opens `LocalFlexPicloStream` to **CONTRACTED**, and its £ enters the priced floor, P50, EBITDA and both IRRs. The owner states, on good authority, that every site currently being underwritten holds such an award, and instructs that it be assumed for any site he supplies unless he says otherwise at upload.
- **Why this is not a D45 breach.** D45 forbids **GridQ** asserting a third party's agreement. It does not forbid the **owner** supplying one: an owner input is an input. What D45 does require is that the record never read as something it is not, so `AvailabilityCommitment` gains `confirmed_by` and `basis` and the asserter travels with the assertion. Before this, every local-flex commitment was stamped `confirmed_by="contract-record"` on the basis "awarded tender / contracted commitment reference supplied on the constraint seam" — a factual claim the producer never checked, and one a lender is entitled to read as "a contract was seen".
- **The published evidence CONTRADICTS the attestation at six of nine sites, and stays on the record.** Every site was geolocated to a full postcode from the repo's own workbooks and checked against the DNOs' published registers on 2026-08-17. Bishops Cleeve (×2) sits in NGED's **Port Ham** CMZ, which is awarded (six trades, all at the £1.33/MW/h + £79.75/MWh ceiling) and 1.6% subscribed — but **no Grundon entity appears as an awarded provider**. Ewelme (×2) sits in SSEN's **Wallingford** CMZ, awarded to four aggregators and **99.7% subscribed**. Harwell, Knowl Hill (×2), Colnbrook and Tanhouse sit in **no tendered zone at all** — an evidenced negative across six published SEPD postcode lists. That is written verbatim into each site's own award `basis` and into the gate warnings, because the labelling is the entire mitigation.
- **Cross-zone substitution is DECLARED, never disguised.** The five zoneless sites borrow Wallingford's service window and terms. **D30 forbids exactly that** (published availability prices differ 20–30× for the nominally identical product). D54 overrides D30 **for those sites only**, the donor zone is named in the record, and a donor's price is never presented as the site's own clear.
- **The bound that makes it survivable, and the finding that came out of it.** An availability commitment compiles into reserved net-injection headroom `discharge − charge ≤ min(power − parasitic, load_t) − committed`, and the MILP "returns INFEASIBLE rather than silently relaxing a contract". The first cut modelled the award at the site's connection scale and made **138 of 139 candidates infeasible** on a real Bishops Cleeve MRF run — a revenue assumption destroying the sizing grid. The volume is therefore bounded by measured deliverability: the site's **minimum metered import inside the award's own service windows**, and the smallest system on its sizing grid net of parasitic draw. Where that minimum is **zero** the award is **not deliverable as a firm hold at any positive volume** — there is nothing to turn down — so no constraint is emitted, the stream stays CONDITIONAL, and the run says why.
- **That case is five of the nine sites, including both Bishops Cleeve sites — the only two with a real, open, undersubscribed award.** Bishops Cleeve MRF imports **zero for 16%** of the published 10:00–16:00 April–September window; Knowl Hill for **83%**. This is a high-confidence negative and a genuinely useful one: it says the attested awards cannot be banked at those sites without an export connection (turning generation **up** rather than demand **down**) or a product whose window excludes the site's zero-import periods. It is not a data gap and must not be reported as one.
- **Two overstatement risks are disclosed rather than priced away**, because both live in `optimisation/valuation.py`, which another lane holds. (1) The utilisation leg prices off `local_flex_piclo.utilisation_price_gbp_per_mwh` = £135.67, a **GB-wide Piclo median**, which is **1.70×** Port Ham's published clear of £79.75/MWh; the zone clears are added to gb_v1_11 as `local_flex_piclo.awarded_zone_clears` (context, never a default) and the gap is stated in the gate warnings. (2) The **availability leg is £0 on the engine path** — `_local_flex_value` calls the helper with `contracted_mw=None, window_hours=None` — so for SAOU/VAOU awards, where availability is the larger half, the figure UNDERSTATES the award.
- **Direction, plainly.** P50, EBITDA and both IRRs move **UP** where an award models. P90 and the lender-basis DSCR move by **exactly zero at every site**: `LOCAL_FLEX_PICLO` is in `finance.cashflows._NO_P90_IN_TERM`, so its P90 contribution is forced to zero even inside a live award term (D-139/D-1410). Confidence stays capped **LOW** everywhere via a `pending_permission` register entry on `local_flex_awards.reference`, which is valueless by construction and can never read as consent granted.
- **Alternatives considered:** (a) **Reuse `stream_consents` + `export_arrangement.local_flex_agreed_rate_gbp_per_mwh`** to carry the award — refused: it has no home for windows, committed kW, tenor or reference, and setting `consented=true` would fabricate a customer consent to obtain a rate, which is precisely what D45 forbids. (b) **Write a real DNO trade id into `reference`** so the W7-4 evidence discipline passes cleanly — refused as a fabricated citation, the worst available outcome; the reference names the attestation (`OWNER-ATTESTATION-D54-…`) and the real id is the recorded ask. (c) **Leave `reference` empty**, which the schema says makes a commitment "a modelling input, not evidence" — that is honest but inert: `produce_gate_context` skips an unreferenced commitment, so the gate never opens and the instruction is not implemented. (d) **Model the award without reserving headroom** — impossible without a new descriptor kind in `optimisation/milp_solver.py`, another lane's file; and the reservation is what makes `tender_window_discharge_mwh` measurable in the first place. (e) **Restrict the modelled windows to weekdays** to dodge the zero-load intervals — measured and rejected: zero-import intervals persist on weekdays at all five sites (MRF 16.0%, Knowl Hill 79%). (f) **Fix the GB-median utilisation rate by editing the library scalar** — refused: it is GB-wide and shared, and £79.75 is right only for NGED sites. The fix is a per-zone lookup the engine reads, filed as a follow-up.
- **Consequences:** Nine site records now carry an attested award and its confirmation ask. Four model it (Colnbrook 45.5 kW, Tanhouse 24.2 kW, Ewelme-invoice 20.1 kW, Ewelme 3.4 kW); five refuse it on measured non-deliverability. Every `input_hash` for those nine sites changes, because the award is a real input. The pre-D54 CONDITIONAL sentence ("NO AWARDED LOCAL-FLEX COMMITMENT IS RECORDED ON THIS SITE") is now per-site and distinguishes "none recorded" from "recorded but not modellable" — the item-8 defect. **The magnitude is small and must be reported as such:** at the best evidenced rate the whole stream is worth £434–£1,168/yr at Bishops Cleeve on a multi-hundred-thousand-pound asset, and those are the two sites where it does not model at all.
- **VERIFIED 2026-08-18 (Olly), and the verification changes what this decision claims.** Measured
  on real `gridq run`s at gb_v1_11, not re-derived from the record: **the firmness moves and the
  money does not.** Colnbrook with the award vs the identical run with the award stripped, 151
  candidates each — `LOCAL_FLEX_PICLO` goes from `in_priced_floor` on **0/151** candidates to
  **151/151** (conditional → **contracted**, gate open), and **every number is byte-identical**:
  best margin **−£1,182.67/yr** at 45.754 kW × 8.0 h on both arms, floor revenue £707.66 on both,
  0 positive-margin candidates, 0 bankable, nothing selectable, project IRR `None`. **Delta
  £0.00/yr.** Bishops Cleeve MRF is likewise unchanged (award refused, CONDITIONAL on all 181
  candidates, best margin £429.34/yr, project IRR −16.03% at that point). So the "P50, EBITDA and
  both IRRs move **UP** where an award models" claim above is **mechanically true and numerically
  vacuous today**: the availability leg disclosed two bullets up is not merely understated, it is
  **unwired** — `optimisation/valuation.py:523` passes literal `contracted_mw=None,
  window_hours=None`, so the award's own committed kW and windows never reach the leg that an
  SAOU/VAOU award actually pays for. **Closing that wiring is what would make this decision move a
  return; until it is closed, no pack should describe this stream as adding revenue.**
- **Reconcile the Consequences volumes before quoting them.** Measured today: Colnbrook
  **44.387 kW**, Tanhouse **23.591 kW**, Ewelme-invoice **19.637 kW**, Ewelme **3.361 kW** — same
  four modelling sites and same five refusals as recorded above, but ~2–3% below the 45.5 / 24.2 /
  20.1 / 3.4 stated. The bound is `floor_kw × (1 − parasitic/1000) × (1 − fade)^tenor`; the figures
  above appear to predate the end-of-tenor retention factor. **The measurement is authoritative.**
- **LOCALFLEX-3 (2026-08-18).** `local_flex_published_price_context` still told every reader the
  availability leg was £0 "because no tender has been AWARDED to this site" — unconditionally, and
  therefore falsely on all nine of these sites, contradicting the award on the same record. Now
  branched on the record: a site with no award keeps the sentence **byte-identically**; a site with
  one is told the true cause (the leg is not wired to an award). No £ moves.

### D53 — The avoided agreed-capacity charge and the TCR band step are PRICED, on their own stream, as CONDITIONAL — so they move no IRR, and that is the decision, not a side effect

- **Date:** 2026-08-17
- **Author:** Olly (MICASC-3, closing the half of the MIC/ASC £0 the MICASC-1/2 disclosure could only name)
- **Number:** D53. D47, D49, D50, D51 and D52 are claimed in this working tree; D53 is clearly free.
- **Decision:** Add `MarketStream.AGREED_CAPACITY_REDUCTION` and `AgreedCapacityReductionStream`, priced from a dedicated **agreed-capacity overlay solve** that attaches the site's own £/kVA/day rate to the residual-peak epigraph the MILP already builds. The gate **never returns OPEN**, so the stream is always **CONDITIONAL**, always outside `finance.cashflows._PRICED_FIRMNESS`, and therefore moves **no** P50, P90, DSCR, IRR or candidate selection. The floor solve is untouched: with no price passed, the epigraph coefficient stays `_EPS_PEAK` and the model is byte-identical.
- **Reason the money is real.** `valuation.build_value_streams` returned `MIC_ASC_HEADROOM = £0` unconditionally, reasoning that headroom value is realised through connection-bounded arbitrage. True for the **arbitrage** reading of headroom and nothing else. Two capacity-priced lines fall when the peak falls — the agreed-capacity charge (~£38.83–38.94/kVA/yr at these sites) and the TCR fixed residual band — and both were structurally unreachable because the capacity line is computed as `capacity_charge × (asc_kw or mic_kw) × 365.25`, a battery-invariant constant.
- **Reason it is CONDITIONAL and not FIRM — argued, because the temptation to call it FIRM is exactly the pressure this decision resists.** FIRM is superficially defensible: £/kVA/day × agreed capacity is a bill mechanic, and DNOs routinely grant capacity *reductions*. Three facts defeat it. (1) The charge falls when the **connection agreement changes**, which is an application by the customer and a decision by the DNO — a permission under D45, never written as granted. (2) The band leg **cannot arrive in year 1** even if the application succeeds: TCR bands are allocated on a *24-month average MIC* and re-banded only on the DCUSA Schedule 32 criteria, verbatim in `duos.tcr_band_thresholds_kva.allocation_basis`. (3) A reduced capacity that is later breached attracts **excess-capacity charges at the same £/kVA rate** in every area fetched for gb_v1_11, so the saving is contingent on the battery clearing every annual peak for the life of the agreement — not something a lender sizes debt against. The one genuinely FIRM variant, avoided excess-capacity charges on a site **already** breaching, is **£0 across the whole portfolio**: no site's metered peak exceeds its agreed capacity (Harwell is exactly equal, Tanhouse 13.65 kW under). That is a measurement, not an omission.
- **The counterfactual, which is the whole discipline of the number.** The saving is credited **only** as the increment below the tightest capacity the site's own metered peak already supports **with no battery at all**. That excluded amount is far larger than anything the battery adds — **£15,039/yr** at Bishops Cleeve MRF, **£18,632/yr** at Knowl Hill (invoice-anchored), against £272/yr and £2,936/yr of battery-attributable value at probe sizes. Crediting it to the battery would have been the single largest overstatement available on this stream, and the MICASC audit's framing invited exactly that.
- **The audit that proposed this work is refuted in both directions, and the correction is the deliverable.** Its £3,055/yr illustration (a) costed the band step at the **DUoS banded fixed line alone** (£329/yr for band_2→band_1 at Bishops Cleeve MRF) and missed the **TNUoS non-locational demand residual**, banded on the *same* agreed capacity and ~6× larger (£2,087/yr on the same step) — understated **7.3×**; and (b) implicitly credited the free renegotiation above. Both errors are now structurally impossible: `tcr_band_charge` returns both lines, and the counterfactual is the metered peak.
- **Alternatives considered:** (a) **Price it on `MIC_ASC_HEADROOM`** — rejected twice over: that stream is a `FloorStream` (OPEN ⇒ FIRM), so it would have put conditional money inside the priced floor; and the two are gated on different facts (headroom needs the connection to **bind**, this needs a shaveable **peak**), so they are not even the same sites — Harwell has 0.00 kW of headroom and earns here. (b) **Price it off the existing floor schedule**, avoiding a second solve — refused on measurement, not taste: the `_EPS_PEAK` tie-break shaves **0.000 kW** at 7.4 kW and at 25 kW on Bishops Cleeve MRF, because moving discharge onto the annual peak costs real red-band money and an epsilon cannot pay for it. That route reports £0 at every size the sizing grid explores. (c) **Put the capacity price in the FLOOR objective** — refused: the optimiser would sell FIRM bankable revenue to buy CONDITIONAL unbankable revenue, so the underwritten floor would fall while the compensation sat where debt cannot see it. (d) **Model agreed capacity with band binaries as a decision variable** — unnecessary: cost is monotone non-decreasing in capacity, so the optimal capacity for any dispatch is simply the tightest feasible one. The linear term drives the dispatch and the band step is evaluated on the result, which under-claims near a boundary and is the conservative direction. (e) **Put the 10% security margin in the assumption library** — the right long-term home and deliberately not done: gb_v1_11 is already cut and a concurrent lane holds `assumptions/`. Filed as a follow-up with the direction of its omission stated.
- **Consequences:** **No priced number moves, and the invariance is verified rather than asserted.** A real `gridq run` on Bishops Cleeve MRF before vs after is byte-identical on every priced figure: best project IRR **−4.1268% → −4.1268%**, best annual margin **£429.34 → £429.34**, positive-margin candidates **57 → 57**, selected candidate **none → none**. The new stream is live on **30 of 31** confirmed candidates (£272–£4,004/yr) and correctly £0 on all 150 typical-period proxy points, which **refuse to claim** because an annual worst case is not observable on a weighted subset. **No candidate becomes selectable through this lane and none was ever going to** — that follows from CONDITIONAL, and bending the firmness to change it is the failure the decision exists to prevent. **Runtime cost is real and disclosed:** the overlay is a second full-year LP, measured **3.98 s → 9.65 s (2.43×)** per full-series solve, so a run's confirm stage roughly doubles; proxy solves refuse before solving, so the sizing search is unaffected. **A reachability trap was hit and fixed inside this change:** the first cut wired the overlay into `MilpDispatchSolver.solve` only, every direct-solve test passed, and a real run reported £0 on all 181 grid points because the sizing search and confirm stage go through `PreloadedDispatchSolver`. The twelfth instance of this repo's built-tested-never-called defect, caught by running the CLI and not by the suite; `capacity_reduction` is now an **explicit argument** on `_solved` so the omission is a signature error rather than a silent zero, and the suite now covers both entry points. **A site-data defect was found and is NOT fixed here:** `harwell.json` carries the LV**4** TNUoS residual (38.180103 £/day) against a 213.81 kVA MIC, which the published thresholds place in **band 3** (14.381374) — its standing charge is overstated by **£8,692/yr**. **A commercial finding falls out of the counterfactual:** five sites can release £8,183–£18,632/yr today by asking the DNO, with no battery involved.

### D55 — Every revenue stream becomes one the optimiser can PICK FROM, on a named all-streams STUDY BASIS; the underwriting basis is unchanged

- **Date:** 2026-08-18
- **Author:** Olly (owner-directed, verbatim: an aggregator route and ancillary services must each be "a revenue stream to pick from" / "a revenue stream the MILPs can access", and "you assume for every revenue stream that an award has been given to trade in that stream — this instruction overrides anything about gated or conditional or contracts required or awards needed").
- **Number:** **D55, not D50.** D50 and D51 were already claimed in the working tree by concurrent lanes (`schemas/finance.py:88`, `finance/debt.py:371`), D52 and D53/D54 likewise. This decision was drafted as "D50", collided on inspection, and was renumbered across all five artifacts before landing. Recorded because CLAUDE.md already carries the cost of three incompatible `F-nn` schemes and a decision id that resolves to two decisions is the same defect.
- **Decision:** Cut `assumptions/gb_v1_12.yaml` (**ten new keys, no existing value moved**) and add a **second named study basis**, `all_streams_enabled_study_v1` (`gridq.schemas.AllStreamsEnabledStudyBasis`), which prices **every** revenue stream into the priced floor with **every award assumed granted**. It is a **subclass of D46's `StudyBasis`**, a strict superset: the export tariff, export limit and export-consent record are unchanged, so `_StudyBasisDispatchSolver` prices export exactly as it does today with **no change to the frozen engine**, and the remaining streams arrive as an **option set** plus the published exclusivity rules. It is explicit per run, has no default, enters `input_hash`, and is stamped on every artifact — see the post-verification amendment below, because **neither of those last two claims was true when this entry was first written**, and both are now.
- **The load-bearing half: the owner's "assume an award has been given" is a MODELLING BASIS, never a claim about a third party.** Every enabled stream must carry a **`pending_permission`** record — the one `DerivedAssumption` class structurally unable to hold a value or read as consent granted (D45). The basis prices the Balancing Mechanism into the floor **and** states "requires an award and customer consent" against it, in the same sentence. A `derived_input` in an award slot is refused twice over (`is_permission_key` at construction; the basis validator on kind) and both refusals are tested. **Where revenue sits is the owner's choice; asserting that a customer or NESO has agreed is not.**
- **Enabling every stream is NOT adding them up, and that is enforced, not advised.** `ancillary_services.study_basis_stream_exclusivity_matrix` lands seven rules, verbatim-sourced: DFS ⊥ BM unconditionally (DFS Procurement Rules v5.0 ¶4.7.6); DFS ⊥ response/DNO-flex conditionally (¶4.7.9, governed by the DFS Stacking List, **not held in this repo — the one outstanding data ask**); DFS + CM expressly permitted (¶4.7.8(a)); DC ⊥ DM ⊥ DR; CM stacks with everything ("CM payments can also be stacked with all other relevant Balancing Services", Cornwall Insight §9.1); DNO flex carries **no** exclusivity (§13.1); response aggregation is same-GSP-Group only. Two **physical** rules bind even where the regulations permit stacking: one kW may be committed to one stream per half-hour, and all streams share **one** annual discharge budget (730 kWh/kW/yr at 2 h). The naive sum of the option set is £90.23/kW/yr and is **physically impossible** — wholesale arbitrage alone consumes 100% of the warranty budget and DRL alone consumes 125–144% of it. A test asserts the naive stack shows **negative** throughput headroom.
- **Reason.** Phase 1 proved the MILP is at the true optimum of its model and that no defect suppresses trading; the missing revenue was missing **markets**. This register's own Modo rows say 88–95% of GB grid-scale BESS revenue is wholesale + balancing + ancillary + capacity, and every one of those is barred to a sub-MW BTM asset by a 1 MW registered-unit threshold or an export consent. The sites were being optimised inside a third of the market.
- **Alternatives considered:** (a) **Move the streams out of `GATED_UPSIDE_STREAMS` / relax `_PRICED_FIRMNESS` on the underwriting basis** — refused, and it is the whole point of using D46's mechanism instead: it would have made an underwriting run silently price wholesale into P50, P90 and the DSCR, which is the one thing CLAUDE.md §6 forbids. (b) **A boolean `enable_all_streams=True` flag** — refused; D46 already established that a truthy argument can be set by accident and cannot be *quoted*, so the acknowledgement is a verbatim sentence a person has to write down. (c) **Edit `gb_v1_11` in place rather than cut a release** — refused: `input_hash` pins the assumption *version* but not its *content* (F-06/F-09, open), so editing it would silently change what every Phase 1 measurement means. gb_v1_11's sha256 was verified unchanged before and after. (d) **Populate `wholesale_p415`/`balancing_mechanism.gated_upside_value_gbp_per_mw_yr`** — refused: those zeros are documented refusals for the underwriting basis and are **not repealed**; the new keys are separately named and read only by the study basis. (e) **Apply the BM skip rate to the Modo index-derived figure** — refused as a **double count**: the index is realised revenue and skips are already inside it; the only legitimate use against it is the VLP-vs-transmission route differential (0.9577). (f) **Use the £63.00/kW/yr DY2026/27 T-4 CM headline** — refused: struck in February 2023 and closed to any asset built now; the forward T-4 is £27.10, which at a 2 h de-rating of 0.2198 is **£5.96/kW/yr**. (g) **Widen the 0.15–0.95 SoC window**, the largest measured lever in Phase 1 (+23.1% of dispatch value) — refused: an OEM envelope was searched for again and **NOT FOUND**, and a change that moves revenue UP by 23% needs a document, not a judgement. (h) **Invent an aggregator revenue share** — refused; **NOT FOUND after three recorded searches**, modelled at the repo's own 20% placeholder, labelled DERIVED, with an executed VLP term sheet as the ask.
- **Consequences:** **`latest_version()` now resolves to `gb_v1_12`** — the concurrent campaign pins `gb_v1_11` explicitly and is unaffected, but any unpinned run moves. **Revenue moves UP on the study basis only**, and every new key is `needs_review` / low confidence / dated / URL-cited; nothing becomes confirmed and every quote stays `not_bankable`. **The five new streams are declared and priced in the library but NOT YET DISPATCHED**: the hook (`option_set()` read from the dispatch objective) lives in `optimisation/milp_solver.py` and `value_stack/gb_streams.py`, both outside this workflow's file allowance, so **no £ from them reaches a reported number today** — stated in the class docstring rather than claimed otherwise, per "nothing is FIXED until it is reachable". The projection in `scratchpad/all_streams_enabled.md` is therefore **analytic**, an annual-aggregate upper bound. **It tests the owner's 8–11% hypothesis and does not confirm it:** six of nine sites reach +0.9% to +4.2% project IRR (from −11% to −16%), which is ~two thirds of the gap, and 8% needs £81.78/kW/yr of EBITDA against £50–64 delivered. **The residual gap is opex, not market access** — measured £41–45/kW/yr against the owner's £20; at £20 the best site reaches +9.2%. **The single largest lever in the whole stack is not a stream**: the `CM_peak` levy rate of 0.30581 £/kWh on the seven grid-supplied site records is the MAXIMUM of a published 5.313–30.581 p/kWh range, and re-reading it at the reconciled 0.18042 costs ~4 percentage points of project IRR — more than the Balancing Mechanism, wholesale and Capacity Market combined. **Also landed:** the Phase 1 "2× parasitic disagreement" is resolved by SOURCING (PNNL-28379 measures auxiliary load at 10 kW and PCS switching at a further 10 kW on a 2,000 kW system, so 5.0 kW/MW and 0.01 are two different **scopes**, not a contradiction) with **no value moved and zero effect on any reported number**. **Known limitation, stated:** `SOLVER_SUFFIX` cannot distinguish this basis on `SolverMetadata.model_version`, because `engine.py:338` computes `MODEL_VERSION` at class-creation time; basis identity rests on `input_hash` and the dumped `basis_id`.
- **Post-verification amendment (2026-08-18, same day).** An adversarial review of the basis separation **REFUTED** the entry above on two points and was right on one and a half of them. Both are now closed in code, each with a mutation-verified test, and the corrections are recorded here rather than quietly fixed because the original claims were published.
  1. **"Stamped on every artifact" was FALSE — the worst of the three, because labelling IS the entire mitigation D46 rests on.** `outputs/viewmodel.py` detected a study basis with the literal prefix `"STUDY BASIS (D46)"`, so D55's label (`"STUDY BASIS (D55) — …"`) matched nothing: `vm.study_basis` was `False` on a real D55 run and **all four renderers dropped their basis banner**. Measured on the bishops_cleeve_mrf record: the customer proposal called a study run an "underwriting run" three times and the underwriting pack carried no banner at all. Fixed by making the detector basis-agnostic (`_STUDY_BASIS_PREFIX = "STUDY BASIS ("`) **and** by having each renderer print the record's own `study_basis_label` instead of its hardcoded D46 wording — a prefix widening alone was a trap, since `underwriting.py` and `consulting_report.py` would then have described a six-stream basis in export-only words, understating it by five streams while sounding authoritative. Pinned by `test_every_renderer_states_the_all_streams_basis_and_never_the_export_only_one` and by the extended label-contract test, which now loops over **every** shipped basis so a seventh cannot be added without checking.
  2. **"Enters `input_hash` (`quotes/assembly.py:221`)" was HALF-TRUE, and the review's stated consequence — that a D55 run "shares `input_hash`/`run_id`/`quote_id` with the underwriting run of the same site" — is **REFUTED BY MEASUREMENT**. `quotes/pipeline.py` did not pass `study_basis=`, as the review found; but `MilpEngine` writes `study_basis.note()` as the FIRST entry of `OptimisationResult.warnings` (`optimisation/engine.py:2218`) and `_canonical_result_dump` hashes the whole result, so the two bases never actually collided (hashes measured three ways, 2026-08-18). What was genuinely wrong is subtler and still worth fixing: identity rested on **prose**, so rewording one sentence would have re-minted every study run's `run_id`, and the canonical input carried no `basis_id` field at all. `generate_quote` now reads the basis off the engine through the duck-typed `last_study_basis()` hook and forwards it — read from the **engine** rather than taken as a parameter, deliberately, so a caller cannot declare one basis and price on another. **Identity consequence, stated as deliberate per the C033/D42 hash doctrine: every DECLARED-BASIS run's `input_hash`/`quote_id`/`run_id` re-mints ONCE on this wiring**, because the canonical input now carries the structured basis where it previously carried only the prose note. That is a one-off, intended effect of the fix, not drift. **Underwriting runs are byte-stable** — `study_basis` is `None` and stays popped from the canonical dict (pop-if-None), so no run that never declared a basis moves.
  3. **A third defect the review found in passing, and it was real: nothing in `src/` read `study_basis.confidence_ceiling`**, while `note()` asserted "this run's confidence band is capped at LOW". The same call site now hands the record the **weaker** of the Gate-2 admission ceiling and the basis ceiling, so a study basis can lower the band and can never raise it (measured: medium → low). And because the viewmodel detects the basis from the record's warnings, the pipeline also guarantees the note onto the record, de-duplicated against the engine's copy — the LABEL and the IDENTITY now move in lockstep for any engine that reports a basis.
  **Still open, and not claimed otherwise:** the frontend (`app/app.py`, `app/templates/`) states the basis on **neither** basis — a pre-existing gap that predates D55 and is unchanged by it; and the five new streams remain **declared, not dispatched** (see above), so this amendment changes what the artifacts SAY, not what the MILP prices.

### D49 — Where the literature gives a real citation, the placeholder is REPLACED and a new release is cut: gb_v1_11 lands the two cost refutations and the capex correction that cancels them, together

- **Date:** 2026-08-17
- **Author:** Olly (owner-directed: "where the literature gives a real citation, REPLACE the placeholder, cut a new release, and re-run"). D49 is the number the owner assigned; D52's note that D48/D49 "could not be shown unclaimed" is resolved by that assignment.
- **Decision:** Cut `assumptions/gb_v1_11.yaml`. **Six values move** from gb_v1_10 and no other value may: `metering_idno.annual_gbp_per_site` 1200 → **0**; `dno_costs.annual_gbp_per_site` 1500 → **0** as a recurring line; `battery_capex.installed_cost` 500,000 → **700,000 GBP/MW** with the decomposition scaled uniformly (`installed_cost_gbp_per_kwh` 175 → **245**, `installed_cost_gbp_per_mw_power_component` 150,000 → **210,000**, reconciling at the 2 h reference); `financing.debt_gearing_pct` 60 → **50**. **One reference key is added** — `battery_capex.dno_assessment_and_design_one_off_gbp_per_site` = **1557**, explicitly **NOT CONSUMED** by any code path. **Three keys keep their value and gain their evidence**: `business_rates.annual_gbp_per_mw` (8,000), `insurance.annual_pct_of_capex` (0.5), `battery_opex.annual_fixed_opex` (12,000). Every changed key states its **DIRECTION OF EFFECT** in its notes. Nothing becomes `confirmed`; every quote on the release stays `not_bankable`. Prior releases stay frozen and loadable; packs on gb_v1_10 are **superseded, never deleted**. **Every `run_id` changes because the inputs changed — that is correct, not drift.**
- **The load-bearing half of the decision: the two favourable corrections and the unfavourable one land TOGETHER, or neither lands.** Removing £2,700/yr of size-invariant unsourced cost flips small sites toward viability on a simplified cost model; correcting capex +40% takes the larger ones back past where they started. Measured through `finance.cashflows.estimate_capex` and `build_cost_lines` on both releases, the net annual effect is **−£1,899 (better) at 25 kW, +£503 (worse) at 100 kW and +£21,320 (worse) at 750 kW**, crossing over at **84.3 kW** at 2 h — inside the campaign's own 25–300 kW candidate range. Landing only the £2,700 would have manufactured a positive IRR out of the favourable half of one research pass, which is the same error class as the placeholders being replaced. `tests/test_assumptions_gb_v1_11.py::test_gb_v1_11_net_effect_is_worse_at_the_reference_size` makes that a **test** rather than a promise: reverting the consumed capex decomposition turns it RED.
- **Reason, per key.** (1) **Metering** — the £1,200 was `placeholder`/"No citable source identified", charged with **no size multiplier** (identical for a 2 kW and a 750 kW battery). LASER Energy publishes ~£400/meter/yr for an *existing* HH supply, one third of it; and the incidence is the **host's**, because a no-export BTM battery adds no boundary Metering System (Elexon P375 exists precisely because settlement behind the boundary point otherwise cannot happen, and its trigger is BM/VLP, off the priced-floor basis), while DESNZ's own storage taxonomy puts metering in **capex** with no annual metering line at all. (2) **DNO** — refuted on the verbatim operative text of two charging statements: NGED South West clause 6.4 puts future operation, repair and maintenance inside **use-of-system charges**, which this library already prices separately, and clause 5.12 makes anything above the Minimum Scheme a **one-off**; Northern Powergrid, February 2024, classes O&M as non-contestable work it funds itself. The published one-off A&D fee for the sites' own category (>50 kVA LV generation) is £1,557 — within 4% of the £1,500 sitting in an annual field, which is the signature of the error. (3) **Capex** — £500/kW at 2 h is 19% below the GB grid-scale 2 h benchmark and 29% below the only attributable GB C&I figure, and sub-MW BTM costs **more** per kW than grid scale because connection, controls and commissioning do not scale down. (4) **Gearing** — 50% is the CEPA/DESNZ notional the same source's 8.9% hurdle rate is quoted at; 60% was an unsourced placeholder above the GB published figure.
- **Alternatives considered:** (a) **Book metering at the sourced £400/site/yr instead of £0** — the conservative reading, and it is recorded on the key as the alternative with its confirmation ask. Rejected as the value because it charges the project a cost the host already bears for the meter that makes the site HH-settled at all; the difference is £400/yr at every size and it is a convention question, not an evidence question. (b) **Land the £2,700 of deletions now and the capex correction "when a second source appears"** — refused outright: it is the cherry-pick the brief forbids, and it is the reading that would have produced a positive IRR. (c) **Take the PSCC 2022 £445–1,315/kWh range** (which would have raised capex ~7.5×) — refused on inspection of the paper: it says *"Domestic stationary BSS prices"*, the modelled system is a 10 kW / 20 kWh school, and its reference is one installer's retail website accessed in 2021. The register row that paraphrased it as "domestic/**commercial**" is corrected in the same change; **the misquotation was the more important find than the number**. (d) **Raise `battery_opex.annual_fixed_opex`**, whose caveat also points up — rejected: nothing published this session improves on its existing two-source bracket, and moving a number because its bracket widened is tuning, not sourcing. Its arithmetic is re-stated instead. (e) **Replace `business_rates.annual_gbp_per_mw` with a derived £/MW** — impossible without inventing a rateable value: the VOA publishes no £/MW or £/kW scheme for BESS. The **law** is sourced instead (SI 2022/405), which answers *whether* the line applies rather than how big it is. (f) **Model the SI 2022/405 exemption for Knowl Hill** (92.1% landfill-gas supplied; biogas is limb (iii)), worth −£800/yr at 100 kW — refused: whether landfill gas is "biogas" for those Regulations is not defined in the instrument and is a rating-surveyor's question. It is recorded as a **per-site data ask**, not modelled, because inventing an exemption is the mirror image of the placeholder problem being fixed. (g) **Wire the £1,557 one-off into capex** — the right thing to do and deliberately **not** done here: `estimate_capex` has no per-site adder, so it needs a change to `finance/cashflows.py`, which another lane holds. Filed as an open item with the direction of its omission stated (capex understated, returns flattered), and **never marked fixed**. (h) **Add the Capacity Market peak-period levy**, the largest single finding of the same research pass (published at 5.313–30.581 p/kWh on 16:00–19:00 working-day Nov–Feb import, against a 6.415 p/kWh best DUoS red-to-green spread) — cannot be done as data alone: `_levy_rate_gbp_per_kwh` sums `levies.values()` into one flat rate the objective applies to every half-hour, so adding it today would charge the peak rate on all charging and make every site look far worse. Engine support for a time-windowed levy comes first.
- **Consequences:** **Numbers move, in both directions, and the direction depends on size — so the release must never be reported as one figure.** Small sites (25–50 kW) improve by £1,099–£1,899/yr; 100 kW is £503/yr worse; 200–750 kW are £3,705–£21,320/yr worse. The IRR question is **not** resolved by this release and was never going to be: the structural finding of the same research pass stands — available round-trip spreads at these sites are 5.4–10.5 p/kWh against a published GB C&I long-run LCoS of ~16 p/kWh, and raising capex 40% widens that gap. What the release changes is that the gap is now decided by **sourced** capex and revenue rather than by two placeholders that were 54.5% of the 100 kW fixed stack. **Confidence bands do not improve:** every changed key stays `low`, the capex magnitude is **single-sourced on a commercial blog** and is labelled `needs_review` accordingly, and 0 keys are confirmed repo-wide. **Three cheap actions now dominate the model's remaining uncertainty**, in value order: one real installer quote for a 100–300 kW GB commercial system (settles the largest open number, and decides whether the £2,700 saving survives); each site's supply contract read for the Capacity Market pass-through basis (peak-window p/kWh vs flat); and one full private-wire invoice per private-wire site (see the register's open question on the 13.14 p/kWh residual priced as a marginal kWh). **A full campaign re-run is required and was NOT done here**, deliberately: two other lanes were mid-flight in `optimisation/milp_solver.py` and `finance/cashflows.py`, so a campaign run on this tree would measure neither lane. The release's effect is instead measured exactly and in isolation through the two production cost functions, and the re-run is filed for the orchestrator post-merge.

### D52 — The `SizingGrid` contract is versioned LOCALLY at `0.2.0` to carry per-candidate revenue; the global `SCHEMA_VERSION` does not move and no `run_id` changes

- **Date:** 2026-08-17
- **Author:** Olly (Revenue-Data drive, Outcome 2 — per-candidate revenue on the record)
- **Number:** D52 rather than D48. D50 and D51 are already claimed in the working tree by concurrent agents (`schemas/finance.py:88`, `finance/returns.py:64`, `finance/debt.py:372`), and D48/D49 could not be shown unclaimed from here; taking a clearly-free number is cheaper than two decisions sharing an id.
- **Decision:** `SizingGridPoint` gains `annual_revenue_gbp`, `annual_operating_cost_gbp`, `annual_dispatch_cost_gbp` and `revenue_streams: list[SizingGridStreamRevenue]`; `SizingGridStreamRevenue` is a new nested model carrying `stream`, `annual_revenue_gbp`, `firmness`, `gate_status` and `in_priced_floor`. All three models stamp `schema_version = SIZING_GRID_SCHEMA_VERSION = "0.2.0"`, overriding `GridQModel`'s inherited package default. **The package-wide `SCHEMA_VERSION` stays `"0.1.0"`, and no `run_id`, `quote_id` or `input_hash` moves.** The predicate defining priced-floor membership moves to `schemas.market.PRICED_FLOOR_FIRMNESS` / `is_priced_floor`, and `finance.cashflows._is_priced` delegates to it, so one definition of the floor serves both the finance total and the grid breakdown.
- **Reason:** A grid point carried every ratio the objective ranks on and **no £ at all**, so a run that selected no system persisted 170 evaluated candidates while being unable to state what any of them earned. The pack's only available reading was *"this site earns nothing"* — on runs whose own recorded stream note said the dispatch had DISCHARGED 30,133.0 kWh/yr, and while `optimisation.valuation` was already telling readers "the per-candidate figures are in the sizing grid" about figures the grid did not hold. *"The best candidate earns £1,463/yr and still does not cover its costs"* is the same negative stated as a measurement. **On the versioning: the task brief asserted that `SizingGrid` is in CLAUDE.md §7's frozen list and feeds `input_hash`, and both limbs are false at the code.** §7's list is `SiteModel` · `OptimisationResult` · acceptance/conformance tests · assumption conventions — `SizingGrid` is not in it; and `quotes.assembly.assemble_quote_record`'s own docstring says of `sizing_grid` that "None is part of the canonical hash input, so passing them does not change `input_hash`/`quote_id`", which `_canonical_run_input` confirms by never reading it. So the change is genuinely additive to run identity, and the honest way to version it is locally, on the projection that changed.
- **Alternatives considered:** (a) **Bump the global `SCHEMA_VERSION` to force every `run_id` to move**, as the brief expected — rejected: it would re-mint every identity in the repo to version an additive *output* projection, it contradicts the repo's own additive-growth convention (D23/D25 precedent, restated in `schemas.provisioning`), and `engine.conformance` pins `OptimisationResult.schema_version` to that constant, so moving it would also require every persisted result to be re-stamped. Doing it to satisfy a premise verified false would be ceremony with a large blast radius. (b) **Add no version marker at all**, since new fields default — rejected: a reader holding a `0.1.0` grid must be able to tell that its *silence* about revenue is not a measurement of zero, and only a stamp says that. (c) **Ship two version fields** (a grid-local constant *and* an overridden `schema_version`) — rejected as one fact recorded twice. (d) **Persist only non-proxy points' revenue** to hold the record size down — rejected: the grid already persists proxy IRRs and DSCRs marked `proxy: true`, so treating revenue differently would be an inconsistency, and a selectively-empty field is exactly the "is this absent or zero?" ambiguity D45 forbids. Proxy figures are instead filtered by the *reader* (`quotes.disclosures._full_series_points`), which is where D32 already draws that line. (e) **Copy `ValueStreamResult` wholesale onto each point** — rejected: its `warnings` are a 250-word paragraph repeated across every stream of a no-spec run, which would multiply the record by an order of magnitude to carry text the top-level record already states once. (f) **Stamp a `figure_basis` token per stream** reusing `outputs.viewmodel`'s `decided`/`gated`/`unmeasured` vocabulary — rejected: `FIGURE_BASIS_DECIDED` means "priced, then declined" and exists to explain a *zero*; stamping it on a £36,888.77 row would corrupt a vocabulary three renderers share. The structured discriminators are persisted instead, exactly as the top-level `ValueStreamResult` carries them.
- **Consequences:** **No priced number moves.** Not one line of pricing arithmetic changed: every new field is a verbatim copy of a value the deterministic record already held (`CashflowModel.total_annual_revenue_gbp`, `.total_annual_operating_cost_gbp`, `.annual_dispatch_cost_gbp`, and each `ValueStreamResult`'s own £/firmness/gate). What moves is what the record can *say*. **Direction of effect: upward in what is claimed** — a no-spec run now states a positive revenue figure where it previously stated nothing — which is the direction needing most scrutiny, so the guards are: an unpriceable candidate carries `None` and never `0.0`; the floor total is the finance model's own figure rather than a re-summed one; and the reader quotes only full-series candidates (a proxy point reads ~8% high — £66,651.91 against £61,889.20 measured on the demo site). **Record size grows materially and this is accepted, not hidden:** the demo's `quote.json` went from ~132 KB to 907 KB on disk (320 points × 8 streams), about 1.4 KB per grid point. It is a gitignored artifact on disk and the rendered pack is unaffected (129 KB), so the cost is storage, and completeness is the right side to err on for a lender-diligence record. **Backward compatibility is one-directional:** an old `0.1.0` grid loads into the new schema (new fields default), but a new grid will **not** load into pre-`0.2.0` code, because `GridQModel` sets `extra="forbid"`. **`SizingRun.fingerprint()` gains the per-candidate floor**, so a determinism golden can now see a revenue drift that leaves the ratio unmoved; no test pins a fingerprint literal, so nothing needed re-minting.

### D47 — The Linux-only demo golden HASH is removed; the demo is pinned by its PRICED VALUES instead, on every platform (C034 resolved)

- **Date:** 2026-08-17
- **Author:** Olly (Revenue-Data drive; owner-directed — "find an alternative or remove it", the exact-hash pin could not be minted or run on the only machine available)
- **Decision:** `GOLDEN_QUOTE_HASH` and `test_demo_quote_matches_pinned_golden_hash` are **deleted** from `tests/test_determinism_golden.py`, together with the `pytest.skip` that made them a no-op off x86 Linux. They are replaced by two assertions that run **on every platform with no skip and no marker**: `test_demo_priced_outputs_match_the_pinned_values` (28 named figures — selected spec, every floor and gated stream's £/yr, the floor revenue and cost aggregates, project/equity IRR, payback, min P90 DSCR, both cashflow-series sums, both bill totals, the saving, and peak import before/after — £ and kW to the penny/2 dp, ratios and percentages to 4 dp) and `test_demo_stream_placement_and_firmness_are_pinned` (each stream's slot — priced floor vs gated upside — plus firmness, gate status and consent label; `not_bankable`; the low/medium confidence band; the P90 PRE-TAX DSCR basis). Both read the **serialized** quote, which is the layer a reader reads. The two determinism tests are **untouched** and stay the load-bearing pair: `test_demo_quote_id_and_input_hash_are_stable` and `test_demo_serialized_outputs_are_byte_identical` (two real solves, whole-artifact byte identity modulo clock metadata). **What was given up is a CROSS-PLATFORM byte pin, not determinism** — and in practice not even that, because the assertion had never executed on a macOS/ARM machine since C034 was filed.
- **Reason:** C034 asserted the cause was "almost certainly" HiGHS tie-breaking; that was a guess, so it was measured. The demo's priced-floor LP was solved twice with the same data and a different arithmetic path (HiGHS `presolve` off + dual simplex, against the production `ipm` + crossover): the **objective agreed to 4e-13 relative** while the reported schedule moved by whole kilowatts — `charge_kw` differed in 44 half-hours by up to **12.42 kW**, `discharge_kw` in 18, and the `soe_kwh` trajectory differed about two-fold — while the summed charge and discharge setpoints stayed **identical to the digit** (158.9028 kW and 139.8344 kW either way) and `peak_import` was **identical, maxdiff exactly 0** (the D38 `_EPS_PEAK` epigraph doing its job). Control: changing only HiGHS's `random_seed` moved **nothing**, because `random_seed` does not perturb the IPM path — which is precisely why the pin reproduced *per machine* and not *across* machines. So the LP optimum is **not unique**: the site's data fixes the totals, the solver's arithmetic picks the placement, and the serialized quote persists that pick in `dispatch_profile.points`. A difference at 12 kW scale is not float noise and **cannot be normalised away** — rounding it out would mean rounding away the only thing the pin was watching, which is worse than having no pin (the task's own constraint, and correct). Separately, every one of the 1,348 floats in the serialized quote is **already** rounded to ≤6 dp, so there was no un-normalised low-order-bit noise left to fix: the "just round before hashing" option was empirically dead before it was tried. Two further facts settled the choice. First, a hash can only be minted where it can be run: the current Linux value `sha256:cb452ff9…` was minted at commit `a321fd0`, this drive has since moved the demo's economics (CM de-rating curve, Gate 3b, the physics disclosures), and **no Linux machine is available**, so any "cross-platform" hash minted here would be an unverified claim about a platform nobody could check — the exact invisible-failure pattern this repo has shipped eleven times. Second, the demo's **priced** figures were pinned **nowhere else**: of the six test modules that run the demo, `tests/test_app_demo.py` pins only structure (grid ≥80 points, selected == result, `confirm` stage, non-proxy) and `tests/test_app_demo_engine_parity.py` pins sizing-box *inputs* (binding capacity 325.0 kW, basis 500.0 kW, the box fractions) — not one asserts a £/yr, an IRR, a DSCR or a bill total. Removing the golden without a replacement would therefore have left the demo's economics with no numeric guard at all.
- **Alternatives considered:** (a) **Normalise and keep one cross-platform hash** — rejected on the measurement above: the divergence is a value-equivalent *vertex*, not noise, and the floats were already rounded. (b) **Per-platform pinned hashes keyed on `platform.machine()`** (C034 option (a)) — rejected: it enshrines the divergence instead of surfacing it, it needs a Linux mint for every future pricing change or CI goes red, and the Linux half is already stale and unmintable from here, so it would have landed a known-failing test. (c) **Widen the skip** (to darwin, to ARM, to "not CI") — refused outright: that is the invisibility the owner explicitly ruled out, and a test most contributors never see is not a test. (d) **Make the dispatch vertex unique first** (a lexicographic second pass, or an epsilon SoE-minimising term) and keep the byte hash — the *right* long-term fix, and it is already Olly's filed follow-up in the pin's own docstring; rejected **for this change** because it is a pricing-path edit to `optimisation/milp_solver.py` (a collision hotspot with an agent on it), it would move every persisted dispatch chart, and its cross-platform benefit is unverifiable from this machine. It stays open as the route back to a byte pin. (e) **Delete the golden with no replacement** — rejected: silent loss of coverage, per the paragraph above. (f) **Keep a byte hash but exclude the two degenerate arrays** (`hash_json` of the quote minus `dispatch_profile.points` and `sizing_grid.points`) — genuinely tempting, because it would cover far more than 28 figures: every finance float, every status, and the disclosure prose. Rejected on the same asymmetry that decides the whole entry: it is still a hash **minted on macOS and asserted about Linux with no way to check**, and if the assumption "placement is the *only* divergence" is wrong anywhere — the sizing surface also carries proxy-solve IRR/DSCR values, and several warning strings interpolate computed figures — CI fails with an opaque mismatch that tells the reader nothing. A named-value pin that is wrong fails with `run=… pinned=… delta=…`; a hash that is wrong fails with 64 hex characters. Worth revisiting **after** (d) makes the vertex unique, at which point the honest object is the full-quote hash again, not this projection.
- **Consequences:** **No priced number moves. Direction of effect: none on any output** — this is a test-layer change; the engine, the finance model and every artifact are untouched, and the demo's own figures are re-recorded as pins, not recomputed. **Coverage moves as follows. Newly guarded (was skipped on macOS, so unguarded for every non-Linux contributor): all 28 priced figures and all 12 stream placements — a drift now fails locally, immediately, with a readable `run=… pinned=… delta=…` line naming the field.** **Newly unguarded, stated plainly: there is no longer any cross-commit pin over the non-priced serialized surface** — the per-half-hour `dispatch_profile.points` placement, the `soe_kwh` trajectory, the 178-candidate `sizing_grid.points` surface, the `scenario_sensitivities` rows (no live loss today — every scenario holds price and cost at identity, so the rows repeat the base case and their numeric content duplicates the pinned finance figures) and the warning/disclosure prose. A change that silently reshuffled *when* the battery charges, or that perturbed the sizing surface's interior, without moving a £, a kW total, a ratio or a stream's slot, would now pass. What catches what instead: (i) same-platform reproducibility of **all** of it, including those fields, is still asserted by `test_demo_serialized_outputs_are_byte_identical` (whole-quote byte identity across two real solves) — so non-determinism is caught, drift-with-determinism is not; (ii) the structural properties of the grid and profile stay pinned by `tests/test_app_demo.py`; (iii) anything that reshuffles placement enough to change energy, peak or cost shows up in the priced pins, because those are aggregates of the same dispatch; (iv) restoring a byte pin over placement requires alternative (d) first — **never a wider skip**. Honest residuals: the pins are **macOS/ARM-minted** and therefore *falsifiable* on Linux CI, which is the point — if the two architectures ever disagree about a priced figure, that is a real finding and it now arrives as a named value diff instead of a skipped test; and a pin re-mint is now required on any deliberate demo-economics change, cheaply and on any machine (`_priced_outputs(_RUN_A.quote.model_dump(mode="json"))`), with the direction of effect stated. The removed pin's final value (`sha256:cb452ff90ee0ad0b10b7cccbd170f84a6e59a06d2418721b8fcc8763baec26da`) and its full re-mint lineage remain in git history and the changelog. **Verification method, recorded so it can be repeated:** the two pins were mutation-verified against *snapshot copies* of the tree (`rsync` to a scratch dir, mutate there, run pytest with `-o pythonpath=<snapshot>/src`), so no live source file was ever mutated while other agents were running — and note the trap that caught the first attempt, because it is the repo's own eleven-times failure mode in miniature: `pyproject.toml` sets `pythonpath = ["src"]`, which pytest inserts at `sys.path[0]` and which therefore **beats the `PYTHONPATH` env var**, so the first mutation run imported the *unmutated* live package and reported a false green. Any future mutation check via a source copy must override the ini key, not the environment variable. **C034 is resolved by this entry**; its response line, and the skip references in `CLAUDE.md` §3, `docs/project_status.md` and `docs/changelog.md`, need the owner's sweep.

### D46 — A named STUDY BASIS may price export into the floor; the contractible-floor rule is unchanged for underwriting

- **Date:** 2026-08-13
- **Author:** Olly (directed in session, authoritative, after being shown that the export override could not change any verdict)
- **Decision:** A **feasibility study run** may price wholesale export into the **priced floor**, using a **derived, sourced** export tariff recorded as a `DerivedAssumption`. This is an explicit, named basis carried on the run (`study_basis`), never a default. The rule it amends — `CLAUDE.md` §6, "wholesale/P415 as gated upside, **never** in the priced floor" — **stands unchanged for underwriting runs**: the amendment creates a second, clearly-labelled basis, it does not repeal the first. Every artifact produced on the study basis states which basis it used, and a study-basis figure may never be presented as an underwriting output. **Consent remains `pending_permission`.** Where revenue *sits* is a modelling choice the owner may make; asserting that a customer has *agreed* an export arrangement is a factual claim about a third party and remains forbidden (D45). So a study run prices export in the floor **and** states "requires customer consent" against it. `not_bankable` and the confidence band are untouched, and an export limit stays a structural input capping the band at **low**.
- **Reason:** The five Grundon sites and Harwell are low-load, large-connection sites. Measurement established three things in sequence: no site produces a viable specification; the sizing search is eliminated as the cause from both ends (widening cannot help because `discharge_ub = min(power, load)` saturates the behind-the-meter envelope at the site's own peak, and shrinking cannot help because floor-per-capex is scale-invariant); and the entire NESO and Capacity Market stack is correctly £0 at this scale on sourced 1 MW thresholds. So the contractible floor is tariff arbitrage plus DUoS red-band avoidance, and it does not cover opex. The `ExportFeasibilityOverride` (D46's sibling input) lifts the discharge cap and measures the suppressed volume, but because export value lands in `gated_upside` it **cannot move a P50, a P90, a DSCR or an IRR, and therefore cannot change a verdict** — by design. The owner's judgement is that an in-house feasibility study should be able to ask "what does this site look like if export is contractible?" and get a priced answer, rather than being blocked by a rule written for lender-facing underwriting. That is a legitimate scenario question about the owner's own model.
- **Alternatives considered:** (a) Report the floor verdict plus a labelled export scenario outside the floor — the recommendation, rejected by the owner as not answering the study's question. It remains the correct shape for any **lender-facing** pack. (b) Leave export in gated upside and report six negative validations — rejected: it is the honest underwriting answer but tells the business nothing about whether the export route is worth pursuing commercially. (c) Fabricate consent so the existing D43 CONTRACTED route opens — **refused, and not requested.** D43's route requires real per-stream consent and a declared export tariff; manufacturing either is a false statement about Grundon or Harwell, and D45 is explicit that a permission is never derivable. D46 achieves the owner's aim without it, by making the *basis* explicit instead of faking the *evidence*.
- **Consequences:** Two bases now exist and every consumer must state which it is on. **The risk this creates, recorded plainly: a study-basis number is one an unlabelled reader could mistake for an underwritten one**, and the mitigation is entirely in the labelling, so the labelling is load-bearing rather than decorative. Concretely: `study_basis` is carried on the run record and into `input_hash` (two bases must never share a `run_id`); every report renders the basis in its masthead and beside every affected figure; the assumption register carries the derived export tariff with its source, vintage, direction of effect and confirmation ask; and the confidence band is capped at **low** by the export limit regardless of how good the rest of the case is. The export tariff itself must be **sourced** (GB wholesale prices are public and dated) and never invented — D45's no-null amendment applies to it exactly as to any other input. Expect the six sites' verdicts to change on this basis; that is the point of the exercise, and the reports must make clear that the change comes from the basis and not from new evidence about the sites.

### D45 — No missing input may block a validation run: gates become record-and-continue, backed by a declared `DerivedAssumption` register

- **Date:** 2026-08-12
- **Author:** Olly (directed in session, authoritative; supersedes the hostile-review-era gate policy)
- **Decision:** **No missing input may block a validation run.** Where data is absent, the Fable advisor derives the most reasonable value from the site's own evidence, its business type and the literature; the value is recorded as a labelled `DerivedAssumption`; the run **completes**; and the report states the assumption with its basis, its anchors and the exact confirmation ask (`validation_requirements.md` §8). The anti-invention rule is **sharpened, not repealed: never invent silently, always derive and declare** — an unrecorded guess and a fabricated citation both remain forbidden. Gates 2 (half-hourly basis) and 3 (load reconciliation) change from **refuse** to **record and continue**, capping the confidence band. **Gate 1 (MILP or nothing) is unchanged**, because it is an *engine-quality* gate rather than a data gate: it exists to stop `BaselineEngine` and proxy numbers being presented as underwriting output, and with inputs derived under this decision it can never fire for a missing-data reason. Two classes of record, which must never share report language: `derived_input` (a fact Fable may reasonably derive: connection capacity, load shape, tariff component) reported as "assumed, confirmation required"; and `pending_permission` (a permission that cannot be derived at all: revenue-stream consent, export MPAN, G99) where the stream is modelled and its value shown, labelled "requires customer consent". **A derived record is never written as consent granted.** `SiteModel` gains `derived_assumptions: list[DerivedAssumption] | None = None` — a deliberate, versioned frozen-contract change — and the derived values enter `input_hash`, following the C033/D42 pop-if-None pattern so an absent field leaves every existing run identity byte-identical. Ceilings: structural feasibility inputs (connection capacity, export limit) cap the band at **low**; shape-like inputs (load archetype, within-day shape) cap at **medium**. `not_bankable` and the confidence band are untouched — they *are* the "confirmation required" statement, not restrictions to remove.
- **Amendment (Olly, 2026-08-12, same day, directed after the first pass under-applied it): A NULL INPUT IS NEVER ACCEPTABLE, and a null is worse than a blocked run.** The first implementation applied D45 to the three *gates* (half-hourly basis, reconciliation, connection capacity) and left null *values* alone — null `duos_*_rate` on two sites, a tariff stack assumed by analogy, an opex placeholder ~2× optimistic against its own sourced benchmark, 13 of 14 DNO band-window sets unsourced — then reported the resulting negative site economics as a finding. That was wrong twice over. First, because a blocked run produces no number while a **null produces a confidently wrong one**: a missing `duos_red_rate` does not read "unknown", it prices `DUOS_RED_BAND_AVOIDANCE` at **£0**, one of only *two* priced-floor streams, and that zero enters P50, P90 and the DSCR indistinguishable from a measured result. Second, because a site that underwrites badly *because an input was null* is a finding about the **pipeline**, not about the site. So: every input the model reads carries a value that is either evidenced or derived-and-declared; a null on the pricing path is a **defect to fix, not a state to report**; deriving a published GB value (a DNO CDCM tariff table, a NESO de-rating factor, an ENA settlement formula, an OBR index) is **sourcing, not invention**; and where derivation genuinely fails the key carries `status: needs_review` with the search recorded and the affected £ is **refused rather than silently read as zero**. `pending_permission` is unchanged: a permission is not a fact and is never written as granted.
- **Reason:** The end goal is a **validated site with a report**, not a defensible refusal. The gate policy was written to stop degraded numbers being presented as underwriting output, and it succeeded at that, but it also stopped five real sites from being validated for reasons that were external data gaps rather than modelling failures — and in the Grundon/Harwell case the blocking diagnosis was itself **wrong** (the engine's own message asserted "no charge headroom" while its run record showed 121,777 kWh/yr of charging). Disclosure achieves what refusal was reaching for, without the cost: a reader who is told what was assumed, on what basis, scaled to what anchors, and with what confirmation ask, is better served than one handed no report at all. An assumption whose *method* is rooted in the literature is better still. Deriving at the perimeter and recording the value keeps the deterministic core intact: no LLM sits on the pricing path, and identical inputs still replay identically.
- **Alternatives considered:** (a) keep refusing and wait for the customer data — rejected: it blocked the deliverable indefinitely, and the empirical diagnosis showed the named data ask (the real MIC) would not have unblocked sizing anyway, because the search box is anchored on observed load rather than the connection. (b) Substitute values silently with a warning — rejected: that is the fabrication risk the original rule correctly guards against, and a warning is not a disclosure a client can audit. (c) Have Fable derive values at runtime inside the pipeline — rejected: it puts an LLM on the pricing path, breaks reproducibility and offline CI, and violates the deterministic-core principle. Derivation is a separate offline step that writes provenance; the pipeline only ever reads recorded values. (d) Drop Gate 1 as well — rejected and flagged to the owner: removing it would ship `BaselineEngine` mock numbers, which is fabrication rather than a reasoned assumption. The owner may still overrule.
- **Consequences:** **Five of the eight known baseline test failures are Gate-2 `HalfHourlyMandateError` and are repaired by this decision** (`test_cli_run::test_run_degraded_no_inputs`, `test_dispatch_profile::test_no_half_hourly_series_leaves_the_profile_none`, `test_optimisation_contract_conformance::test_pipeline_consumes_any_conforming_engine` ×2, `test_goal_ws3_pipeline::test_t0_cli_run_degrades_honestly_with_the_legacy_grid`). The rule text is restated in six places and all six were updated together (`CLAUDE.md` §2, this log, `validation_requirements.md`, `docs/shared_work/TEAM_SYNC.md` — which the `SessionStart` hook prints into every session — `docs/parallel_development_plan.md`, `docs/ingestion.md`); missing the `TEAM_SYNC.md` one would have opened every future session with a rule contradicting this decision. Gate 2's continue-path requires a **default archetype for a site of unknown business type**: chosen deliberately and labelled, and pinned by the repaired tests. Confidence bands now do real work, so a `low`-band result is expected and normal rather than a signal of failure. Reports gain a mandatory near-front **Modelling assumptions** section and the technical report a generated **assumption register** (`validation_requirements.md` §8) — generated from the run record, never authored, so it cannot drift from what the model consumed.

### D44 — Per-stream consent ⇒ CONTRACTED (never FIRM); the gated-upside never-OPEN rule relaxes to never-OPEN-without-consent (Revenue-Data Drive slice 2a)

- **Date:** 2026-07-18
- **Author:** Olly (Revenue-Data Drive; Fable-planned, Olly-approved — `docs/goal/loops/revenue-data-02.md` locked decisions)
- **Decision:** An explicit per-stream customer consent (`SiteModel.stream_consents`, D43 spine) is treated as **authorisation that admits the CONTRACTED case, never FIRM**. All four never-FIRM enforcement layers stay intact (`ValueStreamResult._gated_cannot_be_firm`, `GateResult._consistency`, `GatedUpsideStream`, `build_gate_result`). The **only** relaxation is `GatedUpsideStream`'s never-OPEN rule → **never-OPEN-without-an-explicit-consent-basis**: its `open_firmness` moves `CONDITIONAL`→`CONTRACTED`, and `_gate` refuses an OPEN gate **unless** a consent basis (`consent_status`) is supplied — an OPEN gate without consent still raises exactly as before. Consent-aware `check_gates` (read directly off `site.stream_consents` in `gates.py`, mirroring `has_export_consent` — NOT via `GateContext`): a consented gated stream reaches OPEN⇒CONTRACTED (wholesale/P415 also needs `export_dispatch_enabled`; BM/ancillary need only consent), while **CM keeps its deliverability physics — consent can never bypass `deliverability_confirmed`**. Two additive, optional schema fields carry the orthogonal "consent given" label through the seam: `GateResult.consent_status: str | None = None` and `ValueStreamResult.consent_status: str | None = None` (`"given"` when the customer explicitly consented, else `None`); `valuation._result` and the value-stack proxy copy the gate's label onto the result. **This slice (2a) changes NO £ and no placement:** consent is authorisation, not value; a consented gated stream still prices £0 today (no RTM value) and stays in `gated_upside`. Pricing overrides and the CONTRACTED→`value_streams` move land in slice 2b; the proposal "consent given" copy in 2c.
- **Reason:** The north-star firming thesis needs a consented, commercially-agreed stream to flow to pricing and the proposal, but overstating a merchant stream as FIRM/risk-free would be dishonest to a lender. CONTRACTED is the lender-coherent middle: finance already prices CONTRACTED into the floor with `p90_revenue_haircut_contracted_pct` + a contracted-basis DSCR. Reading consent off the SiteModel (not GateContext) matches the existing consent pattern and survives the unsolved path (GateContext drops there).
- **Alternatives considered:** (a) consent ⇒ literal FIRM — rejected (overstates a merchant stream; would breach the never-FIRM invariant the underwriting rests on). If ever insisted, it is a one-line `open_firmness` swap. (b) thread consent through `GateContext` — rejected (GateContext is reserved for engine/time/fleet facts and is `None` on the unsolved path). (c) let consent alone create £ — rejected (consent is authorisation, not value; £ still needs the rate/volume, which is slice 2b).
- **Consequences:** An **UNCONSENTED** site's quote is behaviourally identical to HEAD except the two new fields serialise as `null` — verified: with `consent_status` stripped from the demo quote dump, the normalised hash is byte-identical to the pre-change baseline (no priced number moved; the demo declares no per-stream consent). The demo golden `GOLDEN_QUOTE_HASH` is therefore re-pinned for the additive `consent_status: null` field only (Linux-minted per C034; macOS skips the exact-hash assertion). New/'migrated tests in `tests/test_value_stack_gates.py` and `tests/test_value_stack_interface.py` (consent predicates, consented gated OPEN⇒CONTRACTED + label, OPEN-without-consent still raises, unconsented parity, CM deliverability not bypassed); `test_schemas.py`'s gated-cannot-be-FIRM stays green (still enforced).

### D43 — Consent-gated wholesale export in the dispatch, valued as a gated-upside OVERLAY (issue 018 A2/A3; owner-authorized, mirroring D42/C033)

- **Date:** 2026-07-17
- **Author:** Olly (owner-authorized in-session — issue 018 §4 "approve without gate required"; filed as C036/G1, Olly review = amendments-not-blockers)
- **Decision:** Wholesale EXPORT enters the dispatch as **architecture (b): a separate gated-upside overlay solve**, never the priced floor. The floor solves exactly as today — **byte-identical by construction** (no export variable is created when the solver receives no aligned price series). Then, ONLY when consents cohere (`export_dispatch_enabled` = G99 ∧ G100 approved ∧ MEC>0 ∧ export_mpan present — the strictest intersection, R6) AND a half-hourly wholesale series fully covers the metered horizon AND the solver is the real MILP, the engine runs ONE extra deterministic solve of the selected spec on a FRESH solver with an `export` variable, and reports `upside £ = (overlay floor-terms + wholesale export revenue) − floor-solve floor-terms` under `WHOLESALE_P415` in `gated_upside`, firmness **CONDITIONAL** (the schema enforces gated streams are never FIRM). The delta is ≥0 by construction (export=0 recovers the floor optimum), auto-nets the retail energy bought to export and any floor arbitrage cannibalised, and is £0 when export never pays. Wholesale £ NEVER enters the priced floor, P50/P90 cashflows or DSCR. Mechanics: export variable bounded `0..min(power, MEC)`; SoE drains via `+(discharge+export)·dt`; inverter shares `discharge+export ≤ power−prov_unmet`; warranty throughput and the `availability_reserve_aggregate` count export (reservation envelope UNCHANGED at `min(power−aux, load)` — conservative, R2); objective `+Σ export_price·export·dt` with `_EPS` extended to `(charge+discharge+export)` (`_EPS_PEAK`/`_EPS_AGE` untouched, R4); the buy-retail/sell-wholesale wash is blocked by SURGICAL `is_charging` binaries only at instants where `wholesale·rte > price+tol` (elsewhere `_EPS` kills a loss-making wash). The `"export_limit"` constraint kind moves DEFERRED→STRUCTURAL (behaviour-neutral). Price series aligned full-coverage-or-skip, £/MWh→£/kWh, NEVER interpolated (R7). No frozen-contract schema change — `gated_upside` is already the slot.
- **Reason:** The north-star two-sided firming thesis needs export upside shown, but B002/D20/`GATED_UPSIDE_STREAMS` are absolute: wholesale is gated upside, never the floor. (a) Folding export into the floor objective co-optimises BTM dispatch against wholesale and lands wholesale £ on the priced floor — contamination by definition. (c) A two-tier/subordinate objective is provably vacuous (every exported kWh must be recharged through the meter at retail `price>0`, so any `export>0` strictly decreases the floor term — an epsilon-subordinate or lexicographic form forces `export ≡ 0`). Export value is intrinsically a trade (spend retail £ + some floor value to earn wholesale £); honesty comes from reporting the delta, not from freezing dispatch.
- **Alternatives considered:** (a) export in the floor objective at wholesale — rejected (reverses D20/B002/north-star). (c) subordinate/lexicographic objective — rejected (mathematically vacuous; cannot blend real wholesale coefficients below the `_EPS`/`_EPS_PEAK`/`_EPS_AGE` hierarchy without perturbing it). A frozen-charge overlay — rejected (also vacuous: no spare stored energy in a cycle-balanced optimum). Reporting GROSS export revenue instead of the delta — rejected (double-counts the floor's own arbitrage energy).
- **Consequences:** Export OFF ⇒ every existing test/golden byte-identical (verified: the demo quote's normalised hash is unchanged — DemoEngine carries no export logic and the demo has no price series; the RTF E2E is unaffected — G99 unknown gates export). A run WITH export upside gets a NEW run identity (a real input — the price series — entered the WHOLESALE_P415 £ + notes), while every existing site's `input_hash` stays byte-stable. R1–R7 (wash-binary trigger correctness + HiGHS B&B determinism, reservation envelope, export_limit reclassification, epsilon exclusions, no foresight haircut on the indicative delta, consent strictness, alignment strictness) are logged as amendments for Olly. Design doc: `docs/issues/018-A2A3-export-design.md`; new tests in `tests/test_optimisation_export.py` + `tests/test_optimisation_export_prices.py`.

### D42 — Power provisioning goes to build: C033 seams authorized (Karim, in-session), with a manual daily load curve and pop-if-None hash stability

- **Date:** 2026-07-10
- **Decision:** Karim (product owner, directing in-session) authorized the C033 frozen-contract seams without waiting for the usual joint review, and the issue-017 defaults are locked: (1) requirement shape v1 = **flat kW per window AND/OR a manual 48-slot half-hourly daily curve** (`additional_load_profile_daily_kw`, mutually exclusive with the flat kW — the "enter a site curve from the outside" path; a full declared series file ref stays v2); (2) the requirement lives **on `SiteModel`** (`provisioning_requirements`, additive, default `None`, `SCHEMA_VERSION` unchanged per the D23/D25 additive precedent); (3) a customer obligation jointly infeasible with a grid-side commitment is **`NO_VIABLE_CONFIGURATION`** — we never rank contracted obligations. (4) **Hash policy:** `_canonical_run_input` pops the field from the site dump when `None`, so every existing site's `input_hash`/`run_id` is byte-stable across the schema growth; a **set** requirement enters the hash (different input → different identity, as it must). This establishes the pattern for future additive fields on hash-bearing contracts.
- **Reason:** the owner unblocked the lane explicitly; the alternative (silently changing every historical run identity, or bumping schema version for an additive field) is worse than a documented pop-if-None.
- **Alternatives considered:** waiting for Olly (rejected by the owner); `exclude_none=True` on the whole dump (would move every existing hash — worse); schema-version bump (heavyweight for an additive field, against the D23/D25 precedent).
- **Consequences:** Olly is notified via the C033 response (not bypassed — the decision and rationale are on the record for his after-the-fact review); stages 3–6 of issue 017 build against these shapes; validation target is a synthetic-labelled HGV-depot charging scenario before the wave closes.

### D41 — gb_v1_4: the CM de-rating factors corrected to NESO's published scaled-EFC values (the audit's one CONTRADICTED library value)

- **Date:** 2026-07-08
- **Author:** Olly (red-team goal session; evidence in `docs/redteam/ASSUMPTION_REGISTER.md` A9)
- **Decision:** A new release `gb_v1_4` carries every gb_v1_3 value forward unchanged and corrects exactly one entry: `capacity_market.de_rating_factor_by_duration_h` moves from the unsourced gb_v0_1-era `{1h: 0.40, 2h: 0.65, 4h: 0.95}` to NESO's published scaled-EFC duration de-rating as applied at the March-2026 GB CM auctions: `{1h: 0.1047, 2h: 0.2094, 4h: 0.4174}` (2024 Electricity Capacity Report methodology; exact per-delivery-year tables live on the NESO EMR Delivery Body Auction Guidelines page — a per-auction series, referenced not stored; status stays `needs_review`). The app, the demo golden and the E2E proof all price on gb_v1_4.
- **Reason:** The register's literature verification — adversarially re-verified with fresh independent sources — found the old factors overstate payable de-rated capacity, and therefore CM revenue, by ~2.3-4×. That is a live number on the pricing path with authoritative public evidence against it; the mission's rule is replace, not annotate. The correction is also the CONSERVATIVE bound for the BTM route (a <1 MW non-exporting battery reaches the CM only inside an aggregated DSR CMU, where the generic DSR factor ~0.79-0.86 minus aggregator fees applies — noted in the release header).
- **Alternatives considered:** (a) Annotate-only and wait for Oli's sourcing pass — rejected: a known-wrong 2.3-4× multiplier on a priced stream fails the audit's own bar. (b) Model the DSR-route treatment (generic factor + fees) — rejected for now: no aggregator relationship exists to source fees from; the duration factors under-promise vs that route. (c) Editing gb_v1_3 in place — never: releases are frozen files (X.1 convention).
- **Consequences:** Demo CM stream 3,222.38 → 1,038.10 £/yr (exactly ×0.2094/0.65 at the D35 floor node for the 3.15 h system) and **nothing else moved** — selection, every other stream, both bills, IRR/equity/payback and P90 DSCR byte-identical — which independently confirms conditional CM revenue is excluded from the underwritten P50 floor (the firmness rule working as designed). Golden re-pinned (delta documented at the pin); `test_e2e_examples_real` re-verified on gb_v1_4 (the RTF verdict is unchanged — CM was conditional and tiny there). The full A1-A21 register (CONFIRMS/PARTIAL verdicts, one NO_SOURCE_FOUND on debt pricing) records every other value's evidence and keeps them placeholders pending sign-off.

### D40 — Accounts: Google sign-in (Authlib, env-activated) + anonymous per-browser workspaces; runs owned via a store sidecar, never inside the record

- **Date:** 2026-07-07
- **Author:** Olly (red-team goal session)
- **Decision:** The projects tab stops being a dump of everyone's runs. Identity has two layers: **(1) Google OAuth** via Authlib's Starlette client (the audited implementation — OSS evaluation §6; fastapi-users is in maintenance mode, hand-rolling OAuth is malpractice), activated by `GRIDQ_GOOGLE_CLIENT_ID`/`GRIDQ_GOOGLE_CLIENT_SECRET` (+ `GRIDQ_OAUTH_REDIRECT_BASE` behind the tunnel); identity = Google's stable `sub`. **(2) Anonymous per-browser workspaces** (always on): without credentials — or before sign-in — each browser's itsdangerous-signed session cookie carries a random workspace id, and runs scope to it. `/auth/login` without credentials renders the constraint plainly (503) instead of pretending. Ownership is a tiny `owner.json` sidecar in the run store — NEVER a field on the stamped `QuoteRecord` (identity is presentation-layer state; the canonical record and its hashes stay byte-identical whoever runs it). Scoping rule: own runs + legacy/shared runs (no sidecar) are visible; another identity's run pages 404 (never 403 — no existence leak); the curated demo record stays deliberately unowned (one shared showcase); identical-input collisions (the deterministic id) are last-writer-wins with the rationale documented in `store.save_run`. The landing hero/tiles/listings all read the visitor-scoped list — another account's numbers never headline someone else's page.
- **Reason:** Mission requirement (accounts + per-user projects) plus a real privacy defect: any visitor could browse every other visitor's submissions, and the landing hero leaked the newest run's figures to everyone.
- **Alternatives considered:** (a) fastapi-users — rejected: maintenance mode + a user/DB framework this file-based store does not need. (b) Only Google OAuth (no fallback) — rejected: the live box has no OAuth client today; the mission says implement the closest isolation and state the constraint. (c) Owner on the QuoteRecord — rejected: would move quote hashes for identical inputs and smuggle identity into the pricing path.
- **Consequences:** `authlib` + `itsdangerous` join the `[app]` extra; `deploy/.env.example` documents the four env vars (incl. the session-secret reset caveat); `tests/test_app_auth_scoping.py` pins isolation, the 404 rule, the shared demo, and the honest 503. Session secret unset ⇒ per-boot random ⇒ anonymous workspaces reset on restart (stated in the UI copy).

### D39 — Unviable sites get a first-class NO-VIABLE-CONFIGURATION verdict; the intake consumes the full transcribed bill and models profiles from observed constraints (the examples/real red-team fix set)

- **Date:** 2026-07-07
- **Author:** Olly (red-team goal session; derivation and proof in `docs/redteam/`)
- **Decision:** Five coordinated fixes, found by tracing the `examples/real` (Robert Thomas Farms) E2E run stage-by-stage against a hand-derived expected result (`docs/redteam/EXPECTED_RESULT.md`): **(1) Verdict.** A sizing run whose candidates all evaluate economically unviable (no positive project IRR or levered return anywhere) returns `selection_status = no_viable_configuration` on the `OptimisationResult` (additive enum field; `selected` on every recommended result) with NO selected spec — the engine's indicative fallback is gated on the new `candidate_viable` policy (`sizing/objective.py`), so the rank-key's smaller-system tie-break can never again manufacture a "recommendation" out of an all-unviable pool (the pre-fix run presented the smallest grid corner, 3.98 kW × 3.09 h, as the optimum). The verdict is a COMPLETE result: the app saves it, the results page, memo, proposal and report present it as a first-class do-not-proceed statement with the preserved surface as evidence. **(2) Full bill intake.** `TariffStack` gains `capacity_charge_gbp_per_kva_day` (the UK C&I availability charge, billed on ASC); the intake forms and `_site_from_form` carry `levies` (e.g. the CCL line) and the capacity charge; the customer bill carries `standing_gbp`/`capacity_gbp` as battery-invariant fixed lines on BOTH sides (savings unchanged; totals become a real bill). **(3) Constrained profile modelling.** With observed shape facts on the intake (day/night volume split from bill lines, peak timing, near-zero-midday), the modelled profile is built from those constraints (`generate_constrained_hh_csv`: annual energy, band split and stated peak hit EXACTLY, deterministic disclosed placement) instead of a business archetype; the archetype path's silent peak miss (a 421 kW site modelled at 79.6 kW by the clamped reshape) now warns loudly on every synthetic path; `/run/real` synthesises exactly like `/api/run` (it previously degraded to T0). **(4) Price counterfactual.** `PriceRecommendation` gains `counterfactual_import_gbp_per_kwh`/`exceeds_counterfactual`: a price-to-target above the customer's current blended import cost is warned as not-commercially-viable and rendered as a viability statement, never an offer (pre-fix: a bare £1.06/kWh "recommendation" on a ~£0.21/kWh site). **(5) Proof.** `tests/test_e2e_examples_real.py` runs the whole pipeline from `examples/real` alone, twice, asserting AC-1..AC-9 (verdict, exact profile constraints, full tariff stack, analytic arbitrage ceiling £1,772, no phantom finance, ranked asks, determinism, artifact presentation); runbook in `docs/redteam/RUNBOOK.md`.
- **Reason:** The E2E completed but returned the wrong result on every axis that matters to an underwriter: real invoice lines (CCL, capacity) never reached the model, the load model contradicted the documented observations, and — decisive — an all-unviable surface degenerated the ranking to its tie-break and shipped a micro-battery spec plus an absurd PPA price with the truth buried in warnings. An underwriting platform's most valuable output on a bad site is a fast, evidence-complete NO.
- **Alternatives considered:** (a) Treating negative-but-computable IRRs as selectable "least-bad" indicative picks — rejected: recommending capital destruction is not an underwriting result; the surface stays available as evidence. (b) Parsing the markdown fact files directly in the pipeline — rejected: prose parsing on the pricing path violates the deterministic-core rule; the prefill JSON is the audited machine-readable transcription (field provenance documented inside it). (c) A separate "site screening" pre-pass instead of the verdict — rejected: two verdict sources; the sizing search already computes exactly the needed evidence.
- **Consequences:** `SelectionStatus` exported from `gridq.schemas`; per-candidate results keep `selection_status=None`. The demo golden re-pins once for the bill's new standing line (both sides, +£675.71, savings identical — verified). `docs/redteam/` holds the derivation, runbook, OSS evaluations and (forthcoming) assumption register/audit. Follow-up recorded: a no-viable-verdict quote could carry a before-side-only customer bill for context (schema requires both sides today).

### D38 — Peak-aware placement tie-break (`_EPS_PEAK` + `_EPS_AGE`): the LP's placement ties break toward the firming deliverable — supersedes D37 before it ever landed

- **Date:** 2026-07-07
- **Author:** Olly
- **Decision:** The dispatch LP's placement ties (which equal-price half-hours to use) break toward **lower residual peak net import**, not toward SoE-holding. Alongside the D18 throughput regulariser (`_EPS = 1e-7`) the weights-off objective gains: (a) `− _EPS_PEAK · peak_import` (`1e-6`) where `peak_import` is an epigraph variable ≥ `load + charge − discharge` at every t — the exact quantity `customer_bill.peak_import_after_kw` reports, so the tie-break protects the peak-shaving / firming deliverable (C029) by construction; (b) `− _EPS_AGE · Σ (t/n)·discharge_t·Δt` (`1e-9`), a strictly-subordinate discharge-only earliness lean keeping the calendar-ageing direction without any charge-side pressure. Both are epsilon-scale tie-breaks, **not prices**: no reported number reads the LP objective, and verified field-by-field on the demo that not one £ figure moves.
- **Reason:** D37's SoE-holding lean charged as late as economics allow — which on the demo pushed charging into the morning load ramp and **raised** the residual peak import (315.4 kW on the dev machine vs 288.14 under D38) — worse firming, and £-costly the day a demand charge is priced. The peak epigraph instead *levels* tied placement: on the G9 fixture it spreads the red-band discharge evenly across all six half-hours (200/3 kW), cutting the residual peak from 320 to 253.3 kW at identical £.
- **Alternatives considered:** (a) Keeping `_EPS_SOE` and adding a peak term on top — rejected: the two fight (charge-late raises the peak) and the SoE lean has no £ or deliverable rationale of its own. (b) A £-priced demand charge — the right long-term shape, but no sourced £/kW value exists in the library yet; when one lands the peak becomes real objective £ and the tie-break naturally subordinates. (c) A larger `_EPS_AGE` that visibly orders every discharge half-hour — rejected: adjacent-slot coefficient deltas would still sit below HiGHS working tolerances until the term is big enough to fight the peak term.
- **Consequences:** Placement the peak *binds* is unique by construction (every G9 weekday red band levels identically; the demo's binding charge nights level under the global cap). **Placement the peak does not bind — weekend day-rate discharge, sub-peak night charge — remains solver-chosen:** deterministic on a machine, but not pinned across platforms; `_EPS_AGE` breaks those ties in exact arithmetic but is deliberately below solver tolerance in practice. Consequence discovered during verification: the pre-D38 golden pin was minted on a Linux container and did **not** reproduce on macOS at its own commit (a different degenerate vertex per platform) — the golden re-pin (`afabcd53…`) is anchored to the dev machine that minted it, with the £ content verified unchanged. G9 rewritten to pin the levelled red-band spread + exact residual peak + closed-form £; the tight-MIC binding test drops 250 → 150 kW (at 250 the levelled charge honestly never touches the cap). A £-priced calendar-ageing cost stays deferred to M7. Refines D18/D22; supersedes D37.

### D37 — SUPERSEDED BY D38 BEFORE LANDING — second objective tie-break (`_EPS_SOE`): SoE-holding

> **Status note (2026-07-07):** this decision was reversed the same day, before its code ever landed in a commit — verification found the charge-late lean *raised* the residual peak import (worse firming). The entry is kept for the record; the "totals unchanged" claim below was wrong in one respect (no £ moved, but `peak_import_after_kw` did), and the golden re-pin described here never happened. See D38.

### D36 — Finance charges each physical cost once: levies on the NET import increase (round-trip losses), degradation via the revenue decay alone — resolves F-012

- **Date:** 2026-07-03
- **Author:** Olly (goal loop 03, prompted by Olly's challenge on the demo's negative economics; M5 composition change — flagged to Oli via C032)
- **Decision:** Two corrections to the cashflow cost stack. **(1) Levies:** `levies_on_charge` prices the NET import increase, ``levy × max(0, charge − discharge)`` — the round-trip losses — because on the no-export floor every discharged kWh displaces levied site import. This is the same symmetric net-import convention the dispatch objective (``price × (discharge − charge)``) and the customer bill (``net import = load + charge − discharge``) already use; charging levies on gross charge was internally inconsistent with both. Either volume derives from the other via the RTE convention when only one is known. **(2) Degradation:** the annual capacity fade is charged exactly ONCE — as the revenue decay the P50/P90 series apply — with the warranty throughput cap as the hard cycling limit. The ``degradation_warranty_proxy`` (fade% × capex replacement provision) and ``degradation_marginal_cost`` (£/MWh wear, D33) cost lines are REMOVED: under the model's stated no-augmentation convention no replacement spend ever occurs, so a provision line double-charged the fade the revenue decay already prices, and the fade key is explicitly a calendar+cycle proxy, so the per-MWh wear line triple-charged cycling. D33's "previously omitted entirely" premise was factually wrong (the proxy line existed) — self-corrected. The library keys stay as unconsumed homes pending M7's calibrated SoH model.
- **Reason:** F-012: at the demo optimum the double-counts summed to ~£8.3k/yr (levies £5,180 gross vs £622 net; proxy £3,090; marginal £691), turning a genuinely viable project (project IRR +9.8%, levered +10.6%, P90 DSCR 2.23 ≥ the 1.30 covenant) into a phantom-negative one (−3.3%, DSCR 0.14). A cost model that is inconsistent with its own dispatch objective and bill convention is wrong in whichever direction it errs; conservatism does not excuse incoherence, and a lender diligencing the stack would find exactly this.
- **Alternatives considered:** (a) Keeping the proxy line and removing only the revenue decay — rejected: the decay is the physically-truthful representation (capacity genuinely falls; the revenue genuinely follows). (b) Keeping the per-MWh wear line for merchant cycling above warranty — rejected: the throughput cap hard-limits cycling to warranty terms, so no above-warranty cycling exists to price. (c) A netting flag on the levy line — rejected: there is one correct convention, not a configuration choice.
- **Consequences:** Demo-scale economics turn honestly positive and covenant-feasible (the D34 bake-off re-ran on the corrected surface per D29's re-run rule — numbers in the D34 addendum); goldens + the app-demo covenant pins re-based deliberately. `COST_LINE_KEYS` drops the two degradation keys (outputs render whatever lines exist — no template change). Cross-refs: F-012 (FIXED), C032 (Oli's M5 review note), D33 (its cost-line half superseded; its gearing/sculpting halves stand).

### D34 — The engine-default sizing search swaps to the two-stage continuous search (C025 resolved); the dense exact grid stays the certification instrument, never the per-request path

- **Date:** 2026-07-03
- **Author:** Olly (goal loop 03; the follow-up decision north-star D29 reserved for after C025)
- **Decision:** With the capex energy component live (gb_v1_3, C025 resolved), `MilpEngine` and `DemoEngine` size via the **two-stage continuous search by default** (`use_two_stage_search=True`; the legacy 3x3 stays the honest fallback for non-MILP solvers / no-metered-series runs, and remains available via the flag). The **dense exact grid keeps its D29 role as the certification instrument** — re-runnable in the bake-off harness whenever the objective/constraints/finance metric change — but is NOT the per-request surface (230s+ per run on the deploy box vs 65s two-stage); adaptive-zoom and PSO stay bake-off baselines. The persisted per-run surface is the two-stage stage-labelled cloud (`m4-two-stage-v2`), which the WS4 UI already consumes confirm-first.
- **Reason:** The D29-mandated bake-off, re-run on the NON-degenerate surface (gb_v1_3 capex shape + D35 conservative caps + injected illustrative DebtTerms, demo site full metered year — the only full HH year on this box; a customer-site re-run is recorded as follow-up): dense 12x12 reference = 67 evals / 230s, best −4.02% project IRR @ 184 kW × 2.97 h; adaptive = 92 evals / 262s, −3.92%; PSO = 84 evals / 279s, −3.80%; **two-stage = 281 evals / 65s, confirmed best −3.27% @ 179 kW × 552 kWh (3.08 h)** — the best optimum found, in ~4x less wall time, with 3-4x the coverage, and every reportable number full-series confirmed (never a proxy). All four methods converge on the same optimum region (~170–200 kW, ~3 h), which certifies the two-stage refinement against the exact-grid reference in the direction D29's certification story requires. NOTE the honest headline: on gb_v1_3 every honesty line is live (perfect-foresight haircut, parasitic + wear cost lines, levies-on-charge, real energy capex), and the demo-scale site prices NEGATIVE project returns with NO covenant-feasible size — the platform reports that honestly (indicative-only selection, not bankable) rather than flattering it; the 23.5%-era bake-off numbers were artifacts of the per-MW-only capex shape this decision removes.
- **Alternatives considered:** (a) Dense grid as the per-run surface (D29's original sketch) — rejected: 4-5 minutes per request on the deploy box is operationally unusable, and its coarse-lattice optimum measured WORSE than the two-stage confirm on the real surface. (b) Adaptive-zoom as the default — rejected: slower than two-stage at lower coverage here; stays the certification refiner. (c) Waiting for a customer-site bake-off before swapping — rejected: the C025 gate's condition (a real energy-cost trade-off) is met, the app degrades honestly regardless, and the customer-site re-run is a recorded follow-up, not a blocker.
- **Addendum (2026-07-03, post-D36):** the bake-off re-ran on the D36-corrected cost stack per D29's finance-change rule — every method now finds a covenant-FEASIBLE optimum on the levered metric, converging on the same region: dense 12x12 67 evals/145s → 9.97% @ 184 kW × 2.97 h; adaptive 81/161s → **10.52%** @ 167 kW × 3.11 h; PSO 120/260s → 10.28%; two-stage 149 evals/**36 s** → 9.94% @ 174 kW × 3.00 h (covenant-passing full-series-confirmed selection; the search stops early once confirmed-feasible, hence fewer evals). Honest delta: the adaptive refiner's best levered point sits 0.59 pp above the two-stage selection on this surface; the default stays two-stage (4.5× faster with the confirm guarantee — the operational trade D34 makes), and widening the confirm budget to close the gap is a recorded follow-up. **Follow-up DONE (same day):** the confirm stage now takes a DIVERSE top-10 (near-clones skipped), confirms ALL of them (the first-feasible-wins early stop removed — the best feasible confirmed candidate wins), and a bounded full-series compass polish (±5%/axis, halving, ≤10 probes) recovers the peak the proxy misranks: demo selection improved 9.94% → **10.35%** @ 165.3 kW × 3.15 h in 73.5 s total — 0.18 pp from the adaptive reference at ~2.2× less wall time.
- **Consequences:** `gridq run` / the app persist the stage-labelled two-stage cloud by default; the app's assumption default moves to gb_v1_3 in the same change (the C025 shape must be live for the recommendation to mean anything); goldens + grid pins re-based deliberately (release + search + D35 all move them at once, reasons in the test comments). The bake-off harness (scratchpad script, results in this entry) re-runs at every objective/constraint/finance change per D29. Follow-ups: a customer-site bake-off when a real full-year HH site lands; the W6-3..W6-8 search-stack findings stay tracked in the adoption review appendix.

### D35 — Duration-keyed lookups take the conservative NEIGHBOUR: ceiling on decreasing tables (warranty cycles), floor stays on increasing tables (CM de-rating) — resolves F-002/C030

- **Date:** 2026-07-03
- **Author:** Olly (goal loop 03; amends the adopted north-star D28 in the direction its own reasoning implies)
- **Decision:** Between tabulated duration nodes, a duration-keyed assumption lookup takes the **conservative bracketing node**: the CEILING node for a table that DECREASES with duration (`warranty_cycle_limits.max_cycles_per_year_by_duration_h` — a 3 h candidate gets the 4 h point's 250 cycles, via the new `ceiling_value_for_duration`, applied in `milp_solver` + `mpc_solver`), and the FLOOR node for a table that INCREASES with duration (`capacity_market.de_rating_factor_by_duration_h` — unchanged `value_for_duration` in valuation + gb_streams). On tabulated nodes both lookups return the node exactly (existing on-node results unchanged). Above the largest node the largest node's value is used WITH a needs_review warning (likely optimistic on a decreasing table — no honest bracketing point exists until sourced warranty terms land).
- **Reason:** North-star D28 kept flooring as "conservative in the honest direction", reasoning from the CM table — but flooring a DECREASING table RELAXES the cap between nodes (365 cycles at 3 h = 1095 kWh/kW-yr of monetisable throughput vs 730/1000 at the bracketing contract points), and a wide continuous search preferentially selects just-below-key durations to harvest exactly that slack (F-002; independently confirmed against the north-star branch's own SizingDomain in the adoption review, W6-1). The conservative-neighbour rule keeps D28's no-interpolation stance — no intermediate value is ever invented — while making the direction conservative on BOTH table shapes. With the wide search going live (D34), off-node durations become the norm, so the direction had to be fixed first.
- **Alternatives considered:** (a) Linear interpolation (the withdrawn goal-session fix) — rejected again: manufactures precision between placeholder points (D28's objection stands). (b) Step-quantising search domains to tabulated keys — rejected: defeats the floating C-rate that is the point of the continuous search, and does nothing for user-specified off-node dispatch. (c) Leaving flooring with a warning — rejected: a warned anti-conservative cap is still an anti-conservative cap feeding FIRM revenue.
- **Consequences:** Off-node candidates cycle less and earn less than under flooring — strictly conservative; on-node results (incl. the demo's 4 h selection and every golden) are unchanged. `tests/test_goal_invariants_dispatch.py` pins the direction on both tables + the ceiling cap binding in a real off-node solve. F-002 closes FIXED; C030 resolves. The future sourced warranty schedule supersedes the table (and may re-open interpolation as a content decision behind the X.1 door, exactly as D28 anticipated).

### D32 — M4 sizing rework: a two-stage deterministic continuous search (coarse sweep + seeded refinement + full-series confirmation), OPT-IN behind the north-star C025 capex gate (engine default stays the legacy 3x3 grid); the full evaluated cloud persists as the sizing surface

- **Date:** 2026-07-02
- **Author:** Olly (goal session, WS2)
- **Decision:** `MilpEngine(use_two_stage_search=True)` sizes via `gridq.optimisation.two_stage.two_stage_sizing` — **OPT-IN, never the default**, per the north-star C025 gate (per-MW-only capex pins any wide-duration search to the band edge; north-star D29 gates the engine-default swap on the capex shape gaining an energy component plus a re-run bake-off). The default engine path and the customer-visible demo stay on the legacy 3x3 enumeration. The search: **(A) coarse sweep** — 10 labelled structural power fractions (5–150% of the site's binding capacity) x 8 durations (0.5–8 h), scored against a **typical-period proxy**: one medoid day per (month x weekday/weekend) stratum of the site's own metered series, weighted by stratum size (`gridq.sizing.typical_periods`; only complete gap-free days qualify; nothing invented). **(B) seeded refinement** — an in-house deterministic differential-evolution loop (`gridq.sizing.refine`, seeded rng, memoised, rounded candidates, ~200-evaluation budget) over the same continuous box, ranked by the existing `SizingObjective` (covenant gate first, then levered return / project IRR — policy unchanged and unmoved). **(C) confirmation** — the top-ranked candidates (up to 5) re-solve on the **full metered series**; only confirmed evaluations are selectable or reportable, and `MilpEngine._choose`/`DemoEngine._choose` exclude proxy evaluations from the indicative fallback. Weighted proxy dispatch runs through the same `_solve_milp` with an `interval_weights` array folded into the objective, annualisation and warranty cap (`None` = byte-identical unweighted model) and through weighted `HalfHourlyDispatchSchedule` aggregates, so valuation + finance consume proxy solves unchanged; every proxy result carries a loud provenance warning. The whole evaluated cloud (coarse + refine + confirm, stage- and proxy-labelled) persists via `SizingRun.sizing_grid()` onto the `QuoteRecord` (`SizingGridPoint.stage/proxy`, `SizingGrid.search_version = "m4-two-stage-v2"`; additive fields, hash-excluded as before per D23). Duration-keyed assumption tables keep the conservative-flooring `value_for_duration` lookup per the adopted **north-star D28** (interpolation deferred to sourced assumption content behind the X.1 door); the originally-built warranty interpolation policy was withdrawn in adoption, and the anti-conservative direction flooring takes on the DECREASING warranty table stays flagged as F-002 (open) for the joint interpolation-policy decision. One CSV read (one confidential-access log entry) serves the whole search via `PreloadedDispatchSolver`. The search space fractions/durations/bounds and confirm count are labelled structural constants (inject no £; power stays a fraction of the site's own capacity). The legacy 3x3 enumeration remains the honest fallback when no MILP solver or no readable series is available (T0 degrade unchanged).
- **Reason:** The 3x3 grid was too sparse to be a sizing *optimiser* or a continuous surface; the goal spec requires a wide covenant-gated search converging on one sizing with the evaluated cloud persisted. The typical-period proxy makes a ~300-evaluation search affordable (~0.15 s per proxy solve vs ~2.5 s full-year) while the confirm stage guarantees no reported number ever comes from an approximation.
- **Alternatives considered:** (a) full-year solves for every candidate — ~15–25 min per run on the deploy box, rejected; (b) scipy differential_evolution — rejected for transparency/determinism control and to avoid a dependency on its internal RNG semantics; (c) k-means day clustering — rejected for seed sensitivity; calendar-stratified medoids are deterministic and explainable; (d) synthesising a compressed year — rejected outright (fabricated data).
- **Consequences:** An opted-in run on a readable series produces a ~200–300-point continuous cloud (stage-labelled) instead of 9 points, at ~1 minute on the deploy box (5 full-year confirm solves + proxy sweep); the default engine/demo output is unchanged from the legacy grid. The demo golden stays on the legacy-grid baseline (one deliberate re-pin remains, from the F-008 model_version stamping fix, recorded in `tests/test_determinism_golden.py`; the transient two-stage-default pin was withdrawn with the C025 gate). Determinism hardened by test: `tests/test_two_stage_sizing.py` runs the search twice (direct invocation) and asserts identical fingerprints (the goal's M4 exit criterion), plus staged-provenance, never-proxy-selection, covenant-gate and grid-projection invariants; component tests in `tests/test_sizing_search_components.py`. Post-C025 the two-stage search should enter the north-star Wave-6 bake-off (dense exact grid + adaptive zoom + PSO) as a fourth method on the non-degenerate surface, where the typical-day proxy's ~15x per-eval speedup is its case. UI note: the surface consumer should prefer `stage == "confirm"` values where a (power, duration) appears in both proxy and confirmed form.

### D33 — Vacuous finance capabilities implemented behind a new versioned release (gb_v1_2): placeholder gearing makes the P90-DSCR covenant gate fireable in real mode, sculpting becomes real with an ANNUITY default, and degradation marginal cost is a release-gated cost line

- **Date:** 2026-07-02
- **Author:** Olly (goal session, WS1 vacuous-logic implementation)
- **Decision:** Three deferred capabilities that left finance logic vacuous are implemented behind versioned assumptions, per the goal directive, in a new release `assumptions/gb_v1_2.yaml` (gb_v1_0 copied forward unchanged; gb_v0_1/gb_v1_0 stay frozen). (1) **Gearing:** `financing.debt_gearing_pct` = 60.0 as a clearly labelled illustrative placeholder (status placeholder, confidence low, source names Sim's pending portfolio-finance work, B001), plus the D19 fold-in of `financing.credit_rate_tenor_policy` from gb_v0_1 — so in real (non-injected-debt) mode `build_debt_terms_from_credit` assembles full `DebtTerms` on gb_v1_2, DSCR is available, and the `SizingObjective.feasible` covenant gate genuinely admits/rejects candidates. (2) **Sculpting:** `DebtTerms.sculpting_basis` default flips from the decorative `"P90_DSCR"` to `"ANNUITY"` — the honest name for what the schedule always was — and `"P90_DSCR"` now does what it says: `sculpted_schedule` solves the flat target DSCR `x` with PV(P90 CFADS ÷ x, debt rate, tenor) = principal and sets each year's service to CFADS ÷ x (deterministic closed form; zero-rate needs no special case). When any P90 CFADS year within the tenor is non-positive, or the horizon is shorter than the tenor, sculpting is unavailable and the schedule falls back to the annuity with a warning; `DscrResult`/`DebtAssumptions` record the basis **actually applied**, never a sculpt that did not happen. (3) **Degradation marginal cost:** `battery_opex.degradation_marginal_cost_gbp_per_mwh` (illustrative placeholder) prices a new release-gated cost line = rate × annual discharge MWh via the existing `DispatchEnergy` hook (charge-only volume derives discharge as charge × RTE, warned; negative rates rejected); an absent key (all older releases) leaves behaviour byte-identical. Additionally a negative P90 revenue haircut is clamped to zero with a warning so P90 can never exceed P50.
- **Reason:** The DSCR covenant gate — the bankable constraint the sizing objective is built around — could never fire in real mode because no release carried gearing (the spec audit's own note on INV-M4-11: "the v1 library alone can never fire it"), and `sculpting_basis="P90_DSCR"` recorded an intent nothing implemented (doc drift the manual itself flagged). Implementing behind a versioned release keeps every number in the library (no private constants), keeps the frozen releases frozen, and keeps the not-bankable safety property: the gate can fire, but placeholder-driven output can never present as bankable.
- **Alternatives considered:** (a) Waiting for Sim's confirmed gearing — rejected: the gate and its tests stay unfalsifiable indefinitely; a labelled placeholder follows the library's own convention (the 1.30 covenant and the credit policy are already illustrative placeholders). (b) Keeping `"P90_DSCR"` as the DebtTerms default and implementing sculpting under it — rejected: every existing construction (credit path, tests, demo debt) would silently change schedule shape; flipping the default to ANNUITY preserves byte-identical behaviour everywhere no basis is named. (c) An iterative sculpt with per-year gearing/service caps — rejected: the flat-target closed form is deterministic, dependency-free and sufficient for the v1 record. (d) Pricing degradation wear as a dispatch objective term — rejected: D22(3) deliberately omits it from the MILP (no lifetime-throughput price exists); a finance cost line behind a versioned key is the honest, release-gated home. (e) A zero-value marginal-cost placeholder — rejected: understates cost while claiming the capability exists; a labelled illustrative value follows the covenant/opex precedent.
- **Consequences:** `assumptions/gb_v1_2.yaml` (30 entries; 0 confirmed) is the latest release; runs pinned to gb_v0_1/gb_v1_0 are byte-identical to before. `DscrResult` gains a `sculpting_basis` field (the applied basis); `FinancialCase.debt_assumptions.sculpting_basis` now reports what actually happened (`None` when no schedule was built). New invariant suite `tests/test_goal_invariants_finance.py` (22 tests: P90 ≤ P50 with degradation compounding, negative-haircut clamp, sculpt PV identity + flat DSCR + annuity fallback + ANNUITY default, real-mode gb_v1_2 gate firing while staying not bankable, release-gated marginal-cost line). Manual ch. 12 and `docs/financial_model.md` amended (the "sculpting not done yet" note rewritten; the spec extract's INV-M5-12 is superseded for gb_v1_2-era code). Findings F-005/F-006 recorded FIXED in `docs/goal/FINDINGS.md`.

### D31 — Dispatch MILP segments state of energy at metered-series gaps (never chained across missing intervals)

- **Date:** 2026-07-02
- **Author:** Olly (goal session, WS1 invariant verification)
- **Decision:** The half-hourly dispatch MILP (`MilpDispatchSolver._solve_milp`) treats any timestamp discontinuity in the metered series — missing interval(s), a duplicated stamp, or an irregular step — as a segment boundary. The SoE recurrence `soe_t = soe_{t-1} + charge_t·RTE·Δt − discharge_t·Δt` applies only within a contiguous segment, and each segment carries its own cyclic wrap (SoE at segment start = SoE at segment end), implemented as a predecessor permutation over the time index (`pred[t] = t−1` inside a segment, the segment's end at its start). Unordered rows are stably sorted by timestamp first (with a warning). In the MPC rolling-horizon mode the first segment is anchored to `initial_soe_kwh` instead of wrapped; later in-window segments keep the conservative wrap. Segmentation is reported via a solver warning so gappy inputs are visible in the run record.
- **Reason:** Chaining SoE across gaps lets the optimiser charge cheaply just before a data hole and discharge dearly just after it — value manufactured from unmetered intervals (demonstrated: a gapped series whose contiguous blocks each sit in one flat tariff band yielded ~45 MWh/yr of phantom discharge; the segmented model correctly does nothing). A per-segment cyclic wrap is the same no-free-energy convention the global constraint already encoded.
- **Alternatives considered:** (a) carrying SoE across gaps with elapsed-time self-discharge — rejected: the intervening load/price are unknown, so any carried-charge value is unbankable; (b) dropping segments shorter than a day — rejected: discards real metered data; (c) rejecting gappy files outright — rejected: honest degradation is the house pattern, and ingestion already counts (never fills) gaps.
- **Consequences:** Gapless series produce byte-identical results (pinned golden hash unchanged; the permutation equals `roll(t=1)` when no gaps). Gappy series now price only intra-segment opportunity — strictly more conservative. New invariant test `tests/test_goal_invariants_dispatch.py::test_soe_never_chains_across_gaps`; manual ch. 9 and `docs/dispatch_engine.md` updated to state the invariant (both were silent on gap behaviour).

<!-- D27-D30 below were decided on karim/north-star-drive (adopted as authoritative 2026-07-02, Olly's direction); imported verbatim so this lane's numbering is coherent pre-merge. Olly's goal-session decisions were renumbered D31-D33 to make way. -->

### D30 — Assumption-schema evolution: the 23 sections stay required forever; new sections join as OPTIONAL fields defaulting to empty (frozen releases load unchanged)

- **Date:** 2026-07-02
- **Author:** Karim (`agent/sizing-search`, /loop iteration 11 — north_star Wave 8, first slice)
- **Decision:** `AssumptionLibrary` grows by **optional sections**: the 23 names in `SECTION_NAMES` remain required forever (removing or renaming one is a breaking event, guarded by test); new sections are declared in `OPTIONAL_SECTION_NAMES` as explicit model fields with `default_factory=dict` — an empty section means "this release predates the section", **never "zero"**. A mistyped section stays a hard error (`extra='forbid'` intact). First three optional sections declared (empty homes, no values invented): `availability` (availability-payment prices, availability/outage factors), `penalties` (CM non-delivery regime, flex clawbacks/LDs), `tnuos` (the un-firmable demand residual). Content lands in **gb_v1_1** via the X.1 door with sources, not here.
- **Reason:** Wave 8's firming risk shapes need library homes, but `SECTION_NAMES` makes every section a required typed field — adding one as required would invalidate the frozen `gb_v0_1`/`gb_v1_0` releases and every pinned fixture. Optional-with-empty-default is the additive path: frozen releases load byte-identically (test-proven), the loader/`get()` door is unchanged, and the 8-field Assumption discipline applies to any populated entry.
- **Alternatives considered:** Per-version section-set registry (rejected — machinery without benefit at two releases; revisit if a second market's section set diverges). Making the new sections required and re-cutting the frozen releases (rejected — frozen means frozen; re-cutting rewrites provenance history). A free-form `dict[str, dict[str, Assumption]]` growth field (rejected — loses the explicit-field typo protection that caught real errors before).
- **Consequences:** `gb_v1_1` can carry the firming/penalty/TNUoS content the Wave-8 finance shapes read; consumers must treat an empty optional section as absence (data-ask), never zero; `OPTIONAL_SECTION_NAMES` is the single registry of growth sections and `test_assumptions_evolution.py` guards both lists.

### D29 — Sizing production method (issue 016 stage 9): dense exact grid is the persisted reference surface, the adaptive-zoom refiner pins the optimum, PSO stays a bake-off baseline; the engine-default swap is gated on C025

- **Date:** 2026-07-02
- **Author:** Karim (`agent/sizing-search`, /loop iteration 4)
- **Decision:** The production shape of the wider sizing search is: **(1)** the **dense exact grid** (`dense_grid_search`) is the reference surface — it is what gets persisted via `search_result_to_sizing_grid` for the 3D chart (complete, exact, no sampling method between the LP and the surface); **(2)** the **adaptive-zoom refiner** (`adaptive_refine_search`) is the optimum-pinning method (deterministic, step-aware by construction — pure sampling + explicit duration-key probes, no surrogate — and feasibility-first); **(3)** **PSO** (`pso_search`) is retained as the bake-off comparison baseline only, never a production path. **(4)** **The engine default does NOT swap** from the 3×3 grid to the wider search yet: the swap is **gated on C025** (per-MW-only capex makes every wide search pin duration to the band edge) **plus a re-run bake-off on a non-degenerate surface** after C025 resolves.
- **Reason:** Bake-off on the demo site (real `MilpDispatchSolver`, full metered year): dense 12×12 reference = 67 evals, 23.48% project-IRR @ 162.7 kW × 7.79 h; adaptive = 23.50% (+0.084% vs ref, ≈zero information loss), within 0.5% of the reference at 41 evals; PSO = 23.50% (+0.088%), within 0.5% at 24 evals. PSO's eval-efficiency win is discounted: the demo surface is corner-pinned by the C025 capex artifact — an easy, monotone surface that flatters swarm concentration; the zoom refiner's determinism, simplicity and step-awareness are the properties the certification story rests on, and on the sharply-peaked stub surface it landed nearer the true peak. The stub-surface certification tests (`test_sizing_search.py`) hold both methods to the dense reference permanently.
- **Alternatives considered:** PSO as the production refiner (rejected for now — its advantage was measured on a degenerate surface; revisit at the post-C025 re-run). Swapping `MilpEngine._default_search_space` to the wider search immediately (rejected — C025 means every quote would recommend band-edge duration on placeholder capex; honesty first). Persisting the adaptive trajectory instead of the dense grid as the surface (rejected — the chart should show the exact surface, not a search's sampling pattern).
- **Consequences:** Issue 016 stages 1–9 are all built and green; the wider search is fully available behind `gridq.sizing` (domain → evaluator → parallel harness → dense/adaptive/PSO → `SizingGrid` projection) with the engine default unchanged. Follow-ups: resolve C025 (energy £/kWh capex component in the next assumption release), re-run this bake-off on a real non-demo site + injected DebtTerms, then take the engine-swap decision as its own D-entry.

### D28 — Duration-keyed assumptions keep D22's conservative flooring on the continuous sizing domain; the adaptive search must be step-aware; interpolation is deferred to sourced assumption content

- **Date:** 2026-07-01
- **Author:** Karim (Wave 6 / issue 016 scope addition; engine-lane semantics unchanged)
- **Decision:** The duration-keyed assumption tables (`warranty_cycle_limits.max_cycles_per_year_by_duration_h`, `capacity_market.de_rating_factor_by_duration_h`, keyed at {1, 2, 4} h) **keep the existing conservative-flooring lookup** (`value_for_duration`, per D22) when evaluated at the continuous durations the `SizingDomain` (issue 016) produces. Consequences accepted and required: the objective/feasibility surface is **piecewise-constant with steps at the key boundaries**, so (a) the adaptive refiner must be **step-aware** — it never assumes smoothness across a duration-key boundary, and the dense exact-grid reference (which shares the same evaluator, hence the same steps) remains the certification instrument; (b) no solved result changes and no golden fingerprints are re-baselined. **Interpolation is deferred**: it becomes a question for a future assumption *release* (interpolated tables or an explicit interpolation-policy key) once the sourced per-auction NESO/EMR de-rating series lands (north_star Waves 8/10 sourcing) — a content decision behind the X.1 door, not a code default.
- **Reason:** Flooring is already the recorded engine semantics (D22) and is conservative in the honest direction at off-key durations (a 2.7 h candidate is valued at the 2 h CM de-rating — it under-states contracted revenue rather than flattering it). Switching to interpolation now would change every solved dispatch/valuation, invalidate the determinism goldens mid-lane, and would interpolate between *placeholder* numbers — manufacturing precision from unverified content.
- **Alternatives considered:** Linear interpolation between the {1,2,4} h keys (rejected now — changes solved results, re-baselines goldens, and interpolates placeholders; revisit as sourced content). Restricting the continuous domain to the keyed durations (rejected — defeats the floating C-rate that is the point of issue 016).
- **Consequences:** Issue 016 stages 5–6 treat feasibility/objective as step-discontinuous across duration keys; the stage-9 bake-off certifies the adaptive method against a reference surface that carries the same (documented) steps; a future gb_vX release may supersede this with an interpolation policy, re-baselining goldens in that same change.

### D27 — The North Star (docs/north_star.md) is adopted as the goal + reconciliation reference; Waves 6–11 are the build sequence

- **Date:** 2026-07-01
- **Author:** Karim (in-session directive; `GRIDQ_CONTRIBUTOR` unset on this machine)
- **Decision:** [docs/north_star.md](north_star.md) — the two-sided firming goal (firm the customer / firm the grid / firm the lender), the 8-criterion world-class definition (test-enforced W1–W5, W7 vs evidence-based W6, W8), and the Waves 6–11 roadmap — is **adopted**. It joins the active plan as a session-start reconciliation reference; it changes only by a further D-entry. The §1 "whose engine" working assumption (GridQ underwrites Neura's own deployments first; industry trust = counterparties transacting on its numbers, not a SaaS play) **stands unless Karim overrides it**. Wave-level implementation detail lives in issue files, not in the north star.
- **Reason:** Karim set the goal ("operate a BTM battery with peak shave/arbitrage + multi-market stacking, critically also as a firming solution against a flexible grid; iterate to world-class") and directed execution ("make a list and drive through the goal"). The document was drafted from a 13-agent deep review ([docs/reviews/2026-07-01-deep-review.md](reviews/2026-07-01-deep-review.md)) and hardened by a 4-lens adversarial critic panel (founder/engineering/honesty/fact-check) with every blocker/major fix applied before adoption.
- **Alternatives considered:** Keep it a standing draft (rejected — Karim directed execution; an unadopted reconciliation reference has no authority). Encode the goal only in CLAUDE.md (rejected — too coarse for an 11-wave roadmap with acceptance criteria). Adopt without the critic pass (rejected — the panel found real blockers, incl. a boundary-violating goal verb and a mathematically wrong reserved-power constraint).
- **Consequences:** Sessions reconcile against north_star + ACTIVE_PLAN; Wave 6 (issue 016 + its recorded scope additions) is the active lane; Wave 7 (firming substrate + bill headline) is next and contains the deliberate seam events; the immediate security action (enable `GRIDQ_BASIC_AUTH` on the live URL) is flagged outside the wave sequence and reserved for Olly/Karim as a live-host action.

### D26 — W5-3 public deployment: a reproducible host-agnostic Docker build (pinned solver closure + determinism env pins), an additive `/healthz` + a dormant Basic-Auth gate, an ephemeral-by-default run store; the live go-live actions are reserved for Olly

- **Date:** 2026-06-24
- **Author:** Olly (`agent/deploy`, W5-3)
- **Decision:** Ship the deployment layer for the Wave-5 app as **build + config + runbook**, not a live deploy. **(A) Reproducible build:** a `python:3.12-slim` [`Dockerfile`](../Dockerfile) installs a **fully-pinned** runtime+`[app]` closure ([`requirements.txt`](../requirements.txt), 62 pkgs, generated from the green env and **verified** by a clean-venv install + the app/demo-MILP test run) **wheels-only** (`--only-binary=:all:`; **no compiler/apt layer** — `highspy` statically bundles HiGHS), then installs gridq **editable, in its source-tree layout** (required: `assumptions/` and `examples/demo_site` resolve via `Path(__file__).parents[3]`, and the demo dir has no env override, so a flat site-packages install would break demo + assumption loading). **(B) Determinism in the container:** `TZ=UTC`, `LANG/LC_ALL=C.UTF-8`, and single-threaded numerics (`OMP/OPENBLAS/MKL/NUMEXPR_NUM_THREADS=1`, `POLARS_MAX_THREADS=1`) so the MILP (already `threads=1, seed=0` in code) stays byte-reproducible regardless of host CPU count/locale (extends D18/D22 to the host). **(C) Additive app code:** `GET /healthz`→`200 {"status":"ok"}` (cheap, unauthenticated host probe) and an **optional HTTP Basic gate dormant unless `GRIDQ_BASIC_AUTH="user:pass"` is set** (exempts `/healthz`+`/static`; `secrets.compare_digest`; no credential in the repo) — the default open posture and every existing test are unchanged. **(D) Privacy/retention:** the run store is `$GRIDQ_RUN_STORE` (image default `/data/runs`), **ephemeral by default** (privacy-aligned — real uploads do not persist across redeploys; a volume is documented for durability); only the labelled synthetic `examples/demo_site` ships; [`.dockerignore`](../.dockerignore) keeps `runs/`/`data/`/`.env`/`.claude/` out of the image. **(E) Host:** **Fly.io** is the worked primary ([`fly.toml`](../fly.toml)); Render/Railway/Cloud Run are documented; a [`Procfile`](../Procfile) covers buildpack hosts. **(F) Boundary:** creating accounts, accepting provider terms, registering/pointing domains, setting live secrets, and the final live deploy are **`TODO[Olly]`** in [`deployment.md`](deployment.md) — the agent stops at "ready to deploy".
- **Reason:** `wave5_scope §1` left public hosting open and the strict W5-3 rule reserves the live actions for a human. A pinned, wheels-only, editable image is the minimal-divergence way to make the **real** engine path (not just demo) publicly hostable while preserving the deterministic-reproduction contract; gating + ephemeral-by-default storage honour the §5 privacy rules for a URL that accepts third-party submissions.
- **Alternatives considered:** (1) **Non-editable/site-packages install** — rejected: breaks `assumptions/` + the demo path resolution (the demo dir has no env override). (2) **`pip freeze` the dev venv as the lock** — rejected: it ships the dev/agent toolchain (graphify/pytest/ruff/mypy/tree-sitter) and contaminates the image; the lock is the runtime+`[app]` closure only. (3) **Zero-backend static export** (`wave5_scope §1` fallback) — rejected as the product: it cannot offer the real-site interactive path. (4) **Auth always-on** — rejected: dormant-by-default keeps the default open demo + all tests unchanged while making gating a single env var.
- **Consequences:** the app is publicly hostable via one `fly deploy` once Olly completes the `TODO[Olly]` steps; determinism holds across hosts; real submissions are handled per §5 with a documented deletion path (`gridq.app.store.delete_run`) but **no automatic TTL/retention sweep yet** (follow-up before real submissions at scale). Surfaced + fixed a latent packaging defect: `pyproject.toml` was missing the `[project.optional-dependencies]` table header (the `app`/`dev` extras were bare `[project]` keys — invalid PEP 621, so a clean `pip install` failed, masked only because the dev venv predates strict tooling); header added, **no dependency change** (flagged **C024**).

### D25 — W5-2 visualisation layer: vendored offline Plotly supersedes D24's WebGL surface; a representative dispatch day is persisted additively (D23 pattern) so the dispatch chart is legible from saved runs

- **Date:** 2026-06-24
- **Author:** Olly (`agent/viz`, W5-2)
- **Decision:** W5-2 builds the visual analytics on top of W5-1 (D24), in two parts. **(A) Renderer — vendored Plotly, superseding D24(5).** The Inspect charts are re-rendered on a **vendored** `static/plotly.min.js` (pinned **v2.35.2**, no CDN): the **3D sizing surface** (`surface` with a feasibility `surfacecolor` band + `scatter3d` candidate markers + a distinct accent marker for the chosen optimum), the **half-hourly dispatch** (charge/discharge bars + state-of-energy line + DUoS-red-band shading), **P50/P90 cashflows** (grouped bars), **P90 DSCR vs covenant** (line + covenant rule), and the **value stack** (firm-vs-gated stacked bars). This **supersedes D24(5)'s "self-contained WebGL/canvas, no Plotly"** per the explicit W5-2 brief ("rendered interactively (Plotly)") and `wave5_scope.md §4`, which sanctions *"a vendored Plotly bundle **or** a small WebGL/canvas surface"* as equivalent. The repo's posture is preserved: Plotly is **vendored, never CDN-loaded** (so it renders offline / under a strict CSP; `/static` is auth-exempt), the charts read the **same JSON islands** (raw record values for geometry + pre-formatted label strings for every visible figure — Plotly positions and draws but formats no figure), the accessible server-rendered fallback tables remain, and the template arithmetic-free AST invariant is untouched (`charts.js` is JS, not scanned). **(B) Persisted dispatch profile (joint platform↔engine; mirrors D23).** A new serializable `gridq.schemas.dispatch_profile` — `DispatchProfile` + `DispatchProfilePoint` (`GridQModel`s) — projected by `HalfHourlyDispatchSchedule.representative_day_profile()`, exposed via a duck-typed `MilpEngine.last_dispatch_profile()` / `DemoEngine` hook (**mirrors `last_sizing_run()` exactly** — solver retains its last schedule, engine reads it after the chosen-spec re-dispatch), and persisted by `generate_quote` onto `QuoteRunResult` **and** `QuoteRecord.dispatch_profile` as an **additive, Optional, hash-excluded** field. The representative day is **one real solved day** (highest-total-discharge, earliest on ties) — never an average and never an invented shape; `None` on a run with no metered HH series and for the hook-less `BaselineEngine`. **The frozen `OptimisationResult` is NOT changed (D13 still honoured); `SCHEMA_VERSION` is NOT bumped** (the field is optional/defaulted and excluded from `assembly._canonical_run_input`, so `input_hash`/`quote_id` are unchanged — exactly as D23).
- **Reason:** The brief names Plotly specifically, and `wave5_scope §4` already treats Plotly and WebGL as interchangeable offline renderers — so adopting a **vendored** Plotly honours the brief while keeping the no-CDN/offline/deterministic posture D24 cared about. The dispatch chart's purpose is to "make the solve legible" (the W5-2 goal); `wave5_scope §4`'s acceptance principle is that such views plot from **persisted** run data, not an ad-hoc browser recompute — so a representative day is persisted by the proven D23 accessor pattern (lowest blast radius, shared frozen contract untouched), carrying no invented numbers (every value copied verbatim off a real solved half-hour; `None` where genuinely unavailable).
- **Alternatives considered:** Keep the D24 WebGL surface (rejected — the brief asks for Plotly; the supersede is explicit and `wave5_scope §4`-sanctioned). CDN-loaded Plotly (rejected — breaks offline / CSP / deterministic-bytes posture). Render the dispatch as an honest "not persisted" seam only (rejected by the user — persist a real day so the chart shows real dispatch). Persist the full ~17,520-period series (rejected — heavy on every record; a representative day is the legible projection and the full series stays the engine's internal artifact). Persist an element-wise **average / "typical" day** (rejected — that authors a synthetic shape; one *real* day is the honest projection). Promote dispatch onto the frozen `OptimisationResult` (rejected — D13's deferred joint event; the additive hash-excluded field + duck-typed accessor suffice, exactly as D23). Partial Plotly bundles (gl3d + basic) to shave size (rejected — the full pinned bundle is simplest and avoids trace-type gaps; size is acceptable for a self-hosted, cached static asset).
- **Consequences:** New vendored `src/gridq/app/static/plotly.min.js` (~4.5 MB, v2.35.2 pinned — its only `cdn.plot.ly` reference is the unused topojson geo base, never fetched for our trace types); `static/charts.js` rewritten as a thin Plotly config builder (replacing the WebGL/2D-canvas code); new `gridq.schemas.dispatch_profile` (2 models) + `HalfHourlyDispatchSchedule.representative_day_profile()` + `MilpDispatchSolver.last_schedule()` + `MilpEngine`/`DemoEngine.last_dispatch_profile()` + pipeline/assembly/record threading (`QuoteRecord.dispatch_profile`, `QuoteRunResult.dispatch_profile`); `app/view.py` gains `dispatch_island` + `value_stack_island` and the surface's Plotly `z_matrix`/`feasible_matrix`; templates — new `_dispatch.html`, a value-stack chart div in `_value_streams.html`, `<canvas>`→`<div>` chart mounts in `_surface.html`/`_finance.html`, the vendored Plotly `<script>` + two new islands in `inspect.html`. New tests: `test_dispatch_profile.py` (9), `test_app_view_islands.py` (6), `test_app_charts_wiring.py` (5 — vendored-Plotly/no-CDN, chart divs + islands, demo islands populated, deterministic page, honest no-dispatch seam); the existing app/template/sizing tests stay green. **`SCHEMA_VERSION` unchanged** (additive, hash-excluded — as D23). D24(5) is superseded for the renderer; the rest of D24 stands. Public hosting (`wave5_scope §1`) remains the separate open deployment step.

### D24 — The Wave-5 world-class UI: FastAPI+Jinja2 minted as the framework; the polished multi-screen surface supersedes the v1 thin-shell guardrail; demo = the real engine on a labelled synthetic dataset + illustrative debt; a self-contained WebGL surface; gitignored per-run persistence

- **Date:** 2026-06-24
- **Author:** Olly (`agent/frontend`, W5-1)
- **Decision:** W5-1 builds the public end-to-end product surface on top of W5-0 (D23) and the real MILP (D22). (1) **Framework minted.** The FastAPI + Jinja2 / server-rendered / no-JS-toolchain choice — until now only *Proposed* in [frontend_decision.md](frontend_decision.md), never given a `D` — is recorded here as the decision. (2) **Two explicit run modes, no default.** A landing mode-chooser offers **Real site** (primary) and **Demo** (opt-in); neither is auto-selected and synthetic data is never substituted for a real run. Real mode runs the live path (`generate_quote(engine=MilpEngine())`), grades the tier and surfaces the ranked data-asks (reusing `gridq.ingestion`), degrading honestly to `insufficient_data` when inputs are missing — never an invented value. (3) **The Wave-5 visual layer supersedes the v1 thin-shell guardrail** (frontend_decision.md §8). Boundary item 3.9 (2026-quality outputs / visual proposal layer) is deliberately **pulled forward** for this task: ~8 navigable screens (landing · real form · demo · inspect · system spec · memo+proposal · telemetry), a brand design system on the Neura palette, a 3D surface and 2D charts — the opposite of the deliberately-minimal v1 shell, by explicit task scope ("quality bar of a product shown to a world-class expert"). (4) **Demo = real engine on a labelled synthetic dataset + illustrative debt.** A committed, clearly-labelled `examples/demo_site/` (a synthetic 12-month half-hourly CSV + a `SiteModel`) is run through the **real** MILP dispatch + sizer; a standalone `gridq.app.demo.DemoEngine` (implements the `OptimisationEngine` Protocol via the *public* sizing/dispatch API + the D23 `last_sizing_run`/`selected_dispatch_energy` hooks) injects **illustrative (not underwritten) debt terms** so the surface's levered-return axis, P90-DSCR-feasible region and chosen optimum populate. Olly's `MilpEngine` is **unmodified**; the one additive core change is an optional `generate_quote(..., debt_terms=None)` passthrough to `run_financial_model` (default `None` ⇒ the honest real-mode path is unchanged). The demo run stays honestly `not_bankable` and is badged *illustrative* throughout; every figure is engine-derived from labelled inputs, never typed in. (5) **Charts are self-contained WebGL/canvas** authored in-repo (`static/charts.js`) — no Plotly, no framework, no CDN, no build step — fed a JSON island of **raw record values** (for pixel geometry) + **pre-formatted label strings** (for any visible text); an accessible server-rendered fallback table is always present. (6) **The arithmetic-free invariant is extended to the UI structurally:** all figures come from `outputs.build_view_model` + `outputs.formatting` (exposed as Jinja `fmt_*` globals); a new AST test (`tests/test_app_templates_no_arithmetic.py`) parses every app template and forbids arithmetic operators / aggregation filters on displayed values (the template analogue of `test_outputs_no_arithmetic_in_html.py`). (7) **Per-run persistence to a gitignored store** (`gridq.app.store`, `$GRIDQ_RUN_STORE`, default `runs/`) keyed by the deterministic `quote_id`, so the navigable screens reload one run; a user's own real submission is processed for their run, stored privately (gitignored, no public indexing) with a `delete_run` deletion path, and real third-party data is never preloaded — only the synthetic demo ships (wave5_scope §5).
- **Reason:** The product's credibility comes from *watching a real site go in and an honest quote come out* — so a live backend with the two modes, not a frozen page. On placeholder assumptions a real run prices nothing as bankable and (without a meter file) returns `insufficient_data`, which is the honest state but an empty surface; a synthetic-but-labelled load run through the **real** engine produces a genuinely populated surface + finance while staying not-bankable, and the illustrative debt is the minimal labelled input needed for the §4 feasible-region/optimum — all numbers still derived by the deterministic engine (the `rich_quote` precedent: a labelled synthetic input is not fabrication). Self-contained WebGL honours the repo's no-CDN/offline/deterministic posture better than vendoring a multi-MB charting blob. Persisting by deterministic `quote_id` keeps navigation fast while preserving the determinism the smoke test asserts.
- **Alternatives considered:** Keep the v1 thin shell (rejected — the task explicitly demands a world-class multi-screen surface). Auto-run the demo / default to synthetic (rejected — wave5_scope §2 forbids it). A labelled **mock** engine fabricating per-candidate returns (rejected — `enumerate_sizing` + the real MILP on a synthetic load derives the numbers honestly). Add a `sizing_grid`/debt field to the frozen `OptimisationResult` or modify `MilpEngine` to inject debt (rejected — keeps Olly's contract/engine untouched; the standalone `DemoEngine` + the Oli-owned `generate_quote` passthrough suffice). Vendor Plotly (rejected per the user — WebGL, self-contained). Frozen pre-generated `quote.json` for the demo (rejected — number-drift risk; the live run is the credibility moment). Re-run the engine per screen instead of persisting (rejected — slower; deterministic persistence is cleaner and is the documented privacy story).
- **Consequences:** New `gridq.app` modules — `app.py` (routes), `view.py` (Jinja `fmt_*` globals + chart islands), `store.py` (gitignored persistence), `demo.py` (`DemoEngine` + `load_demo_site`) — plus a brand `static/app.css`, self-contained `static/charts.js`, and ~14 templates/partials. One additive core change: `generate_quote(..., debt_terms=None)` → `run_financial_model` (backward-compatible; real mode unchanged). New `examples/demo_site/` (synthetic HH CSV + `SiteModel` + a labelling README); `examples/README.md` refreshed. Tests: `test_app_smoke.py` rewritten for the multi-screen flow, plus `test_app_demo.py`, `test_app_real_mode_gating.py`, `test_app_templates_no_arithmetic.py`. **Whole tree green: 537 tests pass; `ruff`/`ruff format`/`mypy`(100) clean; review_gates GREEN.** `SCHEMA_VERSION` unchanged (no contract change). Per-value assumption-`source` provenance in the UI (C020) is threaded as run-level provenance now; surfacing each figure's individual `Assumption.source` is a tracked follow-up (C023). Public hosting (a Procfile/container + pinned solver deps) remains the separate wave5_scope §1 deployment step.

### D23 — The full sizing evaluation grid is exposed for the 3D surface via a duck-typed engine accessor + an additive, hash-excluded `QuoteRecord` field — the frozen `OptimisationResult` stays untouched

- **Date:** 2026-06-24
- **Decision:** W5-0 lands the Wave-5 pre-req flagged by the Wave-4 harmony audit (`wave5_scope.md` §4): the sizer already **retains** the full power×duration grid (`SizingRun.candidates_evaluated`) but `MilpEngine.solve()` dropped it at the frozen platform↔engine seam, so it never reached the `QuoteRecord`/outputs. The grid is now exposed and persisted by the **recommended (lowest-blast-radius)** of the two agreed options: **(1)** a new serializable contract `gridq.schemas.sizing` — `SizingGrid` + `SizingGridPoint` (`GridQModel`s) — projected by a new `SizingRun.sizing_grid()` (every candidate's `power_kw`/`duration_hours`/`energy_kwh`/`levered_return_pct`/`project_irr_pct`/`min_p90_dscr`/`passes_covenant`/`solver_status`, plus the chosen optimum and the run covenant); **(2)** a duck-typed `MilpEngine.last_sizing_run()` hook that **mirrors `selected_dispatch_energy()` exactly** (stash on `solve()`, reset at the top); **(3)** `generate_quote` reads the hook via the same `getattr`/`callable` pattern and persists `sizing_grid` onto `QuoteRunResult` **and** the `QuoteRecord` as an **additive, Optional** field. **The frozen `OptimisationResult` is NOT changed (D13 still honoured)** — that promotion remains the separate deferred deliberate seam event. **`SCHEMA_VERSION` is deliberately NOT bumped:** the new `QuoteRecord` field is optional/defaulted and is **excluded from `assembly._canonical_run_input`**, so it cannot change a quote's `input_hash`/`quote_id`, and existing records/fixtures without it validate unchanged.
- **Reason:** The 3D sizing surface (power × duration × levered return, P90-DSCR-feasible region highlighted, optimum marked) must plot from **persisted** run data, not an ad-hoc browser recompute (`wave5_scope.md` §4 acceptance). The accessor + record-field route is the documented "lowest blast radius" choice: it reuses the proven D13 hook pattern, keeps the shared frozen contract and the W3-4 conformance suite untouched, and stays governance-light (no shared-contract change, no version bump). It carries **no invented numbers** — every scalar is copied verbatim off a `CandidateEvaluation`, and a metric that is genuinely unavailable (no debt ⇒ no levered return / no DSCR) stays `None`; the optimum is honestly `None` when no candidate meets the covenant (the placeholder case).
- **Alternatives considered:** Promote a `sizing_grid` field onto the frozen `OptimisationResult` (rejected — a shared-contract change gated behind the deferred deliberate seam event + `SCHEMA_VERSION` semantics + conformance updates; far higher blast radius than the requirement needs). Bump `SCHEMA_VERSION` for the additive `QuoteRecord` field (rejected — `schema_version` is dumped into the canonical hash input, so a bump would change every quote `input_hash`/`quote_id` for a backward-compatible addition). Rely solely on deterministic re-running of the sizer to regenerate the grid (kept as the interim fallback, but persisting is cleaner for the demo and for reproducibility from a saved quote).
- **Consequences:** New `gridq.schemas.sizing` module (2 models) + `SizingRun.sizing_grid()` + `MilpEngine.last_sizing_run()` + the pipeline/assembly/record threading; new `tests/test_sizing_grid.py` (12 tests: grid completeness vs `candidates_evaluated`, optimum-marked + feasible-region, honest-`None` on the placeholder, determinism, JSON round-trip, the engine hook, pipeline persistence, the `BaselineEngine` no-hook backward-compat path, and the hash-unchanged proof). Whole tree green: **527 tests pass; `ruff`/`ruff format`/`mypy`(97) clean**. The grid is now ready for the (separate) Wave-5 surface renderer to consume. `examples/quote_record_example.json` is left unchanged (it was built with the `BaselineEngine`, so its `sizing_grid` is absent → `None`); regenerating a demo record with a populated grid is a follow-up for the surface task.

### D22 — The real half-hourly dispatch MILP (`gridq.optimisation`, linopy/HiGHS) is the default engine; its objective is the site's own time-of-use bill, valued in the dispatch engine; determinism pinned via HiGHS IPM + an xarray bound

- **Date:** 2026-06-24
- **Decision:** W4-1 replaces the placeholder dispatch with a **real deterministic half-hourly MILP** in a new **`gridq.optimisation`** package (the home D16 reserved for the real M2/M3/M4 engine). `MilpDispatchSolver` implements the inner `gridq.dispatch.DispatchSolver` Protocol; `MilpEngine` implements the outer `gridq.engine.OptimisationEngine` by composing the existing `gridq.sizing.enumerate_sizing` loop with the MILP ("one engine, called twice", D17/D21). Six sub-decisions: **(1) Objective = the site's own bill.** The only real half-hourly price signal in a `SiteModel` is its time-of-use import tariff, so the MILP maximises the site bill reduction `Σ price_t·(discharge_t − charge_t)·Δt` where `price_t` is the site's `import_rates` (energy) + `duos_*_rate` placed in time by a new **`tariff_calendar`** (an *indicative GB time-window calendar* — content, never a price; unrecognised tariff labels degrade to no-arbitrage, never an invented window). No invented prices; the `£/MW/yr` value-stack proxies stay a labelled fallback. **(2) Valuation lives in the dispatch engine** (`optimisation/valuation.py`): the MILP computes each stream's £ from the solved schedule and emits the `ValueStreamResult`s itself; the value stack is consulted only for gating/firmness/constraint descriptors — **no value-stack file changed**. Attribution partitions the objective with no double-counting (energy→tariff arbitrage, DUoS→red-band avoidance, MIC/ASC headroom→£0 as it is realised through the connection-bounded arbitrage). **(3) Physics:** SoE dynamics with `round_trip_efficiency.ac_round_trip` (charge = discharge ÷ RTE, finance's exact gross-up convention), charge ≤ `min(MIC,ASC) − site_load`, behind-the-meter discharge ≤ load (no export on the floor; wholesale stays gated), warranty throughput a HARD annual cap (`warranty_cycle_limits…by_duration_h`, conservative duration flooring). Degradation enters as the throughput cap; a degradation *marginal-cost* objective term is **omitted** (no lifetime-throughput / £-per-cycle number exists — honest, not invented). **(4) Determinism:** HiGHS runs single-threaded, fixed `random_seed`, deterministic presolve, **IPM + crossover** (the annual throughput cap makes dual simplex pathologically slow — ~15 s/full year — while IPM+crossover reaches the identical optimum in <1 s and the crossover yields a clean vertex schedule), all outputs rounded; a negligible tie-break regulariser makes the optimum unique. `linopy`/`highspy` added to core deps and **`xarray` pinned `<2025`** (linopy's expression layer breaks on the 2025+/2026 xarray Dataset constructor) — the pin is part of the reproduction contract. **(5) Default wiring:** `MilpEngine` is the CLI `run` default and is registered in the W3-4 conformance `ENGINES`; `BaselineEngine` stays the `generate_quote` signature default (lowest blast radius). The engine surfaces the selected candidate's `DispatchEnergy` via an optional `selected_dispatch_energy()` hook that `generate_quote` threads into finance (new additive optional `dispatch_energy` param), so levies-on-charge + price-to-target compute on the quote path **without** promoting fields onto the frozen `OptimisationResult`. **(6) D13 honoured:** the annual volumes still reach finance only via the injected `DispatchEnergy` hook; no field is added to `OptimisationResult` (that promotion stays the deferred deliberate seam event).
- **Reason:** The brief's deterministic core (no LLM, no invented prices) and "objective = the composed value stack over the dispatch schedule". The site's tariff is the only non-fabricated half-hourly price for the priced floor; valuing the schedule in the dispatch engine keeps W3-1's value-stack files untouched (the proxies were always labelled "site-derived value lands with the dispatch engine"). Composing the existing sizer rather than re-implementing it preserves D21's "one engine, called twice" consistency seam. The wholesale/Modo HH price series (D20) stays deferred — it feeds only the gated upside, which the floor never prices.
- **Alternatives considered:** Keep the `£/MW/yr` proxies as the objective (rejected — the schedule would not drive the floor £; "objective over the schedule" would be vacuous). Edit the value-stack `_price` hooks to do the HH integral (rejected by the lane owner — it churns a W3-1 file in the shared tree; the dispatch engine is the agreed home). Flip `generate_quote`'s default to `MilpEngine` (rejected — larger blast radius; CLI-default + conformance registration is enough). Dual simplex (rejected — ~15 s/full-year solve; IPM is ~6× faster to the identical optimum). Promote charge/discharge onto `OptimisationResult` now (rejected — D13's deferred joint event). Invent a per-cycle degradation cost or a DUoS band schedule when the data is absent (rejected — degrade with a warning, never fabricate).
- **Consequences:** New `gridq.optimisation` package (6 modules) + 4 test modules (~30 tests: physics, determinism, conformance, the dispatch-energy seam, the tariff calendar, a full-year benchmark ≈2.5 s). `pyproject` gains `linopy>=0.5,<0.6`, `highspy>=1.7`, `xarray>=2024.2,<2025`. The CLI `run` now produces a `production`/`optimal` quote with real arbitrage + DUoS red-band floor value, populated charge/discharge volumes and computed levies/price — still **`not_bankable` on `gb_v0_1`** (placeholder RTE + no debt/covenant). `quotes/assembly.py`'s not-bankable note was made engine-agnostic (it wrongly said "baseline engine — no revenues priced"). The W3-4 conformance suite passes for both engines unchanged; finance/quotes/outputs took no field-shape change. Open follow-ups: a band→window calendar in a future assumption version (so the windows are versioned content, not a code default); shadow-price valuation of MIC/ASC headroom; warm-start/parallel candidate solves if the sizing-run benchmark demands it; the D13 volume promotion (jointly with Olly).

### D21 — Sizing optimiser turns real: levered (equity) return modelled (P50 basis), the bankable constraint stays inside the objective, and "one engine, called twice" is enforced by a consistency seam

- **Date:** 2026-06-24
- **Decision:** W4-2 makes the M4 sizing outer loop genuinely optimise (no real MILP required; W4-1 still deferred — it drops in behind the same `DispatchSolver`). (1) **Levered return is real:** `finance.returns.equity_irr(...)` derives the **equity (levered) IRR** from the equity cashflow series `[-(capex − debt principal), then P50 operating FCF − debt service]`, reusing `project_irr` for the root-find (so its one-sign-change reliability gate is inherited). It is computed on the **P50 base case** while the **P90** case governs the DSCR covenant — the standard project-finance split (size to the downside, report the base-case equity return). `run_financial_model` populates a new **additive** `FinancialCase.equity_irr` (optional, default `None`); the sizer wires it into `CandidateEvaluation.levered_return_pct`, so `SizingObjective` now maximises a real levered return (its `primary_metric_available` becomes live). Unavailable (`None`) — never guessed — when there is no debt principal, an empty debt schedule, a non-positive equity outlay, or a non-conventional equity series. (2) **One engine, called twice (consistency contract #1):** the per-candidate dispatch+finance body is extracted into one primitive `sizer._evaluate_spec(...)` used by **both** `enumerate_sizing` (once per candidate) and a new standalone `sizing.underwrite_spec(...)`. `underwrite_spec` reconstructs streams/constraints/`DispatchProblem` **independently** from the raw `(site, assumptions, spec)` inputs (it is not handed the sizer's pre-built result), so re-pricing the chosen spec is a genuine recomputation; `sizing.financial_fingerprint(...)` is the timestamp-free projection a consistency test compares (load-bearing numbers + `run_context.input_hash`, never `created_at` or object identity). (3) **End-of-life deliverability (honest data-ask):** `SizingObjective.feasible` now also rejects a spec with an explicit `end_of_life_deliverable is False`; `None` (the placeholder/v1 case — no late-life signal at the seam) is an unassessed **data-ask**, never a fabricated pass (per D12). Degradation already flows through finance (revenue fade → lower late-life P90 CFADS → lower P90 DSCR), so it is not re-modelled here.
- **Reason:** The System Brief's consistency contracts #1/#2 require the bankable constraint to live *inside* the sizing objective and the chosen spec to underwrite to identical numbers. Equity IRR is **derived** from injected `DebtTerms` + a real dispatch's priced revenue (every input given, none invented) — the honesty line is crossed only by manufacturing a number that was not supplied, which the `project_irr` delegation refuses. Sharing one `_evaluate_spec` primitive is the architectural form of "one engine"; the consistency test guards it from drift and from any wall-clock leaking into a computed value.
- **Alternatives considered:** Derive equity IRR on the **P90** series — **rejected** (conflates the downside stress with the headline equity return and diverges from the existing P50 `project_irr` basis; P50-return-under-P90-covenant is standard PF). Leave levered return unmodelled (the prior v1 honest-defer) — **rejected** (the objective then never maximises what the brief names as its objective). Have `underwrite_spec` reuse the sizer's already-built result — **rejected** (makes the consistency check a tautology; it must reconstruct independently). Make a test-double dispatch emit a *production* result on the default path to force `bankable=True` — **rejected as a spoof**; `run.bankable` stays False on `gb_v0_1` (unconfirmed assumptions) by construction, and the gate-release is proven only via the established `_confirmed_lib()` + bankable-candidate + injected-debt TEST-ONLY pattern. Enforce a real end-of-life deliverability check now — **rejected** (needs the real engine's late-life capability; D12 keeps it a data-ask).
- **Consequences:** `finance.returns` gains `equity_irr`/`EquityIrrResult`; `FinancialCase` gains an additive optional `equity_irr` (backward-compatible, no `SCHEMA_VERSION` bump — flagged to Olly in **C022**); `gridq.sizing` gains `underwrite.py` (`underwrite_spec`/`UnderwriteResult`/`financial_fingerprint`), the `_evaluate_spec` primitive, an `end_of_life_deliverable` field on `CandidateEvaluation` + the `Evaluation` protocol, and `levered_return_pct`/`end_of_life_deliverable` in `SizingRun.fingerprint()`. New/updated tests: equity-IRR unit tests, schema additive-field tests, an orchestrator equity-IRR test, and the four sizing behaviours (bankable-by-construction / covenant-violating-never-selected / sizing == underwrite re-price / determinism) + a gate-release test. Full suite green; my files clean (ruff/format/mypy/review_gates). Still **not bankable** on `gb_v0_1`. Builds on the `agent/counterparty` lane's uncommitted credit→rate/tenor work (D18) in the shared tree (both left uncommitted for review).

### D20 — Real Modo/wholesale forecasts reach dispatch as a hash-pinned, per-scenario price series; scenario multipliers stay explicit; the HH price-series field shape stays deferred to the real MILP

- **Date:** 2026-06-24
- **Decision:** W4-4 wires real external price forecasts into the scenario engine and the dispatch price-scenario seam, additively (no frozen-contract change). (1) **Pinning + attachment:** `scenarios.engine.build_forecast_backed_scenario_set(assumptions, forecast_file=…)` loads a Modo/wholesale export through the ingestion loaders, pins it as a `ForecastReference` (content + file hash, recomputed from the curve), and attaches that verified reference to **every** Low/Central/High band — reusing `build_standard_scenario_set` (weights still from the library; placeholder ⇒ not bankable). (2) **Dispatch feed = the existing opaque seam + a loader:** the forecast-backed `Scenario` (carrying `price_multiplier` + `forecast_ref`) reaches `DispatchProblem.scenario` via the existing `enumerate_sizing(scenario=…)` threading; a new `scenarios.forecast_prices.scenario_price_series(ref, scenario_name=…)` (and `price_series_for_set(set)`) resolves each band's price *series* from the pinned file, **re-verifying the content hash** (drift → empty series + re-pin warning) and **never fabricating** an absent band. (3) **Multipliers stay explicit:** the forecast is **not** read to set `price_multiplier` — Low/High multipliers remain explicit, sourced caller input (a spread is never derived from, nor invented for, the file). The forecast drives the *dispatch price series*, not the cashflow multiplier. (4) **HH field shape stays deferred:** the concrete half-hourly price-series *field* on the dispatch input stays the real MILP's to define (`dispatch/contract.py` names it "knowingly absent"; D12/D17). Flagged to Olly (C021). (5) **Degrade honestly:** an absent/unreadable forecast file → an identity set with **no** `forecast_ref` + a ranked data-ask warning, never a raise or a guessed reference.
- **Reason:** Same discipline as D13 — external forecasts are *referenced input data*, never GridQ assumptions and never numbers a model may invent (SKILLS.md; `docs/scenario_engine.md` §3). Deriving a `price_multiplier` from the forecast's own curves was explicitly **declined** (a coarse mean-price ratio is not the revenue sensitivity the data supports); the honest path is to feed the *real series* to the dispatch MILP, which computes per-scenario revenue properly when it lands. The opaque `DispatchProblem.scenario: Any` seam + a loader keyed off `forecast_ref` adds the capability with zero change to the frozen `OptimisationResult`/`DispatchProblem`/`Scenario`.
- **Alternatives considered:** Auto-derive Low/High multipliers from the forecast's scenario curves (mean-price ratio) — **rejected** (a coarse proxy presented as a sensitivity; violates "no invented spread"). Add a concrete `price_series` field to `Scenario`/`DispatchProblem` and have `SimpleSolver` consume it now — **rejected** (codifies the HH shape ahead of the MILP and against the unconfirmed Modo format C012; the shape is Olly's to define). Wire forecast-backed Low/Central/High through `generate_quote`/`cli.py` now — **deferred** (a bigger cross-lane pipeline change; recorded as the next step). Store the forecast curve in the assumption library — **rejected** (launders external data into a GridQ assumption and loses provenance; `scenario_engine.md` §3).
- **Consequences:** New `gridq.scenarios.forecast_prices` (`ScenarioPriceSeries`/`scenario_price_series`/`price_series_for_set`) + `build_forecast_backed_scenario_set`; a representative `tests/fixtures/modo_sample_forecast.csv` (a format sample loaded through the real path + pinned with a real hash, **not** real data — C012 stays open). 10 new tests incl. the forecast-backed scenario reaching `DispatchProblem.scenario` via a recording solver; W4-4 files green (ruff/format/mypy/review_gates). No frozen-contract change; everything stays not-bankable on placeholder weights. Raises **C021** (Olly to confirm the dispatch-side consumption seam); advances **C012** (still open — needs a real Modo file).

### D19 — `gb_v1_0` is a sourced provenance release that confirms nothing (carry-forward + honest relabel; no web-scraped forecasts; round-trip-efficiency citation corrected)

- **Date:** 2026-06-24
- **Decision:** W4-3 creates a **new** assumption release [`assumptions/gb_v1_0.yaml`](../assumptions/gb_v1_0.yaml) rather than editing `gb_v0_1` (which stays **frozen** for provenance). Policy set here: (1) **Populate a value only where a citable source genuinely exists.** Two values are backed by an in-repo document (the Neura business plan) — `battery_capex.installed_cost` (£500k/MW) and `financing.target_equity_irr_pct` (12%, the low end of the cited 12–13% band) — and are promoted `placeholder → needs_review` with the citation inline and `confidence: low` (indicative plan figures). (2) **Everything else is carried forward unchanged with an honest, display-ready `source`** ("no citable source identified" / "(gated)" preserved) — **no number is invented or guessed**. (3) **External market forecasts are *not* web-scraped into the library** (CM clearing/de-rating, DUoS, DC/DM/DR, wholesale/BM): per SKILLS §6 they are *referenced, not stored*, and the gated streams stay placeholder **zero** — so no volatile/region-specific figure is laundered in behind a URL. (4) **Provenance correction:** `gb_v0_1` cited `round_trip_efficiency` (0.88) as `system_requirements (≈12% round-trip loss)`, but the source document states **no** such figure; `gb_v1_0` re-labels it **NOT sourced** (industry-typical placeholder). (5) The release **confirms nothing** (0 `confirmed`; 10 `needs_review`, 15 `placeholder`) and is **not bankable**. Gaps are ranked by impact with named authoritative sources in [`docs/assumptions_to_source.md`](assumptions_to_source.md).
- **Reason:** The strict rule is "never invent; no source → labelled placeholder; never present a placeholder release as bankable." A sourced *file* (not an in-place edit) keeps `gb_v0_1` immutable for the quotes pinned to it and breaks none of the ~23 tests that pin `gb_v0_1`. Carrying values forward while relabelling `source` is honest because **`status` (not the `source` string) gates the pipeline** — an un-cited value stays `placeholder`/`needs_review`, so outputs stay not-bankable. Web-scraping market figures would violate "forecasts referenced, not stored" and risk presenting stale/region-specific numbers as sourced — the exact failure W4-3 guards against. The frontend-traceability ask (every value used in a calculation must show its origin) is served by the per-value `source` citations; threading them through to outputs is a separate lane (C020).
- **Alternatives considered:** Do the provenance pass **in-place on `gb_v0_1`** — rejected (violates the frozen-release rule and breaks the ~23 `gb_v0_1`-pinned tests). Reconciles with **D18**, which says "a version bump is reserved for *confirming* values": this is not an incidental placeholder addition but an owner-directed (Olly) sourcing release, and as a **separate additive file** it neither mutates `gb_v0_1` nor confirms anything, so it sits outside D18's concern. **WebFetch stable gov constants into sections** (e.g. CM de-rating) — rejected (de-rating factors are auction-cycle-versioned forecasts gated by C007, not stable constants; no section cleanly holds a bare statutory rate; hallucination/staleness risk). **Mark capex/IRR `confirmed`** — rejected (both are indicative plan figures needing real installer quotes / Sim's confirmed cost of capital; `needs_review` is the honest status). **Name the release `gb_v0_2`** to avoid implying "1.0 = stable" — not chosen (the task specifies `gb_v1_0`; guarded instead by `metadata.notes`, 0 `confirmed`, and a test locking that).
- **Consequences:** New `assumptions/gb_v1_0.yaml` (25 entries, 23 sections) + `docs/assumptions_to_source.md`; `tests/test_assumptions.py` +12 (counts/provenance/gated/loader-rejects-`ValidationError`/`gb_v0_1`-frozen). `gb_v0_1` and all its pinned tests untouched. **Coordination:** the `agent/counterparty` lane (D18) is adding `financing.credit_rate_tenor_policy` + the C008 debt keys to the library in parallel (uncommitted on its `gb_v0_1` copy); `gb_v1_0` was built from the **committed** frozen `gb_v0_1` and deliberately does not absorb that — the next release must fold it in. Raised **C020** (per-value `source` → outputs/frontend). Filed `skills/proposed_updates/2026-06-24-assumptions-provenance.md`.

### D18 — Deterministic counterparty credit→rate/tenor mapping (M9, `finance/credit.py`); library-versioned band table; gearing stays Sim's; rate/tenor flow to finance without it

- **Date:** 2026-06-23
- **Decision:** W4-5 builds the **deterministic credit→rate/tenor mapping** that M9's spec calls "the v1 deliverable" (the precondition "W3-5" was never built, so this lane builds it as its foundation). (1) **Home + shape:** a pure function `map_credit_to_rate_tenor(credit_status, assumptions)` in new `gridq.finance.credit` maps a coarse `CreditStatus` band (`strong`/`adequate`/`weak`) to `(rate_pct, tenor_years)` read from a **new versioned assumption** `financing.credit_rate_tenor_policy` (a per-band structured value) — no private constants. `unknown`/`not_assessed` → **unavailable + warning, never defaulted to a band** (that would fabricate a credit assessment). (2) **Monotonicity is enforced in code** (worse band ⇒ rate non-decreasing, tenor non-increasing); a non-monotonic policy table refuses to map — directly satisfying the spec's "no glowing return at a rate the covenant doesn't justify." (3) **Gearing stays out of scope:** debt gearing is Sim's **portfolio finance** (B001 / module ownership), so this lane adds **no** `debt_gearing_pct`. `build_debt_terms_from_credit(...)` assembles a full `DebtTerms` only when gearing also exists (library key or explicit override); when it is absent (v1) the credit `CreditRateTenor` is still returned, so **rate/tenor reach `FinancialCase.debt_assumptions` while DSCR stays honestly unavailable**. (4) **Wiring:** `run_financial_model` derives `DebtTerms` from `site.counterparty.credit_status` **only when the caller did not inject `debt_terms`** (an injected one always wins). `bankable` is unchanged — placeholder policy ⇒ not bankable. (5) **Creditsafe seam (B001):** the input boundary is `CreditStatus`; the future tool produces `score → CreditStatus` (its own future band-threshold mapping) upstream and drops in with no downstream change — `Counterparty` is **not** widened with a raw score now.
- **Reason:** Same discipline as D9/D10/D13 — the deterministic mapping is GridQ's (Oli's) to build; the *values* are Sim's credit policy, so they live in the versioned library as labelled placeholders (not bankable until confirmed). Routing rate/tenor through a credit policy table — not the flat `debt_interest_rate_pct`/`debt_tenor_years` keys `debt.build_debt_terms` reads — keeps `debt.py`'s honesty gate (and its tests) intact. Surfacing rate/tenor without gearing makes the M9 mapping demonstrable end-to-end now, without inventing a portfolio-finance number.
- **Alternatives considered:** Add a placeholder `debt_gearing_pct` so DSCR lights up on `gb_v0_1` — **rejected** (crosses into Sim's lane and would print placeholder-derived DSCR/covenant figures that read as real). Auto-cap debt tenor at `expected_tenure_years` — **rejected for v1** (a financing-policy decision; warn instead, defer the cap to a library-sourced rule). Bump the library version for the additive placeholder — **rejected** (a version bump is reserved for *confirming* values; ~23 test files pin `gb_v0_1`). Widen `Counterparty`/`FinancialCase`/`OptimisationResult` — **rejected** (no frozen-contract change needed; the schema + outputs slots already exist).
- **Consequences:** New `gridq.finance.credit` (the mapping) + one additive `financing.credit_rate_tenor_policy` placeholder in `gb_v0_1.yaml`; `run_financial_model`/`_debt_assumptions` thread the credit-derived terms; ingestion gains a counterparty credit-readiness warning + a precise `counterparty_credit_status` ranked ask. **Resolves the rate/tenor half of C008** (gearing remains the one absent piece, Sim's). 21 new tests; my files green (ruff/format/mypy/review_gates); the credit policy stays placeholder ⇒ every credit-driven case stays not-bankable.

### D17 — Dispatch solver contract (`gridq.dispatch`) + sizing skeleton (`gridq.sizing`); placeholder stays non-bankable; charge/discharge produced on `DispatchResult`, not `OptimisationResult`

- **Date:** 2026-06-23
- **Decision:** W3-2 builds the **inner dispatch-solver seam** and the **sizing outer loop**, MILP-ready, with the real half-hourly MILP (linopy/HiGHS) **deferred to Olly**. (1) **Homes:** the dispatch solver contract lives in a new **`gridq.dispatch`** package (`DispatchProblem`/`DispatchResult`/`DispatchSolver` + the `SimpleSolver` placeholder + `to_finance_dispatch_energy`); the sizing optimiser in a new **`gridq.sizing`** package (`SearchSpace`/`SizingObjective`/`enumerate_sizing`). This supersedes C005's sketch of one `optimisation/` package and complements D16 (the outer `OptimisationEngine` seam stays in `gridq.engine`); the real MILP implements the `DispatchSolver` Protocol and drops in with no downstream change. **Flagged to Olly (C019) — owner of M3/M4 — to confirm or relocate.** (2) **Two distinct seams, both untouched here:** the *outer* `OptimisationEngine.solve(request)→OptimisationResult` (frozen platform↔engine) and the *inner* `DispatchSolver.solve(problem)→DispatchResult` (new). The production engine composes the sizer with a real `DispatchSolver` ("one engine, called twice"). (3) **Placeholder honesty:** `SimpleSolver` is the dispatch-layer twin of `BaselineEngine` — always `not_bankable`/`baseline`, prices nothing, and **reports `annual_charge_kwh`/`annual_discharge_kwh` as `None` (unavailable), never an invented volume**. The `SizingRun` is never `bankable=True` on the placeholder (DSCR unavailable without injected debt; levered return not modelled in v1). (4) **C010 producer seam:** `DispatchResult` carries `annual_charge_kwh`/`annual_discharge_kwh` using **D13's reserved names**, and `to_finance_dispatch_energy(result)` feeds finance's injected `DispatchEnergy` hook — so finance levies-on-charge + price-to-target compute the moment a real dispatch fills the volumes, with **no field added to the frozen `OptimisationResult`** (D13 honoured; promotion stays the deferred deliberate seam event). (5) **Objective:** `SizingObjective` embeds the P90-DSCR-≥-covenant feasibility gate; the *primary* metric (levered return) is **not modelled in v1** (finance computes only unlevered project IRR), so the objective never asserts a bankable best — honest by construction.
- **Reason:** Same discipline as D9/D10/D11/D13 — prepare the seam and the outer loop without faking bankable optimisation or freezing a guessed shape onto a contract ahead of its producer. A dedicated `gridq.dispatch`/`gridq.sizing` split keeps the *solver-output* contract (`DispatchResult`) cleanly separate from W3-1's value-function *read-model* (`value_stack/dispatch.py` `DispatchSummary`) and from the frozen `OptimisationResult`. Putting charge/discharge on the new `DispatchResult` (not `OptimisationResult`) is exactly D13's "carry it on the producer-lane artifact behind an injection hook; promote additively later."
- **Alternatives considered:** Put M3/M4 in one `optimisation/` package (C005's sketch) — deferred to Olly's confirmation; the contract+skeleton split is clearer and the real MILP can still live in `optimisation/` implementing `DispatchSolver`. Have the placeholder emit a cycles-based throughput so finance lines "populate" — **rejected** (that fabricates a dispatch behaviour the placeholder never solved; violates "never invent"). Add `annual_charge_kwh`/`annual_discharge_kwh` to `OptimisationResult` now — **rejected** (D13: premature ahead of the M3 producer). Reuse `BaselineEngine` as the dispatch placeholder — **rejected** (it is the *outer*-seam placeholder; the inner dispatch layer is finer-grained).
- **Consequences:** New `gridq.dispatch` + `gridq.sizing` packages (8 source files) + `docs/dispatch_engine.md`; 34 new tests; whole tree green (405 pass; ruff/format/mypy/review_gates clean). No schema, assumption value, or other lane's behaviour changed. The `DispatchEnergy` seam is proven *computable* via a synthetic-volume test, never by placeholder invention. Open follow-ups for Olly: confirm the package homes (C019); when the real MILP lands, expose a `DispatchScheduleLike` (W3-1 read-model) off the dispatch result so value functions price it, and promote the two volumes onto `OptimisationResult` jointly (D13).

### D16 — Remove the empty `optimisation_contracts/` stub; consolidate the seam in `engine/` and add an importable conformance suite
- **Date:** 2026-06-23
- **Decision:** Resolves [OPEN_COMMENTS](shared_work/OPEN_COMMENTS.md) **C003** (W3-4). The empty scaffold package `src/gridq/optimisation_contracts/` (an `__init__.py` docstring, no code) is **removed**. Everything it was a placeholder for already has a real, tested home: the `OptimisationRequest`/`OptimisationResult` contracts in `schemas/optimisation.py` + `schemas/market.py`; the engine interface in `engine/interface.py`; the baseline/mock in `engine/baseline.py`; the generic value/constraint interface + versioned stream registry in `value_stack/`; and the **real** engine (M2/M3/M4) in its own `gridq.optimisation` package (per `parallel_development_plan.md` §1, issue 014, and C005). `gridq.engine` is confirmed the single home of the platform↔engine seam, and now also hosts the **conformance suite** `gridq.engine.conformance` — a pytest-free, importable bar (`check_engine_conformance` / `assert_conformant`) that depends only on the frozen contracts, so the real MILP runs the *same* checks the baseline passes. The placeholder `BaselineEngine` passes all checks; three deliberately non-conforming stubs each fail exactly one check Pydantic does not enforce (a gated stream priced into the floor as `contracted`; non-determinism; a gated_upside entry with no `unlock_condition`). **Confirmed:** `BASELINE_POWER_FRACTION` (0.1) stays a labelled integration heuristic in `engine/baseline.py` — **not** an assumption-library value — because the baseline result is always `not_bankable` and the fraction injects no £/% into any priced output (same precedent as the tier-boundary constants, D10).
- **Reason:** A second package that overlaps `engine/` is a drift/confusion hazard — the CLI `info` table and `tests/test_imports.py` cited a package with no purpose. The conformance bar is the load-bearing half: issue 014 requires the real engine to drop in behind the *same* seam with no silent drift, and the schema validators alone do not catch the rules that matter most across the mock→real swap (a `contracted` gated stream over-counting the priced floor passes Pydantic; non-determinism and a dropped request-echo are invisible to the schema). Making the suite an importable module (not just a test file) lets Olly run it verbatim against `MilpEngine` from his own lane.
- **Alternatives considered:** **Repurpose** `optimisation_contracts/` for the real engine (rejected — the real engine's agreed home is `gridq.optimisation`, frozen in the parallel-development plan + C005; leaving a second package claiming "the seam" reintroduces the very ambiguity C003 flags). **Keep** the empty stub (rejected — dead scaffold the CLI/tests reference; misleads readers). **Move `BaselineEngine` into `optimisation_contracts/`** (rejected — `engine/` is the established, tested home with its own README). Put the conformance checks only in the test file (rejected — Olly could not import and reuse them, weakening the no-drift guarantee).
- **Consequences:** `gridq.optimisation_contracts` no longer exists; `cli.py` `_MODULES` lists `engine` + `value_stack` instead, and `tests/test_imports.py` smoke-imports the live packages. No schema, baseline, or downstream behaviour changed (zero functional diff beyond the package removal). `tests/test_optimisation_contract_conformance.py` parametrises over engines (`BaselineEngine` now; Olly appends `MilpEngine`) and exercises the M5 seam end-to-end via `quotes.pipeline.generate_quote` (M8 outputs join when that lane lands). C005 (value_stack vs optimisation/ package location) remains the dispatch lane's call, but this removal is consistent with — and reinforces — its proposed split.

### D15 — Customer bill before/after + savings is a labelled-pending seam in M8 (never estimated)
- **Date:** 2026-06-22
- **Decision:** The customer proposal's "bill before / bill after / savings" is surfaced by `gridq.outputs` as an explicit **labelled-pending seam** — rendered *unavailable — not yet computed* with the pending reason and a data ask — **not** computed in the outputs layer. There is no customer-bill model in v1 (`FinancialCase` carries no bill fields), and the outputs layer is forbidden from authoring numbers. The proposal still shows everything the record *does* carry (system size, structure, modelled term, expiry, the contracted floor vs labelled upside). Raised the additive `FinancialCase` fields for the finance lane to own in [OPEN_COMMENTS](shared_work/OPEN_COMMENTS.md) **C017**.
- **Reason:** The shared input-data rule and the M8 "numbers only from the record" rule. Computing a before/after bill needs a tariff × consumption (and dispatch-shifted) calculation that finance (M5) does not yet do; doing it in outputs would either invent a model or interpolate missing site data — exactly the fabrication the rules forbid. A site may also be T0/partial, where the bill cannot be computed honestly at all. Surfacing the seam (machinery ready, reading the fields when finance adds them) keeps the proposal honest now and zero-friction later.
- **Alternatives considered:** Compute a quick bill in the outputs layer (rejected — authors a number outside the deterministic core; risks invented/interpolated inputs). Add bill fields to `FinancialCase` *and* a bill model in this lane (rejected — scope creep into M5; a bill model is finance work, gated on confirmed tariff + dispatch volumes). Omit the section entirely (rejected — the proposal must name what's missing as a data ask, not hide it).
- **Consequences:** The v1 proposal's headline savings reads as pending with a precise data ask; when finance lands the additive fields (C017), the proposal's existing `BillSavingsView` path renders them with no outputs change. Gated/conditional upside is, separately, always kept out of any headline (System Brief §9 RTM risk).

### D14 — M8 outputs render the run record (never author a number); v1 prose is deterministic and LLM-free
- **Date:** 2026-06-22
- **Decision:** `gridq.outputs` (M8) is a **faithful renderer of the run record**: every figure is injected from the `QuoteRecord` (extracted once into a typed `OutputViewModel`), turned into a string only in `formatting`, and placed by the per-artifact renderers — which **author no number**. A missing figure becomes an explicit *unavailable(reason)* / *placeholder* label, never a 0 or guess. The **HTML generator does no arithmetic** (only formatting via `formatting`), enforced by an AST scan. **Prose is drafted LLM-free in v1** behind a `ProseDrafter` Protocol whose methods take **categorical inputs only** (booleans/enums — never a `FinancialCase` figure), so prose structurally cannot carry a record number; the default `DeterministicProseDrafter` ships fixed copy, and a perimeter LLM drafter can be injected later **without ever touching a number**. Implements issues 011/012 and the CLAUDE.md "deterministic core / LLM prose never creates a number" rule.
- **Reason:** Acceptance test 1 (every output withstands an IC / credit desk) and the foundational invariant that every number is traceable and no LLM is on the pricing path. Separating *extract → format → render* makes the rules mechanically true and testable (numbers-match-record, no-arithmetic-in-HTML, no-figure-in-prose, byte-identical regeneration) rather than relying on author discipline. Shipping deterministic prose in v1 keeps the whole path reproducible while leaving a clean seam for an LLM drafter on the perimeter (schema-checked, human-signed) per the architecture's agentic-perimeter rule.
- **Alternatives considered:** Let the renderers compute derived figures (e.g. savings, split totals) inline (rejected for *new* figures — that authors numbers outside the core; aggregation of values *already on the record*, like a floor total, is allowed and lives in `viewmodel`, asserted equal to the summed record values). Use an LLM to draft prose in v1 (rejected — adds non-determinism and a number-leak risk to the default path before the human-sign workflow exists; deferred behind the Protocol). Render to PDF/HTML-only (rejected — Markdown for the two documents + one self-contained HTML report is the lightest v1 choice the spec leaves open).
- **Consequences:** Regenerating from the same record is byte-identical (no wall-clock in artifacts — time comes from the record). The `QuoteRecord` does not embed the `SiteModel`, so site-context sections (counterparty/consents/gates) take an optional `SiteModel` alongside the quote, else render *site context not supplied* (see C018). An LLM prose drafter is an additive, perimeter-only future option; it inherits the categorical-only method signatures so it can never originate a figure.

### D13 — Dispatch-energy stays an injected hook (deferred additive promotion); Scenario multipliers confirmed first-class, finance keeps duck-typing
- **Date:** 2026-06-22
- **Decision:** Resolves [OPEN_COMMENTS](shared_work/OPEN_COMMENTS.md) **C010** (the two finance↔cross-lane forward seams). **Part 1 — dispatch energy:** annual charge/discharge volume **stays injected** via the `DispatchEnergy` dataclass and read through the `ResultLike` Protocol (`finance/cashflows.py`); it is **not** promoted to `OptimisationResult` fields now, because its producer (M3 dispatch) does not yet exist. The **agreed eventual home** is two **additive, Optional** fields on the frozen `OptimisationResult` — `annual_charge_kwh: float | None = Field(default=None, ge=0)` and `annual_discharge_kwh: float | None = Field(default=None, ge=0)` — landed **with M3**, jointly by Olly (the producer that defines the real dispatch-output shape) and Oli (the `OptimisationResult` schema owner), as a deliberate, versioned, conformance-tested event (issue 014). At promotion: widen `ResultLike` to read them, keep `DispatchEnergy` as the explicit override/test hook (injected value wins, else read the result), and carry the load-bearing loss-convention in the field descriptions (charge `kWh` already includes round-trip losses by construction; discharge is grossed up by `round_trip_efficiency.ac_round_trip` when only discharge is known). **Part 2 — Scenario:** `Scenario.price_multiplier` (scales revenue / operating cashflows) and `Scenario.cost_multiplier` (scales capex / year-0) are **confirmed** as first-class, validated fields on the scenarios contract (`scenarios/models.py`, `default=1.0`, `gt=0.0`, `allow_inf_nan=False`) with semantics matching finance and `scenarios/engine.py`. Finance **keeps reading them by duck-typing** (`_scenario_multiplier`, `getattr`) and does **not** import/bind the `Scenario` type — preserving the deliberate `value_stack/interface.py` decision to keep `Scenario` opaque (`TypeAlias = Any`). **No schema change in either part.**
- **Reason:** Same precedent as **D9/D10/D11** — keep cross-lane couplings out of the frozen contracts behind an injection hook, with additive promotion as a later, owner-gated option. Promoting the two volumes onto the frozen platform↔engine seam before M3 exists would freeze a *guessed shape/granularity* (single annual scalar vs per-half-hour vs per-horizon-year) into a contract no producer can yet satisfy, and trip the conformance tests against an unmet contract. The current side-channel already fails safe — a missing charge volume makes `levies_on_charge` explicitly *unavailable* with a warning (never zero, never invented) — and is folded into the reproducibility pin via `_hash_input` (the hash key set does not depend on where the value lives). For Scenario, the contract is already first-class and matches; binding the type would re-introduce the cross-lane coupling the architecture intentionally avoids (M5↔M6) for zero correctness gain — the duck-typed read is strictly more tolerant.
- **Alternatives considered:** Promote `annual_charge_kwh`/`annual_discharge_kwh` onto `OptimisationResult` now (rejected — premature; no producer; risks the wrong granularity; contradicts D9/D10/D11). Import/bind the concrete `Scenario` type into finance (rejected — couples M5 to M6's representation; contradicts the `interface.py` `Scenario = Any` stance). Leave C010 unrecorded (rejected — invites re-litigation of "should finance import Scenario?").
- **Consequences:** Baseline pipeline unchanged (zero code change now). When M3 lands: **coordinate the dispatch-energy shape with Olly first** (a cheap conversation, no contract touch) so M3 need not retrofit finance's already-committed two-annual-scalar `DispatchEnergy` shape, then promote the two Optional fields jointly; the `levies_on_charge` and price-to-target lines activate then, and stay honestly *unavailable* until. Note an **intentional asymmetry** in the Scenario seam: finance tolerates `None`/missing/`<0` → `1.0` (with a warning) **and** accepts exactly `0.0`, which `Scenario` forbids via `gt=0.0`; this is deliberate (finance also accepts a bare non-`Scenario` test double and must not crash). Because finance reads by attribute name, a future rename in `scenarios/models.py` would fail silently (`getattr` → `None` → `1.0`); a tiny cross-lane test asserting `Scenario` exposes both attributes with the documented effect is the recommended cheap guard (no type binding).

### D12 — Gate inputs (CM deliverability / Piclo tender / portfolio-MW) are not SiteModel fields; data-ask-only now, a generic GateContext later
- **Date:** 2026-06-22
- **Decision:** Resolves [OPEN_COMMENTS](shared_work/OPEN_COMMENTS.md) **C006**. **None of the three gate inputs goes on `SiteModel`** (reject option a): each fails the "the site's own data" test — (1) CM de-rated **deliverability** to the final contract year at degraded capability is **engine/sizing-derived** (a function of the chosen power/energy/duration × de-rating factors × the degradation curve, run to the final CM year); (2) Piclo **local-flex tender availability** is **time-varying external market state** keyed by DNO area; (3) aggregated-**portfolio MW** for BM/ancillary is **fleet/run-level** (a single-site run has none by definition). **v1/baseline behaviour stays data-ask-only** (option c) — a **zero-diff ratification**: `value_stack/gates.py` already parameterizes all three predicates (`deliverability_confirmed` / `local_flex_tender_open` / `meets_portfolio_threshold`, each `| None` → conservative miss) and `gb_streams.py` passes `None`, so Capacity Market + local-flex report `CONDITIONAL` with unlock conditions and BM/ancillary stay gated-upside `CONDITIONAL`. The **agreed promotion path** (option b), when the producing lanes exist, is to thread the values into the value-stack gate seam via a **new generic, market-agnostic `GateContext`** (e.g. Optional, default-`None` fields `deliverability_confirmed` / `local_flex_tender_open` / `portfolio_mw`) — `check_gates(self, site_model, assumptions, context: GateContext | None = None)`. **All three flow through `GateContext`**, because `check_gates` runs **pre-solve** and never receives an `OptimisationResult` — so even engine-computed deliverability reaches the gate via the context, not via the result. The values go **neither** on `SiteModel`, **nor** in the versioned assumption library (tender/portfolio are per-run/per-date *state*, not £-assumptions — distinct from **C007**, which is about assumption *status* gating), **nor** as a `market == "GB"` core branch (GB meaning is supplied only by `gb_streams.py` wiring).
- **Reason:** Market-agnostic core + frozen-contract discipline + the D9/D10/D11 precedent. `SiteModel` is "the site's own data" and a frozen, hash-pinned platform↔engine seam; putting an engine-derived number, a perishable market flag, or a fleet aggregate on it would corrupt the central abstraction, force a versioned contract bump for non-site state, and — for the weekly-changing tender flag — break reproducibility (a re-run of the same site would change as the market moved). `connection.dno_region` is legitimately site-intrinsic and already present (nested on `ConnectionDetails`) — proof the boundary is drawn correctly: the site's *location* is its data; the *tender state at that location* is not. `GateContext` is the injection-hook instance of the same pattern the team chose three times (keep non-core facts out of the frozen seams; promote additively later, owner-gated), and `gates.py` is already built for it — the predicates already take exactly these three nullable inputs.
- **Alternatives considered:** Capture on `SiteModel` (rejected — none is site-intrinsic; corrupts the abstraction, triggers a frozen-contract bump, and breaks reproducibility for the tender flag). Put tender/portfolio in the assumption library (rejected — per-run/per-date state, not versioned £-assumptions; would break the "a quote is valid only against its assumption version" rule). Surface deliverability on `OptimisationResult` as the gate's source (rejected as the *gate-input* home — the pre-solve gate never sees the result; an `OptimisationResult` deliverability field, if ever added, is for downstream **reporting** only, a separate optional owner-gated event). Leave permanently data-ask-only with no named path (rejected — names no home for when the lanes land).
- **Consequences:** `SiteModel` and `OptimisationResult` untouched today; CM + local-flex contribute **zero priced revenue** until the producing lanes (the sized engine M3/M4, a confirmed Piclo source, multi-site/portfolio runs) land — correct and honest, no invented data. The future `GateContext` is **behaviourally additive** (default `None` reproduces today's gates exactly) but is a **signature change to M2's own `ValueStream` Protocol** (Olly's core interface, not one of the two frozen platform↔engine seams), so it is a deliberate, coordinated, conformance-tested edit across `interface.py`/`streams.py`/`gb_streams.py`/tests — done only when a caller can populate it (gated by **C007**'s source confirmations), not speculatively. **Forward-wiring note:** `meets_portfolio_threshold` needs **both** `portfolio_mw` (from `GateContext`) **and** `threshold_mw` (a min-MW key sourced from the assumption library) — both must be supplied or it keeps returning `False` even with a real portfolio; `gb_streams.py` currently hardcodes both to `None`. The pre-solve CM gate is **indicative/`CONDITIONAL` by design**; authoritative CM firmness comes only after sizing — document so consumers don't read the pre-solve `CONDITIONAL` as final.

### D11 — Manual benchmark is an `event_type` string marker, not a schema field
- **Date:** 2026-06-22
- **Decision:** A hand-built control-group quote is recorded as `TelemetryEvent(event_type="manual_benchmark")` (the constant `MANUAL_BENCHMARK_EVENT_TYPE` in `telemetry/manual_benchmark.py`). **No** boolean `manual_benchmark` field is added to the frozen `TelemetryEvent` schema. `event_type` is intentionally a free-form `str` in v1 (no enum), so the marker needs no schema change.
- **Reason:** Resolves the immediate part of [OPEN_COMMENTS](shared_work/OPEN_COMMENTS.md) C011 with no contract change. The System Brief phrases it as "manual_benchmark = true", but a string marker on the existing free-form `event_type` carries the same signal in the shared dataset without bumping the `TelemetryEvent` contract.
- **Alternatives considered:** Add a first-class boolean/flag (or a dedicated enum) to `TelemetryEvent` — a versioned, additive contract change owned by the quotes-telemetry/X.3 lane.
- **Consequences:** The conversion helper (`ManualBenchmarkRecord` → `TelemetryEvent`) keeps working unchanged. If the X.3 lane later wants a first-class flag, that remains an additive, versioned option (not blocked by this decision).

### D10 — Tier-boundary constants stay labelled structural constants in ingestion
- **Date:** 2026-06-22
- **Decision:** The data-tier grading constants (`T2_MIN_PERIODS = 17520`, `T2_PERIOD_TOLERANCE`, `T2_MIN_COMPLETENESS_PCT = 90.0`, `T1_MIN_PERIODS`) stay as **labelled structural constants** in `ingestion/data_tier.py` — **not** in the assumption library.
- **Reason:** Resolves the immediate part of [OPEN_COMMENTS](shared_work/OPEN_COMMENTS.md) C013. They are calendar/structural and carry no money (no £/% enters any output), and `SiteModel`'s T2 check already assumes the 17 520 bar — same precedent as `BASELINE_POWER_FRACTION`. The 23 assumption-library sections are frozen and none is a grading section.
- **Alternatives considered:** Add a grading/completeness section to the assumption library (a versioned contract change) so tier grading becomes a versioned decision; if taken, ingestion would read the floor/tolerance via `load_assumptions(version)`.
- **Consequences:** Tier grading is deterministic and lives next to the code that uses it. Promoting the completeness floor/tolerance into X.1 remains a later, owner-gated option, not a blocking question.

### D9 — Projection horizon (`DEFAULT_HORIZON_YEARS`) stays a labelled structural model parameter
- **Date:** 2026-06-22
- **Decision:** `DEFAULT_HORIZON_YEARS = 15` (`finance/cashflows.py`) stays an explicit model parameter with a labelled default that **warns when defaulted** — **not** an assumption-library £-value.
- **Reason:** Resolves the immediate part of [OPEN_COMMENTS](shared_work/OPEN_COMMENTS.md) C009. The horizon injects no £/% into any output (it sets the projection length, not a price), so it is structural, not an underwriting assumption; and the 23 frozen library sections include no horizon/indexation section. Indexation/CPI is likewise not applied in v1 (no assumption exists — not invented).
- **Alternatives considered:** Add a horizon/indexation section to the assumption library (a versioned contract change); keep it a confirmed model parameter (chosen).
- **Consequences:** The horizon is overridable per run and warns when left at the default. Adding a versioned horizon/indexation section to X.1 remains a later option; M5 owns the multi-year extension (degradation, throughput decay, indexation) reading the relevant X.1 keys.

### D8 — Ratify the implemented Pydantic schemas as the frozen v1 data contracts
- **Date:** 2026-06-22
- **Decision:** The implemented models in `src/gridq/schemas/` are the **source of truth** and are ratified as the **frozen v1 contracts**. `docs/03_data_contracts.md` was rewritten to mirror them field-for-field. Resolves [OPEN_COMMENTS](shared_work/OPEN_COMMENTS.md) **C002** and **C004**.
- **Reason:** The doc had drifted to an older shape (`import_capacity_kva`/`asc_band_kva`; `chosen_spec`/`value_by_stream`/`cost_by_stream`/`meta`/`conditional_upside`/`solved_status`; per-stream `tag`+`exposure`; `lifecycle_status`/`versions{}` on `QuoteRecord`; and a `total_value` field) while the green, tested schemas use `mic_kw`/`mec_kw`/`asc_kw` (kW), flat `selected_power_kw`/`selected_energy_kwh`/`duration_hours`, `value_streams[]` with a `firmness` enum, `gated_upside[]`, `solver_status`/`solver_metadata`, and `quote_mode` with flat hashes. Every Wave-2 lane reads this doc; leaving names that no longer exist would mis-direct downstream code. C004 (the additive `SolverStatus.INSUFFICIENT_DATA`) folds into this reconciliation.
- **Alternatives considered:** Keep the doc's older shape and change the code to match (rejected — the schemas are green, tested, and depended on); leave the doc stale with a pointer (rejected — too easy to code against dead names).
- **Consequences:** `SiteModel` and `OptimisationResult` (with its `MarketStream` registry) are the frozen platform↔engine seams; a change is a deliberate, versioned event that trips the conformance tests (issue 014). Code-first from here: any field edit lands in `schemas/` first and the doc is updated in the same change. No code was changed in this ratification (doc + decision log only).

### D7 — Minimal typer CLI in the scaffold
- **Date:** 2026-06-16
- **Decision:** Ship a tiny `gridq` CLI (`version`, `info`) with no pricing logic.
- **Reason:** Gives the command table a real `run`, exercises the `typer`/`rich` deps, and proves the install works end to end.
- **Alternatives considered:** No CLI (run via `pytest` only); a `Makefile` target; a full CLI now.
- **Consequences:** A `gridq` console script exists; commands grow as modules land; the module list in `cli.py` must be kept in sync with the packages (small surface).

### D6 — No separate `telemetry/` or `core/` package yet
- **Date:** 2026-06-16
- **Decision:** Scaffold exactly the requested package list. `TelemetryEvent` is a schema in `schemas/`; telemetry *capture* (X.3) folds into `quotes/`; cross-cutting hashing/versioning utils fold into `provenance/`.
- **Reason:** Matches the requested layout and avoids empty packages.
- **Alternatives considered:** Create `core/` and `telemetry/` now (per architecture §3); fold everything into fewer packages.
- **Consequences:** Slightly diverges from the architecture §3 naming; promote to their own packages if they grow. *Revisit at Milestone 5.*

### D5 — Changelog canonical location → `docs/changelog.md`
- **Date:** 2026-06-16
- **Decision:** `docs/changelog.md` is canonical (history ported); the top-level `CHANGELOG.md` is a pointer; `CLAUDE.md` references it.
- **Reason:** The rest of the project record lives under `docs/`; one changelog, not two.
- **Alternatives considered:** Keep the top-level `CHANGELOG.md` canonical; maintain both.
- **Consequences:** Links to a top-level `CHANGELOG.md` still resolve via the pointer; contributors add entries in `docs/changelog.md`.

### D4 — Storage: files first, SQLite only if needed (Milestone 0 storage decision)
- **Date:** 2026-06-16
- **Decision:** v1 stores SiteModels, quote records and assumptions as files (JSON/YAML). Introduce SQLite only when quote-lifecycle querying/concurrency demands it; Postgres later in production.
- **Reason:** Resolves spec Open Q1 and the Milestone 0 storage gate; keeps v1 simple and reproducible. **Blocks** the Milestone 2 run-record format, so it is fixed now and not relitigated mid-build.
- **Alternatives considered:** SQLite from day one; Postgres; a document DB.
- **Consequences:** File-based run records/assumptions; a migration to SQLite is a known, scoped later step; the Milestone 2 record format can be fixed without re-deciding storage.

### D3 — Dependencies; solver stack deferred
- **Date:** 2026-06-16
- **Decision:** Runtime deps `pydantic`/`pandas`/`numpy`/`pyyaml`/`rich`/`typer`; dev deps `pytest`/`ruff`/`mypy`. The solver stack (`linopy`/`highspy`) is **not** installed in v1.
- **Reason:** The deterministic **mock** engine needs no solver; the real MILP (Olly, issue 014) adds it at its milestone. Keeps the v1 install light.
- **Alternatives considered:** Install the solver stack now; a minimal-deps build (no `rich`/`typer`).
- **Consequences:** v1 installs fast; the real engine later pulls in the solver; CLI/UX deps are available now.

### D2 — `src/`-layout, setuptools build backend
- **Date:** 2026-06-16
- **Decision:** Package under `src/gridq/`, built with setuptools; `pip install -e ".[dev]"` for development.
- **Reason:** `src/`-layout prevents accidental imports of the un-installed tree and is the well-trodden path; setuptools is universally available.
- **Alternatives considered:** Flat layout; hatchling / poetry / pdm build backends.
- **Consequences:** Must `pip install -e .` to import; standard, portable packaging that any tool understands.

### D1 — Python is the first implementation layer
- **Date:** 2026-06-16
- **Decision:** The numerical/domain core is a Python package.
- **Reason:** Matches the committed solver path (linopy/HiGHS), Pydantic contracts, and a Claude-Code-friendly workflow (architecture §2).
- **Alternatives considered:** A TypeScript/Node core; Rust/Julia for the solver; a polyglot core.
- **Consequences:** One language for core + perimeter glue; a Next.js/React frontend is added only after the contracts and outputs are stable; the HiGHS/linopy ecosystem is available.
