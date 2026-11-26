# Frozen interface contracts (`gridq.contracts`)

Cross-workstream seams from spec §10A, frozen as versioned pydantic models with committed JSON
Schemas and conformance tests (`tests/contracts/`). Roadmap: WP-BO-01 (IC-4), WP-CONN-01 (IC-1), WP-CONN-03
(IC-2), WP-PLAT-05 (NEW-IC-13), WP-THERM-02 (NEW-IC-11), WP-BO-06 (IC-4 1.1.0, and IC-1, IC-2 and
IC-11 to 1.1.0 with it), WP-GRC-09 (IC-4 1.2.0 and NEW-IC-15), WP-SIZE-07 (IC-3), WP-EDGE-01 (IC-5), WP-SITE-07 (IC-9), WP-TEL-01 (NEW-IC-14).

| Contract | Module | Version | JSON Schema | Git tag |
|---|---|---|---|---|
| IC-4 `EvidenceObject` | `evidence.py` | **1.2.0** (reads 1.1.0 and 1.0.0) | `schemas/ic4_evidence_object.v1.2.0.schema.json`; archived: the `v1.0.0` and `v1.1.0` files | `contracts/ic4-evidence-object/v1.2.0` (on the commit that completes WP-GRC-09); `v1.1.0` and `v1.0.0` exist |
| IC-1 `ConnectionAgreement` | `connection.py` | **1.1.0** (reads 1.0.0) | `schemas/ic1_connection_agreement.v1.1.0.schema.json`; archived: `schemas/ic1_connection_agreement.v1.0.0.schema.json` | `contracts/ic1-connection-agreement/v1.1.0` (exists); `v1.0.0` exists |
| IC-2 `CurtailmentScenarioSet` | `curtailment.py` | **1.1.0** (reads 1.0.0) | `schemas/ic2_curtailment_scenario_set.v1.1.0.schema.json`; archived: `schemas/ic2_curtailment_scenario_set.v1.0.0.schema.json` | `contracts/ic2-curtailment-scenario-set/v1.1.0` (exists); `v1.0.0` exists |
| IC-13 `IdentityClaims` + `AuditEvent` (roadmap NEW-IC-13) | `identity.py`, `audit.py` | **1.0.0** (unchanged) | `schemas/ic13_identity_claims.v1.0.0.schema.json`, `schemas/ic13_audit_event.v1.0.0.schema.json` | `contracts/ic13-identity-audit/v1.0.0` (exists) |
| IC-11 `ThermalFlexAvailability` + `FlexInstruction` + `FlexAcknowledgement` (roadmap NEW-IC-11) | `thermal_flex.py` | **1.1.0** (reads 1.0.0) | `schemas/ic11_thermal_flex_availability.v1.1.0.schema.json`, `schemas/ic11_flex_instruction.v1.1.0.schema.json`, `schemas/ic11_flex_acknowledgement.v1.1.0.schema.json`; archived: the three `v1.0.0` files | `contracts/ic11-thermal-flex/v1.1.0` (exists); `v1.0.0` exists |
| IC-3 `DispatchEnvelope` + compiled `ResolvedTimeline` (roadmap WP-SIZE-07) | `dispatch_envelope.py` | **1.0.0** | `schemas/ic3_dispatch_envelope.v1.0.0.schema.json`, `schemas/ic3_resolved_timeline.v1.0.0.schema.json` | `contracts/ic3-dispatch-envelope/v1.0.0` |
| IC-5 edge ↔ cloud protocol: `CloudToEdgeFrame` + `EdgeToCloudFrame` + `LinkProfile` (roadmap WP-EDGE-01) | `edge_cloud.py` | **1.0.0** | `schemas/ic5_cloud_to_edge_frame.v1.0.0.schema.json`, `schemas/ic5_edge_to_cloud_frame.v1.0.0.schema.json`, `schemas/ic5_link_profile.v1.0.0.schema.json` | `contracts/ic5-edge-cloud/v1.0.0` |
| IC-9 `ComputeTurndownDeclaration` + `ResolvedTimeline` (roadmap WP-SITE-07, B8-01) | `compute_turndown.py` | **1.0.0** | `schemas/ic9_compute_turndown_declaration.v1.0.0.schema.json`, `schemas/ic9_resolved_timeline.v1.0.0.schema.json`; specification `specs/ic9_compute_turndown_declaration.v1.0.0.md` | `contracts/ic9-compute-turndown/v1.0.0` (annotated, local) |
| IC-12 chassis: `SiteModel` + `OptimisationResult` + `QuoteRecord` (roadmap NEW-IC-12; release `gridq_chassis_v3`) | `gridq/schemas/chassis.py` (models in `gridq.schemas`) | **3.1.0** (WP-UW-17), an additive minor of 3.0.0, which was rendered on 2.1.0 (reads 1.0.0, 2.0.0, 2.1.0 and 3.0.0; 1.0.0 instances are valid 2.0.0 instances, same bytes; 2.0.0 instances are valid 2.1.0 instances; 2.1.0 `QuoteRecord` and `OptimisationResult` instances are valid 3.0.0 instances, same bytes; a 2.x site is lifted only by `upgrade_site_2_to_3`, with its tariff basis and provenance stated; 3.0.0 instances are valid 3.1.0 instances, same bytes) | `schemas/ic12_site_model.v3.1.0.schema.json`, `schemas/ic12_optimisation_result.v3.1.0.schema.json`, `schemas/ic12_quote_record.v3.1.0.schema.json`; archived: the three `v1.0.0`, the three `v2.0.0`, the three `v2.1.0` and the three `v3.0.0` files | `contracts/ic12-chassis/v3.1.0` (on the integration merge of WP-UW-17); `v3.0.0` (on the integration commit that renders 3.0.0 over 2.1.0); `v2.1.0`; `v2.0.0`; `v1.0.0` |
| IC-14 telemetry series: `SeriesDescriptor` + `TelemetryBatch` + `CuratedSeries` + `DqAnnotation` (roadmap NEW-IC-14, WP-TEL-01) | `telemetry_series.py` | **1.0.0** | `schemas/ic14_series_descriptor.v1.0.0.schema.json`, `schemas/ic14_telemetry_batch.v1.0.0.schema.json`, `schemas/ic14_curated_series.v1.0.0.schema.json`, `schemas/ic14_dq_annotation.v1.0.0.schema.json`; specification `specs/ic14_telemetry_series.v1.0.0.md` | `contracts/ic14-telemetry-series/v1.0.0` (annotated, local) |
| IC-15 `ContractTemplateSet` + `ExecutedContractTerms` (roadmap NEW-IC-15) | `contract_templates.py`, texts and manifest in `templates/` | **1.0.0** | `schemas/ic15_contract_template_set.v1.0.0.schema.json`, `schemas/ic15_executed_contract_terms.v1.0.0.schema.json` | `contracts/ic15-contract-template-set/v1.0.0` (on the commit that completes WP-GRC-09) |

A re-versioned module states `SUPPORTED_VERSIONS = ("1.0.0", "1.1.0")`: it reads both and writes the
last; IC-4 states `("1.0.0", "1.1.0", "1.2.0")` and IC-12 `("1.0.0", "2.0.0", "2.1.0", "3.0.0", "3.1.0")`.
Nineteen archived files are kept byte for byte and pinned by SHA-256 (`__main__.ARCHIVED`): the six
`v1.0.0` files, IC-4 `v1.1.0`, the three IC-12 `v1.0.0` files, the three IC-12 `v2.0.0` files
(superseded by the additive minor 2.1.0, WP-UW-03, D79), the three IC-12 `v2.1.0` files
(superseded by the major 3.0.0, WP-UW-07, D103/D104) and the three IC-12 `v3.0.0` files
(superseded by the additive minor 3.1.0, WP-UW-17, D128 to D132; released as 2.2.0 on its branch,
which was cut before 3.0.0). IC-12's models are not in this package:
they are the chassis in `gridq.schemas`, and only the checker reads them. The four `v1.1.0` tags
are annotated and sit on one commit, the one that completes WP-BO-06 on branch
`roadmap/wp-bo06`. The IC-4 `v1.2.0` and IC-15 `v1.0.0` tags sit on the commit that completes
WP-GRC-09 on branch `roadmap/wp-grc09-templates`. The IC-3 `v1.0.0` tag is annotated and sits on
the commit that completes WP-SIZE-07 on branch `roadmap/wp-size07-ic3`. The IC-12 `v3.0.0` tag is
annotated and sits on the integration commit on `roadmap/p1-foundations` that renders 3.0.0 over
2.1.0. The IC-12 `v3.1.0` tag is annotated and sits on the integration merge of
WP-UW-17 on `roadmap/p1-foundations`.

IC-1 depends on IC-4: a real agreement's `source` is an `EvidenceBacked` reference to the IC-4 hash
of its signed document. IC-2 depends on IC-4 (`IssuerRole` for publishers, `EvidenceRef` for
registered responses) and on IC-1 (by hash). IC-13 depends on nothing but `_base`. IC-11 depends on
IC-4 (`EvidenceRef` for trial, meter, baseline-method and SLA documents; `IssuerRole` for the party
an instruction serves). IC-15 depends on IC-4 through `EvidenceRef` alone: it holds IC-4 types and
roles as strings checked against the live enums. IC-3 depends on IC-1 (its agreement, by hash, and
the limit check against it), IC-2 (by hash), IC-4 (`EvidenceRef` for a service's evidenced basis,
`IssuerRole` for its counterparty), IC-11 (the surface by hash, its binding band, and
`deliverable_kw` for the surface check) and IC-12 (the assessment quote by id and input hash, any
`2.x`: IC-3 1.0.0 froze while IC-12 was at major 2, so binding a quote written at IC-12 3.0.0
needs an IC-3 minor that widens the range; no producer writes an envelope yet). Every reference to another contract accepts any `1.x` version of it (IC-3's to IC-12:
`2.x`). IC-5 depends on IC-3 (`EnvelopeRef`, the
compiled timeline it checks schedules against, and the whole envelope in an `envelope_install` frame, which
brings IC-4's `EvidenceRef` and `IssuerRole` with it), IC-1 (the agreement by `ConnectionAgreementRef`, and its
ANM response time for `verify_anm_response`) and IC-13 (`GENESIS_PREV_HASH`). So an
IC-4 minor re-versions a dependant only when it changes what that dependant's schema embeds (the
cost table under "The reference pin" below).

The roadmap places contracts under `contracts/<ic>/` at the top of `Neura GitHub/`. Like IC-1, IC-2,
IC-4 and IC-13, IC-11, IC-3 and IC-5 live in this package instead, so all frozen contracts share one base, one
canonical hash and one `--check`.

## Rules every contract here follows

- **Frozen, strict and closed.** Instances are immutable; no string-to-number coercion; unknown
  fields are refused; NaN and infinity are refused. `EvidenceObject` and `ConnectionAgreement`
  cannot be subclassed.
- **No `None`, anywhere.** A value that exists but is not known is `Unknown(kind="unknown",
  reason=...)`. A term that does not exist for the record is `NotApplicable(...)`. Both require a
  reason. A test walks every field annotation to prove no `None` is admitted.
- **Versioned on the instance.** Every payload carries `contract` and `contract_version`. A
  contract's own version is an exact, enumerated literal (`SUPPORTED_VERSIONS`), so a payload of
  another version is refused rather than misread. A *reference* to another contract carries a
  version in the `1.x` range (`_base.Version1x`).
- **Down-level rule.** An instance labelled with an earlier version may hold only what that version
  allowed: a 1.0.0 label carries only 1.0.0 members and 1.0.0 references, so the label stays honest
  for a 1.0.0 reader. The error says `not in IC-<n> 1.0.0`.
- **Referenced by hash.** `content_hash()` is `sha256:` plus the hex SHA-256 of the UTF-8 bytes of
  the canonical JSON:
  `model_dump(mode="json")`, then strings normalised to Unicode NFC and `-0.0` changed to `0.0`, then
  `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`.
  Floats use Python's shortest round-trip repr. This is GridQ's `provenance.hashing` convention plus
  the normalisation step. It is not RFC 8785. Golden hashes for every fixture are pinned in
  `tests/contracts/_contracts_support.py`. Tests prove the hashes do not change with key order,
  whitespace, int-vs-float spelling, Unicode normalisation form or `PYTHONHASHSEED`.
- **No clock.** Validity (`EvidenceObject.status_at`, `assess`, `validate_token`) is always computed
  as of an explicit date or instant.
- **Instants are UTC.** `UtcInstant` (`_base.py`, added with IC-2/IC-13) refuses a naive time and
  stores every instant in UTC, so `01:00+01:00` and `00:00Z` are one value and one hash.

## IC-4: what "evidence" means here

An evidence object is a **document, hashed**, and by default a **third-party** document (ADR-D10
option b). These rules enforce it:
- **Who issues.** `issuer.role` is an `IssuerRole` or the single `SelfIssuerRole` value
  `neura_self_issued`. `IssuerRole` holds 22 third-party roles: the sixteen of 1.0.0 plus
  `legal_adviser`, `certification_body`, `data_provider`, `funder`, `research_partner` and
  `independent_verifier`. It has no Neura member. A claim Neura authors is an assumption or a
  derived record, never evidence, with one restricted exception.
- **The restricted Neura issuer.** Neura may issue only its pre-registration and decision records:
  `SELF_ISSUABLE_TYPES` is `baseline_method`, `trial_record` and `decision_record` (the owner widened
  ADR-D10 to decision records on 2026-11-23). Third parties may issue these types too. The marker is
  the role value, on the wire, so the hash covers it. Rules:
  - R3: `neura_self_issued` on any other type is refused;
  - R4: Neura cannot verify its own record (`verifier_role: neura_internal` is refused on a
    self-issued record): it stays unverified, or an independent third party countersigns it;
  - R5: a self-issued decision record names its decision (`issuer.reference` is the decision's own
    id, for example the ADR id, never `unknown`);
  - R6: it states when it takes effect (`effective_from` is a date, never `unknown`).

  Neura may still *verify* a third party's document.
- A document hash of zero bytes is refused.
- Payloads that carry assumption fields (`status`, `derivation_method`, `confirmation_ask`, ...) or
  assumption status tokens (`assumed_approved`, `pending_permission`, `placeholder`, ...) are
  refused with the message "an assumption is not evidence".
- GridQ `DerivedAssumption` and `Assumption` instances are refused, and so are their dumps.
- `serialise_evidence` accepts only an exact `EvidenceObject`, both statically (mypy; tested) and at
  runtime. It also re-validates, which catches a `model_construct` bypass.
- **Types.** 1.1.0 appends seventeen types to the fifteen of 1.0.0: data-processing, data-sharing
  and non-disclosure agreements, legal opinion, conformity certificate, technical report, data
  licence, method agreement, service-level agreement, commissioning record, control-authority
  sign-off, FMAR registration, metered and operational data records, baseline method, trial record
  and decision record. 1.2.0 (WP-GRC-09) appends five contract types: assessment statement of
  work, master services agreement, site access and installation agreement, lender reliance letter
  and performance wrapper agreement. There is no assumption-like member. `IC4_INVENTORY.md` maps
  every evidence need in the roadmap to a type and an issuer role, with the remaining gaps (G3 to
  G8) and their owners.
- **Down-level (R1, R2).** An object labelled 1.0.0 may hold only a 1.0.0 type and a 1.0.0 role, so
  a self-issued record can never be labelled 1.0.0. A 1.1.0 reader reads every 1.0.0 object with the
  same meaning and hash. A 1.0.0 reader refuses a 1.1.0 object, which is correct: it has no notion
  of self-issued and no `require_third_party`.
- **Down-level (R7, 1.2.0).** An object labelled 1.1.0 may hold only a 1.1.0 type. 1.2.0 adds no
  role, so no role rule is needed.

**Refusal is the default.** The consumer functions:
- `require_third_party(obj)` returns `obj` when a third party issued it and raises
  `SelfIssuedEvidenceRefused` when Neura did. It accepts only an exact `EvidenceObject` and judges
  it as the wire would carry it (re-validated from its canonical JSON), so a `model_construct` swap
  is caught.
- `resolve_ref(ref, objects, *, expected_type=None, admit_self_issued=False,
  include_synthetic=False)` finds the object a reference points at. It checks, in this order:
  0. `admit_self_issued=True` without an `expected_type` is a `ValueError`, raised before any
     lookup: admission is per type, so a decision record can never sit in a baseline-method slot;
  1. no object has the referenced hash: `EvidenceNotFound`;
  2. the object's version is not the one the reference states: `EvidenceRefused`;
  3. the object's type is not `expected_type`, when given: `EvidenceRefused`;
  4. a synthetic fixture, unless `include_synthetic`: `SyntheticEvidenceRefused`;
  5. a self-issued record, unless admitted: `SelfIssuedEvidenceRefused`;
  6. otherwise it returns the object.
- `assess(..., admit_self_issued=False)` skips a self-issued match unless admitted, exactly as it
  skips a synthetic fixture. `EvidenceCheck.best_is_self_issued` lets a consumer that admits one
  label its result.
- `evidence_ref(obj)` states the object's own version, so a reference to a 1.0.0 object says
  `1.0.0`.

**Who may admit a self-issued record.** This table is the consumer rule; the functions enforce its
default. Admitting outside it widens ADR-D10, which is the owner's decision.

| Slot or use | Self-issued admitted? |
|---|---|
| Registering or looking up a baseline method (WP-LEDGER-04, WP-THERM-09) | Yes, explicitly |
| IC-11 `baseline_method`, and `trial_evidence` of a `trialled` surface | Yes, explicitly; any output that cites it prints "self-issued by Neura" |
| IC-11 `meter_evidence`, SLA `evidence`, `calibration_evidence` | No |
| IC-1 `source`; every IC-2 reference | No |
| Eligibility (`underwriting_basis_v1`), the DPA guard, lender packs, the claims register | No |
| Recording, looking up or displaying Neura's own decision (WP-GRC-07), by type `decision_record` | Yes, explicitly; the output prints "self-issued by Neura" |
| Using a decision record to open a gate, satisfy a consent, price a stream, mark a claim true, or support a lender pack | No |
| Anything not listed | No |

A decision record is evidence that Neura decided something on a date. It is never evidence that the
thing decided is true, or that a third party agreed to it.

For the DPA guard (WP-GRC-05), IC-4 1.1.0 offers the `data_processing_agreement` and
`data_sharing_agreement` types, `assess()` with its 1.0.0 call shape, and a synthetic DPA fixture.
A DPA can never be self-issued (R3). The guard itself is WP-GRC-05's.

**Expiry.** An expired document **validates**, and reports `EvidenceStatus.EXPIRED`. `assess()`
returns `ABSENT` only when no object of that type covers the scope. `ExpiresOn.expires_on` is
exclusive: the evidence is valid strictly before that date.

**Synthetic fixtures.** `record_class="synthetic_fixture"` marks schema-test data. `assess()`
ignores these objects and `resolve_ref` refuses them unless `include_synthetic=True`, and consumers
(A6, packs) must refuse them.

## The reference pin: a `1.x` range (design note, WP-BO-06)

**A reference accepts a stated compatible range, any `1.x.y`. A contract's own version stays an
exact, enumerated literal.** This holds for `EvidenceRef`, `ConnectionAgreementRef`,
`ScenarioSetRef` and `AvailabilityRef` (`_base.Version1x`, pattern
`^1\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`).

Reasons:
1. **The hash is the binding, not the version.** The referenced hash covers the object's own
   `contract_version`. The version inside a reference is a claim nobody can check without the
   object, so an exact pin there protects nothing.
2. **An exact pin would orphan evidence.** If IC-1 1.1.0 could reference only IC-4 1.1.0 objects,
   a connection offer registered at 1.0.0 could not be cited without re-registering it under a new
   hash.
3. **Safety lives at dereference.** Whether an object is self-issued is a property of the object.
   `resolve_ref` refuses a version mismatch and a self-issued object in the one place where the
   object is in hand.
4. **Cost.** More IC-4 minors are already known (inventory gaps G6, G7 and G8; G2 was closed by
   1.2.0). With an exact pin each would re-version IC-1, IC-2, IC-11, IC-15, and later IC-12 and
   IC-8 as well.

A pattern, not the list `["1.0.0", "1.1.0"]`: a list changes with every IC-4 minor, which is the
cascade again.

**A 1.0.0 reader refuses a 1.1.0 object; a 1.1.0 reader accepts a 1.0.0 one,** with the same meaning
and hash. A 1.0.0 reader of IC-1, IC-2 or IC-11 likewise refuses a 1.1.0 instance, and refuses a
reference whose version is not `1.0.0`.

**Cost of the next IC-4 minor, stated now:**

| The next minor adds | Contracts re-versioned |
|---|---|
| Evidence types only | IC-4 |
| A self-issuer role or a wider `SELF_ISSUABLE_TYPES` | IC-4 (and an owner decision: it widens ADR-D10) |
| A third-party issuer role | IC-4, IC-2 and IC-11, because their schemas embed the `IssuerRole` list. Not IC-1, not IC-15 |
| A field on `EvidenceObject` | Major. Every 1.x hash would change |

IC-4 1.2.0 (WP-GRC-09) was the first row: five types, IC-4 alone. IC-15 embeds only `EvidenceRef`
(`test_ic15_embeds_no_ic4_enum_so_a_types_only_minor_re_versions_ic4_alone`), so it joins no row
except a major. Each such release also appends one string to `SUPPORTED_VERSIONS` and to the
model's own version literal. `test_ic4_pin_and_inventory.py` fails if any contract module other than `evidence.py` pins
an IC-4 version, and requires the range to admit `evidence.CONTRACT_VERSION`.

## IC-1: spec A1-01 field map

`connection.A1_01_FIELDS` maps each A1-01 phrase to its field (`IC1_EXTRA_FIELDS` holds the §10A
additions). Main cross-field checks:
- the archetype agrees with the firm and non-firm MW;
- profile blocks do not overlap and do not exceed the agreed capacity;
- curtailment limits are bounded (8,784 h or 100 %);
- there is one LIFO position per constraint;
- a non-firm agreement has either an ANM signal or a profile.

These checks run only on known values. An `Unknown` is never guessed at.

## IC-2: what a scenario set guarantees

- **Weights are exact.** Each weight is `{numerator, denominator}` in lowest terms, and the weights
  must sum to exactly 1 in rational arithmetic, with **no tolerance**. Equal weights over three
  observed periods are 1/3, which no float represents exactly. A tolerance would let 0.999999
  validate, and two consumers could disagree about it. Integers have one JSON form, so weights
  never perturb the hash.
- **Series are step-encoded.** The encoding is unique: first step at index 0, indices strictly
  increasing, adjacent values different. `RestrictionSeries.from_dense` and `dense()` convert.
  A year of minute data is a few steps, not 525,600 numbers.
- **Self-consistent.** The minute series averages to the half-hourly series (1e-9 MW). Every
  restricted interval is covered by a listed event. Series energy equals event energy (1e-6 MWh
  plus 1e-9 relative). Events are sorted, do not overlap, and lie inside the period. Scenarios
  are sorted by id. Depth is at most a known reference capacity.
- **Evidence class is explicit.** `evidence_class` is `replayed_real_data`, `modelled` or
  `synthetic_fixture`, and it must match `provenance`:
  - replayed data: `PublishedSource` per recorded response, with URL, retrieval time, response
    SHA-256, licence, attribution and distribution gate, plus an IC-4 `EvidenceRef` once
    registered;
  - modelled data: engine, version, `input_hash`, seed.
- **Signal basis.** `signal_basis` states whether the history is generation-export curtailment,
  demand-import restriction or loading headroom (spec A1-04's data risk).
- **Consumers need no engine.** `expected_series` and `expected_energy_mwh` use exact weights and
  import nothing outside `gridq.contracts`. A test proves this in a fresh interpreter.

## IC-13: identity claims and audit events

- **Tokens.** JWT (RFC 7519) in JWS compact form, signed **RS256**, with keys as a JWK Set.
  - `validate_token` verifies offline with the standard library (RFC 8017 §8.2.2, comparing the
    full re-encoded block in constant time). GridQ's runtime dependencies stay pydantic-only.
    The tests cross-check against `cryptography` in both directions when it is installed.
  - Refused: any other `alg` (including `none` and HS256 key confusion), keys under 2048 bits or
    with `e != 65537`, unknown `kid`, unknown header fields, duplicate JSON keys,
    non-canonical base64url, and use outside `nbf <= at < exp`.
  - Lifetime is at most 12 h.
- **Roles** (spec E-04): `neura_ops`, `customer`, `lender_readonly`, `verifier`, one per token,
  each tied to an organisation kind. The claims model enforces `ROLE_ACTIONS`:
  - lender and verifier are read-only;
  - only Neura ops may `control_write`, and only on a `device`;
  - a control-write scope needs `amr: mfa`;
  - a customer's scopes stay inside its own organisation;
  - only Neura ops may use a wildcard organisation.
- **No personal data.** Claims carry opaque ids only; there are no name or e-mail claims.
- **Audit events.** One `AuditEvent` per customer-data read or control write, allowed or denied.
  - Each event carries the actor (from the token), the scope that allowed it (or a reason for
    denial), the target (plus the `ControlCommand` for a write), the action, `occurred_at`, `seq`
    and `prev_hash`.
  - The events form a hash chain. `verify_chain` finds edits, deletions, reordering, replays and
    time going backwards.
  - Truncating the tail is found only against an anchored head hash. Anchoring the head is
    WP-PLAT-06's job.
  - `audit_access` authorises the action and returns the event to append.
- **Fixture keys are refused by default.** The committed key pair
  (`ic13_TEST_ONLY_signing_key.jwk.json`, `ic13_TEST_ONLY_jwks.json`) has a `kid` starting
  `TEST-ONLY-`, and `validate_token` refuses it unless `allow_fixture_keys=True`. It is public
  test data and never a secret. Signing lives only in `tests/contracts/_ic13_fixtures.py`.

## IC-11: thermal flex availability, instruction and acknowledgement

Spec A4-03 and TF-04 cross four workstreams, and §10A has no contract for them (roadmap NEW-IC-11).
Producer: A4 `thermal_plant_model` (WP-THERM-03) and ThermaFlex DC. Consumers: the adequacy
simulator (WP-SITE-04), the constraint library and IC-3 (WP-SIZE-02/07), forecasts and joint
dispatch (WP-DISP-01/03), the baseline method (WP-THERM-04) and the Atlas scene (WP-ATL-07).

Design decisions, each with its reason:
- **The surface is seasons x time-of-day bands x duration.** `seasons` partition the twelve months;
  `bands` partition the local day as half-open minutes `[start, end)` on `Europe/London`; `cells`
  hold one entry per (season, band) in a fixed order. A4-03 asks for flex "by time of day and
  season", the GB tariff windows GridQ already bands are on the local clock, and a complete grid
  means a gap is an explicit `unknown`, never a missing row. A cell's kW is indexed by the
  **start** of an event ("from 00:15 the site can hold ..."), as the Cardiff study in A4-03 states
  it. Validity, authority and assessment are read over the whole event (see the last bullet).
- **Each flex cell is a duration curve.** A `FlexPoint` says "`reduction_kw` can be held for
  `duration_min`". Durations strictly increase and kW strictly decrease, so the curve is the Pareto
  frontier and has one encoding. `deliverable_kw` takes the first point at least as long as the
  request, and 0 beyond the longest. Holds are capped at 1,440 minutes because the surface is
  indexed by a time of day.
- **A declared kW is firm** (owner decision, 2026-11-23). For each curve point, `reduction_kw` is
  deliverable for that point's `duration_min` at any event start inside the cell, within the
  binding IT-safety band (`limits.binding`). It is not a typical or expected value, so the
  adequacy simulator, sizing and dispatch may rely on it as stated. A P50/P90 range per cell is
  not in v1; it would be a later, additive version.
- **Zero flex is not unavailable.** A cell is `flex`, `zero_flex` (assessed, 0 kW, with a reason),
  `unavailable` (not offered, with a reason code) or `unknown` (not assessed, with a reason). A
  point must be at least 1 kW, so a zero cannot hide inside a curve. `query` and
  `check_instruction` keep the four apart: zero answers `0` and an instruction against it fails
  with `exceeds_declared_availability`; unavailable and unknown answer no number and fail with
  their own reason codes. This is acceptance 2.
- **Rebound sits on every point.** `energy_kwh`, `peak_kw` and `recovery_min` above the baseline
  after the hold is released, required on every point with no `unknown` option. The spec says
  rebound must be modelled and WP-THERM-03 must account for it after every event; a producer that
  does not know the rebound declares the cell `unknown` instead. Checks: a peak has a recovery time,
  and energy is at most peak x recovery time (rounded up to the kWh).
- **Integers, no floats.** kW, kWh, minutes, tenths of a degree Celsius, whole per cent. Exactness
  matters here as it does for IC-2's weights: zero must be exactly zero, "instructed <= deliverable"
  and "partial < instructed" must give every consumer the same answer, and an integer has one JSON
  form. 1 kW resolution is ample for cooling plant; `kw_to_mw` gives an exact `Fraction`.
- **IT-safety limits are declared, never computed.** `limits.ashrae` carries the class (A1-A4,
  H1), the envelope (recommended or allowable), the temperature band and its measurement point
  (IT inlet or supply air), humidity and rate of change, and the source the numbers were transcribed
  from. The module holds no ASHRAE table; relabelling the class changes nothing it checks (tested).
  `limits.binding` is the band ThermaFlex enforces (A4-02 "whichever is tighter"). What is
  checked:
  - the binding band, and a known customer SLA band, are measured at the ASHRAE band's point, so
    the nesting below is never skipped. An SLA stated at another point (supply air, say) must be
    restated at the ASHRAE point by the producer before it can be declared;
  - the binding band lies inside the ASHRAE band, and inside the SLA band when one is known;
  - `binding_source` is checkable: `ashrae` means the binding band equals the ASHRAE band,
    `customer_sla` means it equals the SLA band, and `operator_margin` means it lies inside both
    (inside the ASHRAE band alone when the SLA is unknown or not applicable).

  Chiller minimum run and off times are declared too. Known v1 limit: `binding` is temperature
  only. A4-02 also names humidity, and the contract does not say which humidity limit is enforced
  when the ASHRAE and SLA limits differ.
- **Evidence class is modelled, trialled or metered.** It must match the `evidence` basis.
  `modelled` names the model, version and `input_hash`, with calibration refs that may be empty.
  `trialled` and `metered` require at least one IC-4 `EvidenceRef` plus the IC-4 ref of the
  baseline method registered before the trial (A4-04), the observation window and the event count.
  Synthetic fixtures say so in `record_class`, not in the evidence class, so consumers can exercise
  every class against fixtures. `EVIDENCE_RANK` orders the classes.
  **The evidence class is a floor.** `evidence_class` is the floor for every `flex` and
  `zero_flex` cell: each one is supported by evidence of at least that class. A cell without
  evidence of that grade is `unknown`, never served at the surface's class. For a trialled or
  metered surface, that covers every month or band the observations did not reach. This is a
  producer obligation that the model does not check, because the basis carries one observation
  window, not per-cell coverage. The metered fixture follows it, and a test checks that it does.
- **The instruction** carries an id, the site, the surface it was planned against (by hash),
  `issued_at`, `issuer_role`, an opaque `issuer_id`, `on_behalf_of` (an IC-4 `IssuerRole`, or not
  applicable), the stream, `reduction_kw`, `start` (on a whole minute), `duration_min` and an
  `ack_deadline` in `(issued_at, start]`. `issuer_role` is its own enum (`neura_dispatch`,
  `neura_operator`): IC-4 `IssuerRole` has no Neura member by design, and Neura's B2 is the normal
  issuer. The upstream party is the IC-4 role.
- **The acknowledgement** is bound to one instruction by id **and** content hash, and is
  `accepted`, `rejected` with a `ResponseReason`, or `partially_accepted` with `accepted_kw`.
  `verify_acknowledgement` refuses another instruction's id or hash, an acknowledgement before the
  instruction, and a partial acceptance that is not below the instructed kW. A late
  acknowledgement is valid and reported `late`: what B2 does with it is B2's policy.
- **All instants are `UtcInstant`**, so an offset spelling never changes a hash (tested).
- **One check for both ends.** `check_instruction(surface, instruction)` runs the contract-only
  checks (site and surface hash, validity window, notice, cell status, kW against the curve), so
  B2 before sending and ThermaFlex on receipt reach the same reason codes. The kW is read from the
  cell the event starts in. Everything else is read over the whole event `[start, end)` on the GB
  clock, across a clock change too:
  - `outside_validity_window` unless `valid_from <= start` and `end <= valid_until`;
  - `availability_unavailable` if the event touches an `unavailable` cell anywhere, so an event
    may not run from an allowed night cell into a forbidden day cell;
  - `availability_unknown` if the event touches an `unknown` cell anywhere. This is the
    conservative rule: an unknown cell carries no claim at the surface's evidence class, so an
    event may not run into one any more than it may start in one.
  `flex` and `zero_flex` cells that the event runs into do not refuse it, because the start
  cell's curve already declares the hold.

Consumers need no plant model: `query`, `query_at`, `deliverable_kw`, `check_instruction` and
`verify_acknowledgement` import nothing outside `gridq.contracts` and the standard library
(`zoneinfo`). A fresh-interpreter test proves this on the fixtures (acceptance 3).

Mutation checks (`test_ic11_thermal_flex.py`): fifteen IC-11 guards are reverted. The four F1
mutants put back the start-only check, or step over a clock change. The five F2 mutants drop the SLA
nesting, either point check, or either `binding_source` equality. See "Mutation checks" below.

## IC-15: the contract template set (WP-GRC-09)

Roadmap NEW-IC-15. Spec D2 needs seven contract templates, and §10A has no contract for them.
The templates are the Assessment SOW, the Software and Dispatch MSA, the data-sharing agreement,
the aggregator/flexibility services agreement, the site access and installation agreement, the
lender reliance letter and the performance wrapper. The texts are in `templates/<id>.md`, and
`templates/contract_template_set.json` is the manifest, an instance of `ContractTemplateSet`. The
MSA is written in full; the other six are heads of terms.

**Every template is a draft that needs legal review.** Each text opens with
`DRAFT — NEEDS LEGAL REVIEW. NOT LEGAL ADVICE.` and says counsel has not reviewed it. Each manifest
entry is `draft_needs_legal_review`, and the manifest lists one person-only legal-review step per
draft. The other review state, `counsel_reviewed`, binds the SHA-256 of the reviewed text and an
`EvidenceRef` to counsel's third-party `legal_opinion`. An edited text is a draft again
(`effective_review`). `check_executed_terms` refuses a draft unless the caller passes
`admit_draft=True`.

What each entry carries, and what checks it:

- **Registration (acceptance 1).** `executed_as` names the IC-4 type an executed copy is registered
  as and the issuer roles admitted for it. A self-issuable type is refused. The entry also lists
  its required fields, each typed (`text`, `date`, `quantity` with a unit, `contract_instance`
  bound to a reference) and each with the ask that fills it.
- **Named clauses (acceptance 2).** The MSA must carry eight named clauses: `safety_hierarchy`,
  `it_outage_carve_out`, `step_in`, `liability_cap`, `service_levels`, `termination`,
  `control_authority_staging` and `responsibility_boundary`.
  - The safety hierarchy is spec §8's six tiers as an ordered typed list. A reordering is refused,
    in the manifest and in the text.
  - Control authority is staged L0 to L4 (ADR-D2). Only L2 and L3 write, and each waits on XG-11,
    WP-EDGE-08 and XG-17.
- **References by contract version (acceptance 3).** A `ContractVersionRef` names a contract and the
  range `1.x`, and is `frozen` (minimum version and tag) or `not_yet_frozen` (the package that
  freezes it).
  - IC-3 is bound: `frozen`, minimum 1.0.0, tag `contracts/ic3-dispatch-envelope/v1.0.0`. WP-SIZE-07
    froze it, and `live_contract_versions()` now lists it. A field bound to it (the reliance letter's
    `relied_dispatch_envelope`, the wrapper's `underwritten_envelope`) holds an envelope by content
    hash. IC-8 still waits on WP-LEDGER-07, with D-2 and XG-22.
  - Texts cite a contract only by a `[[ref:...]]` token. An `IC-n` id in prose is refused, and so
    is an IC-3 or IC-8 artefact (envelope, verifier, method version and others) named without its
    token on the same line.
  - A line citing a pending reference must say "unavailable until" its package, and a field bound
    to one can only be `Unknown`.
  - `reference_findings` reports a pending reference whose contract now exists. **IC-3's binding is
    complete; IC-8's completes when WP-LEDGER-07 freezes it.**
- **Prices and terms.** A price is `list_price_on_record` or `to_be_agreed` with its ask. Only
  ADR-D1's four figures are on record: £20,000 per assessment, £5,000 integration, £10,000 per MW
  per year to operate, and a 2.5% wrapper fee through an insurer (XG-20). Each belongs to one
  template, and each renders as "list price on record, not agreed with any customer". Every cap,
  service level, period and other fee is to be agreed. No party is named.
- **Unavailable terms.** The wrapper's insurer terms are unavailable until WP-GRC-13, and its
  pay-out terms until WP-LEDGER-07 and WP-GRC-13. The text shows each as "unavailable until ...".
  The wrapper admits an `insurer` only. An MGA and a floor provider are unavailable (gap G8) until a
  role exists.
- **Financing variants.** ADR-D1's three variants (direct host-funded, IPP third-party owner,
  power-provision lessor) are manifest data, and each text has a variant schedule.
- **Text pins.** Each text's SHA-256 is pinned in the manifest. Its clause anchors must be the
  manifest's clauses in order, and its field tokens must match the required fields exactly.
  `template_set_findings` runs every check, and returns nothing on the committed set.
- **Data versions.** The manifest carries a `set_version` and each template a `template_version`
  (all `0.x`: drafts), stated in each text. A text change bumps its template's version and the set's;
  the IC-15 contract version moves only when a schema does. Set 0.1.0 is the one tagged with IC-15
  1.0.0. Set 0.2.0 binds IC-3 and takes the five texts that cite it to 0.2.0, each line's "binding
  unavailable until WP-SIZE-07" removed; no IC-15 schema changed, so IC-15 stays 1.0.0.

`ExecutedContractTerms` records one executed contract: the template version and text hash, the IC-4
hash of the signed copy, the variant, and every required field as a value, `Unknown` with its ask,
or `NotApplicable`. `check_executed_terms` refuses the following, in order:

- a text the manifest does not pin;
- a draft;
- a missing or extra field;
- a value of the wrong kind or unit;
- a known instance of a contract that is not yet frozen;
- a signed copy that does not resolve;
- a signed copy of another type, or from an issuer the template does not admit.

The module renders nothing (no docx, no PDF) and stores nothing (WP-BO-02 is the registry).

## IC-3: dispatch envelope

The envelope is what Assess hands to Operate: the reserve policy, availability windows and state
of energy floors that live dispatch, the edge, auctions, the dashboard, the ledger and IC-15 must
honour. It has one producer (the sizer, through the WP-SIZE-08 exporter) and many consumers. Each
consumer binds one instance by its content hash. The module docstring maps spec §10A to the fields.
Decisions: `docs/decisions.md` D85-D89.

- **Time base.** A recurring template on `Europe/London`:
  - `seasons` partition the months, `day_groups` partition the ISO week, and `bands` partition the
    local day on the 30-minute grid;
  - `cells` name one `terms_id` per (season, day group, band);
  - dated UTC `overrides` replace a cell's terms wholesale.

  `resolve` maps each UTC half-hour to its local cell. It never maps a local time to UTC, so the 23-
  and 25-hour days need no rule of their own. It step-encodes the result as a `ResolvedTimeline`,
  and `resolved_hash` is that timeline's content hash, recomputed on validation. An edge with no
  clock logic reads the timeline and trusts it by that hash.

  The template is canonical: adjacent bands, day groups or seasons with identical terms everywhere
  are refused, so two producers of one policy write one hash. Validity is half-open; `supersedes`
  names a predecessor by hash. `active_at` refuses:
  - two envelopes in force at once;
  - envelopes of more than one site;
  - a successor that starts before what it supersedes.
- **Integers.** Power is in kW, energy in kWh of absolute state of energy, durations in minutes and
  temperatures in tenths of a degree. Every closure is exact.
  The producer rounds to the safe side and refuses rather than relax a term: floors and held energy
  round up; limits, ceilings and slices round down; merchant takes the residual.
- **Partition.** Per cell and per direction, resilience + service slices + merchant + withheld equals
  the rating. The energy stack (floor + held + merchant + headroom + withheld) equals the usable
  energy. Every positive slice holds at least `kw x sustain_min / 60` kWh. No double-booking is
  this arithmetic, and nothing gates a service on its basis, stream, size or value (D57).
- **Thermal.** With an IC-11 surface, every cell carries an operating band inside the binding band
  and its thermal commitments. `check_against_surface` proves each commitment against the surface's
  firm kW, over the whole event.
- **Source.** `source.input_hash` is required. It is never unknown, not applicable or the hash of
  nothing, and it equals the IC-12 quote's input hash. `run_id` re-derives from the four run pins.
  The basis is a typed `study` or `underwriting` label; an underwriting label naming a study is
  refused (D46). A `live` envelope holds no unknown reference.
- **Consumers.** These need no sizer, and import nothing outside `gridq.contracts`, pydantic and
  the standard library:
  - `resolve`, `terms_at` and `timeline_matches`;
  - `flat_limits`, `merchant_headroom`, `reserved_kw`, `offered_kw`, `service_windows` and
    `underwritten_total`;
  - `check_schedule` (violations in §8 order), `check_against_surface`, `check_against_agreement`,
    `check_source` and `check_constraint_library`;
  - `active_at` and `envelope_ref`.

  A fresh-interpreter test runs them on the fixtures (acceptance 3).

Mutation checks (`test_ic3_dispatch_envelope.py`): eighteen IC-3 guards are reverted:
- the input-hash, quote-binding and `run_id` derivation guards;
- discharge closure, held energy against sustain, the firm limit and the fail-safe floor;
- `resolved_hash` and the resolver's clock-change step;
- override disjointness;
- the band inside the binding band, thermal presence, surface kW and the unavailable cell;
- the live and D46 labels;
- the floor breach in `check_schedule`;
- `active_at`'s ambiguity refusal.

## IC-5: edge ↔ cloud protocol

The wire contract between the cloud (MPC, control room) and a site's edge gateway (spec §10A, B1/B2;
decisions D114 to D121). Three schemas: one frame model per direction and the per-site
`LinkProfile` both sides bind by hash.

- **Frames.** Each direction is its own hash chain (`link_epoch`, `seq`, `prev_hash`, IC-13 genesis at seq
  0), carrying `profile_hash` and the sender's `peer_head`. A body kind that is illegal in a direction is
  absent from that direction's schema. `ChainTracker` gives the receiver's verdict on every arrival
  (`in_order`, `duplicate`, `gap`, `late_fill`, `fork`, `stale_epoch`, `profile_mismatch`, `new_epoch`); a
  fork refuses the rest of the epoch. Chain health never gates fail-safe: a refused frame only fails to renew
  the lease.
- **Schedules.** `SetpointSchedule` names its IC-3 envelope and compiled timeline by hash. Each period is an
  attributed command (merchant plus service deliveries, exactly), an import cap, an optional chiller band and
  the expected outcome. `check_setpoint_schedule` applies IC-3's own rule over the timeline (a differential
  test pins it to `check_schedule`) and the staged authority: nothing at L0, shadow from L1, closed loop
  from L2, a chiller band from L3. One violation rejects the whole schedule. `admit_schedule` adds the
  receipt checks (site, id reuse, order, supersession, stale on arrival, elapsed horizon, `deliver_by`).
- **Acks.** `ScheduleAck`, hash-chained per schedule, one per transition: `accepted` or `rejected`, then
  `active` or `shadowing`, then `superseded`, `completed`, `lapsed` or `aborted`. `governing_timeline` says
  which schedule governed when. A late schedule is accepted and flagged; only a stale one is refused.
- **Heartbeat and staleness.** Every threshold is a required `LinkProfile` field with no default, measured
  on the receiver's monotonic clock; `link_state` reports every trigger that fires, and equality is
  fail-safe. The profile refuses an MPC cadence at or above the schedule's maximum age.
- **Fail-safe.** 17 `TriggerCode`s in the frozen `TRIGGERS` table (category, IC-3 rank, detector,
  release). An episode holds a set of active triggers; it exits only when none is active, every
  non-automatic one carries an audited release, and a schedule was accepted after the last clear.
  `fail_safe_target` computes B1-02's target.
- **ANM, local only.** `AnmEvent` travels edge to cloud only; no ANM model is reachable from the
  cloud-to-edge schema (a schema-walk test pins the allow-list of ANM-like words there).
  `effective_import_cap` takes the minimum, so the cloud can only tighten; a trip is 0 kW.
  `verify_anm_response` compares with IC-1's `anm_response_time_s` exactly, as a fraction.
- **Latency.** `latency_budgets.cloud_to_edge_schedule` is required; each schedule carries
  `deliver_by = sent_at + bound`; `check_latency` uses an integer nearest rank and the profile's clock skew
  (`pass`, `fail`, `indeterminate_within_skew`, `insufficient_samples`, `unverifiable_clock`). The path set
  has no ANM member.
- **Gateway footprint (acceptance 4).** A gateway can validate frames from the committed schema files
  alone (`tests/contracts/_schema_subset.py`, a stdlib validator that fails on any keyword it does not
  implement), or with `gridq.contracts` and pydantic only. Both are fresh-interpreter tests. There is no
  contracts-only distribution in `pyproject.toml` yet, and `__main__` imports `gridq.schemas.chassis`, so
  such a distribution would need to exclude or guard that file.
- **Reference simulator.** `gridq.edge_reference`, outside this package so no socket reaches a
  contracts-only install: a relay, an edge twin and a cloud twin over stdlib HTTP on loopback, with a
  simulated clock, fault switches and ANM injection (`python -m gridq.edge_reference --help`). Tests:
  `tests/contracts/test_ic5_reference_endpoint.py`, including five mutation checks on its guards.

Mutation checks (`test_ic5_edge_cloud.py`): twenty IC-5 guards are reverted:
- the required schedule budget, `deliver_by`, the strict late flag and the skew in a pass;
- the lease at equality, the resume hold and the MPC cadence below the maximum age;
- the chain link to the head and the refused forked epoch;
- the L2 authority, the named timeline, the resilience floor from the timeline and the chiller band;
- the ack state machine and supersession against the successor's activation;
- no exit with a trigger active, an audited release, cloud release of Neura-ops codes only;
- the ANM limit in the cap and the exact ANM response comparison.

## IC-9: compute turn-down declaration (WP-SITE-07)

Spec §10A IC-9 and B8-01: a GPU operator *declares* the turn-down its workloads can offer at one
site (MW, duration, notice, price, SLA class, validity). Producer: the GPU operator or an
orchestrator adapter (WP-SITE-08). Consumers: the co-optimising sizer A5 (WP-SIZE-11), live
dispatch B2 in shadow mode, IC-3, and the data-centre load model's on-request overlay
(a plug-in package, not named here: CORE never names a plug-in). Declaring is optional and never
assumed (A2-04): with none, the load model's IT load is untouched (WP-SITE-07 acceptance 2).

**The specification is generated.** `specs/ic9_compute_turndown_declaration.v1.0.0.md` is rendered
by `_specdoc.render_spec` from the module docstring, the identity (id, version, versions read,
git tag, each schema file and its SHA-256), a field table per schema object and the public readers
with their signatures. `--check` fails when the committed file is not a fresh render; `--write`
regenerates it. Read the spec for the field-by-field contract; the design, in brief (decisions
D122 to D127, `docs/decisions.md`). The frozen 1.0.0 module docstring and the specification rendered
from it still name these decisions by their WP-SITE-07 branch placeholders (SITE07-1 to -6, in
order): both files are pinned byte for byte to the `contracts/ic9-compute-turndown/v1.0.0` tag
(`test_the_git_tag_is_annotated_and_carries_the_frozen_schemas_and_spec`), so they change only with
a new IC-9 version.

- **A tiered menu plus recurring availability.** `envelopes` are alternative turn-down products
  (each a Pareto duration curve: absolute kW, or basis points of the running IT load with a kW
  cap), named in an `exclusion_group` when they draw on the same workloads. `availability` is a
  GB-clock template (seasons x day groups x bands, each cell `offered` with envelope ids,
  `unavailable` with a reason code, or `unknown`) plus dated overrides (an extra offer, a
  blackout).
- **Compiled and bound.** `resolve` compiles the template and overrides to a UTC step timeline
  (`ResolvedTimeline`, its own schema); the declaration carries its `resolved_hash`, so a consumer
  that holds the timeline knows it is this declaration's. The one naive-local reader,
  `declared_at_local`, reads a repeated hour as its first occurrence (PEP 495 `fold=0`).
- **SLA classes own the limits.** Each `SlaClass` states what happens to the work
  (`deferred`, `slowed`, `moved`, `dropped`), whether energy returns later, and a call budget
  (events, duration, minimum rest) shared by every envelope of the class. Rebound is sized per
  envelope.
- **Price is structured and never a fact.** An envelope's price is `unknown` or a `PriceOffer`
  with availability, utilisation and per-event components, each a stated integer pence figure,
  `not_applicable` or `unknown` (an unknown is never read as zero), and a `price_basis`
  (`indicative`, `firm_offer`, or `synthetic_placeholder`, which prints "SYNTHETIC PLACEHOLDER, not
  an offer").
- **Conditional, never a gate.** Every consumer treats IC-9 kW as CONDITIONAL (B8-03), outside
  the priced floor. The contract has no firmness field and no reader refuses, zeroes or ranks a
  revenue stream (D57); the only rules between offers are arithmetic, so the same kW is never
  counted twice: `stack_at` takes envelopes in merit order, one per exclusion group, up to the
  concurrent cap and above the retained IT floor; `check_call` applies the class budget and
  reports a limit it cannot check as `unchecked`, never as a pass. L4 has no write path (ADR-D2):
  there is no instruction or acknowledgement message in 1.0.0.

Readers (`stack_at`, `check_call`, `offered_at`, `declared_at_local`, `deliverable_kw`, the price
functions and `resolve_sla_evidence`) import nothing outside `gridq.contracts` and the standard
library; a fresh-interpreter test proves it on the fixtures.

## IC-14: telemetry series (WP-TEL-01)

Roadmap NEW-IC-14 (spec §10A has no telemetry contract): the one contract every Operate consumer
reads site telemetry through (dispatch B2, control room B5, ledger B6, twin B7, NESO performance
files B3-04, the independent verifier B6-05). Producers: the edge gateway (B1) and the site twin.
The specification is generated, as IC-9's is: `specs/ic14_telemetry_series.v1.0.0.md`. Design
decisions D148 to D153 (`docs/decisions.md`), in brief:

- **A missing sample is a gap, never a zero.** A raw block tiles its slots by `ValueRun` (integers
  of one quality) and `GapRun` (a reason and no value field); every slot is covered once and
  adjacent runs of one kind must be merged. A curated interval is an `Aggregate` only with the
  method's `min_usable_ms` of usable time, else a `CuratedGap`; nothing is filled, held or scaled
  across a gap, and a `held` value is never usable. Acceptance 2 is property tests P1 to P14.
- **Point naming and units.** `PointName` (asset kind, asset id, quantity, measurement role,
  aspect) with a dotted `point_key`; `series_id` is `<org>/<site>/<point key>`. `QUANTITY_TABLE`
  fixes one integer unit per quantity (W, var, VA, Wh, mHz, mV, mA, basis points, millidegrees C)
  and its aspects; power is load-convention signed. Converters to IC-3/IC-5 kW state a rounding side.
- **Time.** Fixed UTC blocks of integer slots at 50 ms, 1 s and 1 min raw (60 s, 60 s and 30 min
  blocks); curated at 1 s, 1 min and 30 min (the IC-3 grid, a UTC day of 48). `ClockStatement`
  says who stamped a block and whether the clock was synchronised; an unsynchronised block curates
  to `clock_untrusted`. `settlement_period_of` maps UTC to the GB settlement day (46, 48 or 50).
- **Quality.** `ValueQuality` and `GapReason`, each mapped one-to-one to an OPC UA status code
  checked against the OPC Foundation's `StatusCode.csv` (`QUALITY_MAP`); an unlisted code raises.
  IC-5 trigger codes map to gap reasons by value (`TRIGGER_GAP_REASON`).
- **Tiers.** Raw `TelemetryBatch` (immutable, hash-chained per series, sender and stream epoch;
  `track` accepts late fill and refuses only a fork or a changed descriptor), curated
  `CuratedSeries` (a pure function of raw, embedding its whole `CurationMethod` and the raw runs it
  read, recomputed by `verify_curated`, superseded by hash), and `DqAnnotation` (a monitor's finding
  citing raw).
- **Tenancy.** `TenantKey` (IC-13 organisation and site) on every record and inside its hash;
  `read_guard` is IC-13 `authorise` on a `telemetry` resource, and its `ReadDecision` agrees with
  the IC-13 audit event for the same read. Readers that return data take a decision and raise
  `TenantReadRefused`; transforms given two tenants raise `MixedTenants`.
- **IC-5.** IC-5 1.0.0 has no telemetry body and is untouched. A batch is shaped to become one in an
  IC-5 minor (`kind` `telemetry_batch`, IC-5's `site_id` header rule, an `Ic5Binding` to the link
  profile and link epoch); this module never imports IC-5 (an AST test).

## Mutation checks

**197 guards** are reverted one at a time, in memory, in a fresh interpreter, and each guard's test
must fail (pytest exit 1): the fifty-one IC-14 guards (`test_ic14_telemetry_series.py`: the
block tiling, the raw and curated validators, the gap and clock rules in `resample`, the tracker,
the chain, supersession, tenancy and `read_guard`, data age, OPC UA and the converters), the
forty-two IC-9 guards (`test_ic9_compute_turndown.py`: every
validator, the resolver and its clock-change walk, `fold=0`, `stack_at`'s group, cap and floor,
`check_call`'s reasons and budget window, the unknown-price rule and `active_at`'s refusal), the
fifteen IC-11 guards above (`test_ic11_thermal_flex.py`),
WP-GRC-09's twenty-seven (`test_mutations_v120.py`: R7, the IC-4 v1.1.0 archive pin, and IC-15's
twenty-five checks, listed with their tests in the file), WP-BO-06's twenty-four
(`test_mutations_v110.py`): R1 to R6, `require_third_party` (the raise and
the re-validation), `assess`'s skip, `resolve_ref`'s five refusals, `evidence_ref`'s version,
`SELF_ISSUABLE_TYPES` widened by one type, `Version1x` loosened to any major, the IC-1, IC-2 (two)
and IC-11 (two) down-level rules, and `__main__`'s archive hash check and `--write` refusal; WP-SIZE-07's eighteen
(`test_ic3_dispatch_envelope.py`), listed in the IC-3 section above; and WP-EDGE-01's twenty
(`test_ic5_edge_cloud.py`), listed in the IC-5 section above.

The runner is `_contracts_support.run_mutant(module, old, new, nodeid)`. A finder placed first on
`sys.meta_path` serves the mutated module to every importer, so a mutant in `_base` or `evidence`
reaches IC-1, IC-2 and IC-11 too. Each mutant's `old` text must occur exactly once. A control run
with the same finder on the unmutated source must pass. A mutant that cannot load, whose target is
not found, or that is not the module imported exits 3, and a collection error exits 2, so neither
passes as red.

## Fixtures (`tests/contracts/fixtures/`)

- **IC-4.** Four `*.synthetic.json` files: valid, expired, unverified with unknown validity, and no
  expiry.
- **IC-1.** Three archetypes: firm, hybrid (firm base plus interruptible, interim until Technical
  Limits reinforcement), and fully non-firm (profiled import, LIFO, trip).

- **IC-2.** Two committed synthetic-shape sets:
  - three equal 1/3 "years", with minute and half-hourly series;
  - modelled weights 1/2, 3/10 and 1/5, half-hourly only, linked to the IC-1 hybrid fixture by
    hash.

  Real-data sets (UKPN DER4 and NPg DER-0000099) are built from recorded publisher responses
  under `fixtures/real/`. They are **git-ignored** under the Atlas eyes-only gate. Only their
  `MANIFEST.json` (hashes and provenance, no data) and README are committed; see
  `fixtures/real/README.md`.
- **IC-13.**
  - one claims payload per role;
  - `ic13_tokens.fixture.json`, the four signed tokens with issuer, audience and `valid_at`;
  - `ic13_audit_log.synthetic.jsonl`, a six-event chain with one canonical event per line;
  - the TEST-ONLY key pair.

- **IC-11.** Built by `_ic11_fixtures.py`:
  - `ic11_surface_chilled_water_modelled`: water-cooled chillers with towers and CRAH, no trial
    data, so `modelled`. Long overnight holds, minutes at the evening peak, and **zero flex** on
    summer afternoons and peaks.
  - `ic11_surface_metered_trial`: an air-cooled H1 site with a **metered** trial (IC-4 refs to
    the meter records, the baseline method and the SLA). The trial ran January to March, so only
    the `winter_jan_mar` cells carry the metered class. Their day cells are **unavailable**
    (control is allowed overnight only). Every `summer` and `winter_oct_dec` cell is **unknown**,
    under the evidence-class floor rule.
  - `ic11_instruction_winter_night` against the metered surface, and its `accepted`, `partial`
    (350 of 500 kW, IT-safety limit) and `rejected` (late, rebound recovery) acknowledgements.
  - Evidence hashes are SHA-256 of synthetic placeholder text. The ASHRAE numbers are as commonly
    cited and were not verified against any edition of the standard. The source string names no
    edition and says the figures are unverified. The 60 % RH figure may belong to an earlier
    edition than the one first cited (review F4). The owner supplies the standard before any
    registered surface.

  `_ic13_fixtures.py`, `_ic2_fixtures.py` and `_ic11_fixtures.py` rebuild these files, and a test
  requires the committed files to equal a fresh build.

- **1.1.0 fixtures (`fixtures/v1_1_0/`, WP-BO-06).** Every 1.0.0 fixture above, and its golden hash,
  is unchanged and still validates. The 1.1.0 fixtures sit in their own directory, with their
  hashes in `GOLDEN_HASHES_V110`:
  - IC-4: a data-processing agreement (`customer`, verified), for the DPA guard's tests; a legal
    opinion (`legal_adviser`, unverified); and Neura's own records, marked `neura_self_issued`: a
    baseline method (unverified), a trial record (countersigned by an independent third party) and
    a decision record (named `SYNTH-ADR-0001`, dated, unverified).
  - IC-1: the hybrid archetype at 1.1.0, evidence-backed by a `1.0.0` reference to the IC-4
    connection-offer fixture.
  - IC-2: the modelled set at 1.1.0, linked to that agreement by hash.
  - IC-11: a `trialled` surface that rests on Neura's own trial record and baseline method. The
    trial ran July to September, so only the `jul_sep` cells carry findings and every other cell is
    `unknown`.

  `_ic2_fixtures.py` and `_ic11_fixtures.py` build the IC-2 and IC-11 ones; a test requires the
  committed files to equal a fresh build.

- **1.2.0 and IC-15 fixtures (`fixtures/v1_2_0/`, WP-GRC-09)**, built by `_ic15_fixtures.py`:
  - two IC-4 1.2.0 signed copies: an MSA (`customer`) and a lender reliance letter (`lender`);
  - the executed terms of each. Every commercial term of the MSA is `Unknown` with its ask, because
    none is on record. The reliance letter binds the IC-1 and IC-2 1.1.0 fixtures by content hash,
    and the dispatch envelope to the IC-3 `ic3_envelope_hybrid_bess` fixture, which binds those same
    IC-1 and IC-2 fixtures (it was `Unknown` until WP-SIZE-07). Their hashes for set 0.2.0 are in
    `GOLDEN_SET_0_2_0`; the set 0.1.0 hashes in `GOLDEN` are checked against the IC-15 tag.
- **IC-3 (`fixtures/ic3/`, WP-SIZE-07).** Built by `_ic3_fixtures.py`, hashes in `GOLDEN_HASHES_IC3`:
  - `ic3_envelope_hybrid_bess`: a synthetic hybrid BESS. A night non-firm import limit, a Capacity
    Market slice on winter weekday days, local flex stacked on it at the winter weekday peak, and a dated
    maintenance override in July 2027. It references the IC-1 and IC-2 1.1.0 hybrid fixtures.
  - `ic3_envelope_hybrid_bess_successor`: a re-issue from 1 July 2027 with a larger Capacity Market
    slice (1500 kW, was 1000), superseding the first by hash; `active_at` picks the successor from then.
  - `ic3_envelope_dc_thermal`: a data-centre envelope against the IC-11 chilled-water fixture
    surface. It has DFS turn-down commitments and per-cell bands, and is provable against that
    surface.
  - `ic3_envelope_clock_change_week`: the week of the March 2027 clock change, on the IC-1 firm
    fixture.
  - `ic3_timeline_clock_change_week`: that week's compiled timeline, 336 half-hours in 15 steps.

  Site ids start with `SYNTHETIC-`, the record class is `synthetic_fixture`, and every placeholder
  hash is the SHA-256 of text that names what it stands for. A test requires the committed files to
  equal a fresh build.

- **IC-5 (`fixtures/ic5/`, WP-EDGE-01).** Built by `_ic5_fixtures.py`, hashes in `GOLDEN_HASHES_IC5`:
  - `ic5_link_profile_hybrid`: a link profile for the IC-3 hybrid site. Its numbers are synthetic: the
    2-minute maximum age and 5 s p99 budget are the spec's internal targets, not published values, and
    every profile number is required with no default.
  - `ic5_cloud_to_edge_hybrid` (JSON lines): a heartbeat, a schedule and a later heartbeat.
  - `ic5_edge_to_cloud_hybrid` (JSON lines): a boot episode that exits on the schedule's acceptance,
    its acks, heartbeats and a local ANM setpoint.

  Each line is the frame's canonical JSON, so its SHA-256 is the frame hash; the golden for a JSON-lines
  file is its chain head. A test requires the committed files to equal a fresh build.

The IC-1 and IC-4 fixtures are **synthetic archetypes for schema testing, not real customer
agreements**:
- site ids start with `SYNTHETIC-`, and issuer and party names say "fixture; not a real ...";
- no network operator is named;
- document hashes are hashes of a synthetic placeholder text.

The *structure* follows the public GB sources cited in each IC-1 fixture's `source.based_on`
(the URLs as listed in spec §17; they were not re-fetched when the fixtures were written).

- **IC-9 (`fixtures/ic9/`).** Built by `_ic9_fixtures.py`, hashes in `GOLDEN_HASHES_IC9`. Every
  site and class id is `SYNTHETIC-`, and every stated price is a `synthetic_placeholder`; no kW, price or SLA value is an operator's.
  - `ic9_declaration_gpu_campus`: three envelopes (checkpoint and power-cap share an
    exclusion group; migrate does not), two SLA classes, a concurrent cap and a retained IT floor,
    a dated extra offer and a Christmas blackout, and one `unknown` cell (summer weekend evenings).
  - `ic9_declaration_gpu_campus_successor`: supersedes the campus from 2027-07-01.
  - `ic9_declaration_relative_curve`: a share-of-load curve with its kW cap.
  - `ic9_declaration_clock_change_spring` and `_autumn`: weeks spanning the 2027 clock changes
    (a 23-hour and a 25-hour night), and `ic9_timeline_clock_change_spring`, the spring
    declaration's compiled timeline.

  A test requires the committed files to equal a fresh build.
- **IC-14 (`fixtures/ic14/`).** Built by `_ic14_fixtures.py`, hashes in `GOLDEN_HASHES_IC14`
  (a JSONL chain is pinned by its last batch's hash). Tenant `SYNTHETIC-ORG-HYBRID`, site IC-5's
  `SYNTHETIC-SITE-HYBRID`, source protocol `simulated`; the watts are an arithmetic pattern and the
  meter certificate is a placeholder hash. One half-hour (2027-01-04 16:00Z to 16:30Z):
  - three descriptors: the boundary revenue meter, its SCADA counterpart, a battery state of charge;
  - `ic14_batches_site_power_meter`: 29 one-minute batches in two stream epochs across an edge
    reboot, with a held run, a 20 s `comms_lost` gap, an `inaccurate` minute, a lost minute and
    one block on an unsynchronised clock;
  - `ic14_annotation_flatline`: a flatline finding over 16:15;
  - `ic14_curated_site_power_1min_rev0` and `_rev1`: the 1-minute curated record before and after
    the second epoch arrived (`late_raw`).

## Changing a contract

Do not edit a frozen version in place. To change a contract:
1. **Archive first.** Add the current schema file's name and SHA-256 to `__main__.ARCHIVED` before
   touching the model. `--check` then verifies its bytes, and `--write` refuses (exit 2, nothing
   written) to change it.
2. Bump `CONTRACT_VERSION` and `GIT_TAG`, and append the new version to `SUPPORTED_VERSIONS` and to
   the model's own `contract_version` literal. The last supported version is the one written.
3. **Down-level rule.** Add one `after` validator on the top-level model: an instance labelled with
   an earlier version may hold only what that version allowed (its members and the versions of the
   references it embeds). The error says `not in IC-<n> <version>`.
4. Run `python -m gridq.contracts --write` to write the new schema file beside the archived one.
5. Pin the schema delta, as `test_schema_delta_v110.py` does: the changed paths equal a pinned
   list, and no field is added, removed or renamed. A new field on an object is a major version.
6. A reference to another contract uses `Version1x`, never an exact pin.
7. Add a mutation check for every new guard, and a `CHANGELOG.md` entry.
8. Existing fixtures and golden hashes never change. A new version's fixtures go in their own
   directory with their own hashes; update the golden hashes only if the canonical form changed,
   which is itself a contract change.

`python -m gridq.contracts --check` (also run by the tests) fails on drift, or on an archived file
whose bytes no longer match their pinned hash.
