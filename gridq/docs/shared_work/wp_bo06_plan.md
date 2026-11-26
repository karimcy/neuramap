# WP-BO-06 execution plan: IC-4 1.1.0 and one coordinated re-version

Planner: Fable 5.2, 2026-11-23. Executor: Opus. Plan only; no source or test was changed to write it.

- Worktree: `/Users/oliverpugh/Desktop/1 - Neura UK/Neura GitHub/uk-therm02`, branch `roadmap/p1-foundations`, HEAD `31da157`.
- Package: `src/gridq/contracts/`. Tests: `tests/contracts/`.
- Roadmap entry: `atlas/roadmap/roadmap.yaml`, `WP-BO-06`. Reasons: `atlas/research/21_fable_scope_review.md` §5.
- Bound inputs (owner, 2026-11-23; not reopened here): ADR-D10 option (b); the DPA guard's two types and
  public-intake decision (a); the IC-11 review's trial and baseline types; declared IC-11 kW is firm.

## 0. Measured baseline (at `31da157`, today)

| Check | Result |
|---|---|
| `PYTHONPATH="$PWD/src" ../uk/.venv/bin/python -m pytest -o addopts="" -q -W ignore tests/contracts` | 563 passed, 5 skipped (the skips are the git-ignored real IC-2 fixtures) |
| `PYTHONPATH="$PWD/src" ../uk/.venv/bin/python -m gridq.contracts --check` | `ok` for all 8 schemas |
| Tools in `../uk/.venv` | pydantic 2.13.4, ruff 0.16.1, mypy 2.3.0. `jsonschema` and `hypothesis` are **not** installed |
| Consumers of `gridq.contracts` outside the package and its tests | none |
| Tags | `contracts/{ic1,ic2,ic4,ic13}-…/v1.0.0` at `8533e58`; `contracts/ic11-thermal-flex/v1.0.0` at `3fc5cc3`. Every frozen schema and fixture at HEAD equals its tag |

SHA-256 of the six schema files this package archives. They must never change:

| File (`src/gridq/contracts/schemas/`) | sha256 |
|---|---|
| `ic4_evidence_object.v1.0.0.schema.json` | `d457702203c285245704a29c3f8472cf8daa4413462db01d3de910ec73ebdf80` |
| `ic1_connection_agreement.v1.0.0.schema.json` | `1c62e7139ab3a27171cbc5302fd0230a58ee99336877bf3d5e046407215b73b3` |
| `ic2_curtailment_scenario_set.v1.0.0.schema.json` | `fd9e588cb7b061daa5370eac4b08ccfba39c91276e7bcd289d37d259c92c0318` |
| `ic11_thermal_flex_availability.v1.0.0.schema.json` | `8ee3297cdea7d365e8a71be5ac0e037363227e6668603abde00cb35745c8dbfd` |
| `ic11_flex_instruction.v1.0.0.schema.json` | `363f44f25a33cc0fc3098ea5252611977b4987c97305b4628b963b7a6355ad42` |
| `ic11_flex_acknowledgement.v1.0.0.schema.json` | `2a44df0094548fe7a14104d82f49930454f0e5f1e0335bdff1d3d0e2af7b05d4` |

IC-13 is not touched. Its two schemas stay live and must re-check byte-identical
(`ic13_audit_event` `339c3f1d…`, `ic13_identity_claims` `59d61143…`).

## 1. How the pin works today, and why three contracts must follow IC-4

- `evidence.py` defines `EvidenceRef` with `contract_version: Literal["1.0.0"]`.
- `connection.py` (`EvidenceBacked.evidence`), `curtailment.py` (`PublishedSource.registered_evidence`,
  `ModelledRun.evidence`) and `thermal_flex.py` (calibration, trial, meter, baseline-method and SLA refs)
  import that class. Pydantic copies it into each contract's JSON Schema as
  `$defs.EvidenceRef.properties.contract_version = {"const": "1.0.0"}`.
- Each schema file is byte-frozen. So any change to what `EvidenceRef` accepts changes three schemas, and a
  changed schema is a new version of that contract.
- A second coupling: `curtailment.py` (`publisher_role`) and `thermal_flex.py` (`on_behalf_of`) import
  `IssuerRole`, so the role list is copied into the IC-2 and IC-11 instruction schemas too.
- A third: `curtailment.ConnectionAgreementRef` pins IC-1 `"1.0.0"`, and `thermal_flex.AvailabilityRef` pins
  IC-11 `"1.0.0"`. Once IC-1 and IC-11 move, those pins must move with them.

## 2. Decisions this plan makes (the executor does not reopen them; see STOP conditions)

### 2.1 What "minor" means here, as a tested property

A release is **minor** when all three hold, and each is a test:

1. Every valid 1.0.0 instance is accepted by the 1.1.0 reader, with the same meaning and the **same content
   hash**. Proof: the 19 existing fixtures and their golden hashes are untouched and still pass.
2. Nothing the 1.1.0 reader accepts under the label `"1.0.0"` would be refused by a 1.0.0 reader (the
   **down-level rule**, §4.5). So the label on an instance stays honest.
3. No field is added, removed or renamed. Only value sets widen.

A 1.0.0 reader refuses every 1.1.0 instance. That is true of any version change under the house `Literal`
rule, so it does not separate minor from major.

Consequence: each model's own `contract_version` becomes `Literal["1.0.0", "1.1.0"]` with default
`CONTRACT_VERSION` (`"1.1.0"`). The models are edited in place, as the README's "Changing a contract" says;
the 1.0.0 schema files are kept and pinned by hash (§6 step 1).

### 2.2 The self-issued marker is the role value, not a new field

- New enum `SelfIssuerRole` with one member, `NEURA_SELF_ISSUED = "neura_self_issued"`.
  `Issuer.role` becomes `IssuerRole | SelfIssuerRole`.
- The marker is on the wire in every such object (`issuer.role == "neura_self_issued"`), cannot be omitted,
  and cannot disagree with the role. `EvidenceObject.is_self_issued` reads it.
- **Why not a `self_issued: bool` field.** `canonical_json` dumps every field. A new field would add
  `"self_issued": false` to every 1.0.0 object, change all four IC-4 golden hashes, and break property 1
  above and the roadmap criterion "the 1.0.0 schema and its golden hashes are unchanged". It would also be a
  second source of truth beside the role.
- **Why a separate enum and not a member of `IssuerRole`.** IC-2 `publisher_role` and IC-11 `on_behalf_of`
  are typed `IssuerRole`. Keeping `IssuerRole` third-party-only means Neura can never appear in those slots,
  by type and in their schemas. The existing guard test
  `test_no_evidence_type_or_issuer_role_names_an_assumption_or_neura` stays true and unedited.

### 2.3 Refusal is the default path

`assess()` and the new `resolve_ref()` ignore or refuse self-issued objects unless the caller passes
`admit_self_issued=True`. A consumer that upgrades and changes nothing gets third-party evidence only.
`require_third_party()` is the explicit single-object check.

### 2.4 The reference pin: a stated compatible range, `1.x`

See §5 for the full note. In short: every version field **inside a reference** (`EvidenceRef`,
`ConnectionAgreementRef`, `ScenarioSetRef`, `AvailabilityRef`) accepts any `1.<minor>.<patch>`. A contract's
**own** `contract_version` stays an exact enumerated literal.

### 2.5 New member values must pass the existing guard word list

`test_no_evidence_type_or_issuer_role_names_an_assumption_or_neura` refuses `assum`, `derived`,
`placeholder`, `pending`, `study`, `self`, `neura`, `estimate` in any `EvidenceType` or `IssuerRole` value.
The list is **not edited**. So the study report is `technical_report`, not `study_report`.

## 3. The inventory

Method: every `acceptance` line in `roadmap.yaml` matching `evidence object`, `IC-4`, `evidence-object`,
`EvidenceRef` or `evidence registry` (30 lines, 22 packages), plus `delivers` text and a second sweep for
`signed`, `certificate`, `opinion`, `pre-regist`. Then every `EvidenceRef` slot in IC-1, IC-2 and IC-11.

Status: **E** = a 1.0.0 member; **N** = added in 1.1.0; **G** = gap, with its owner.

| # | Package | Criterion (short) | Evidence type | Issuer role | Status |
|---|---|---|---|---|---|
| 1 | WP-GRC-03 | each §12 row has an evidence object | `regulatory_publication` | `ofgem`, `elexon`, `neso`, `government` | E |
| 2 | WP-GRC-04 | no claim true without an evidence object or test | any member | any | E |
| 3 | WP-GRC-05 | no customer dataset stored before its DPA object | `data_processing_agreement` | `customer` | N type |
| 4 | WP-GRC-05 | data-sharing agreement set (delivers) | `data_sharing_agreement` | `dno`, `idno`, `neso`, `elexon` | N type |
| 5 | WP-GRC-06 | Cyber Essentials Plus certificate | `conformity_certificate` | `certification_body` | N type, N role |
| 6 | WP-GRC-07 | opinion recorded | `legal_opinion` | `legal_adviser` | N type, N role |
| 7 | WP-GRC-07 | decision recorded | none | none | **G1** |
| 8 | WP-GRC-08 | KPI tracker cites the MW definition object | `method_agreement` (row 26) | as row 26 | N |
| 9 | WP-GRC-09 | each template is an evidence-object type with required fields | partly: `data_sharing_agreement`, `aggregator_agreement`, `dno_flexibility_contract` | | **G2** |
| 10 | WP-GRC-11 | ISO 27001 certificate | `conformity_certificate` | `certification_body` | N |
| 11 | WP-GRC-13 | FCA perimeter opinion | `legal_opinion` | `legal_adviser` | N |
| 12 | WP-DATA-09 | licence evidence object permits lender-pack use | `data_licence` | `data_provider` | N type, N role; **G3** |
| 13 | WP-CONN-05 | tolerance agreement predates the back-test | `method_agreement` | `research_partner` (PSAL) | N type, N role |
| 14 | WP-CONN-08 | each G99/G100 item closed by an evidence object | `g99_approval`, `g100_compliance` | `dno`, `idno` | E; **G4** |
| 15 | WP-SITE-04 | critical-facilities sign-off of the floor | `critical_facilities_signoff` | `host`, `customer` | E |
| 16 | WP-THERM-08 | mode transition needs the host sign-off | `control_authority_signoff` | `host` | N type; **G5** |
| 17 | WP-EDGE-08, WP-OPS-07 | closed loop refused until sign-off exists | `control_authority_signoff` | `host` | N type; **G5** |
| 18 | WP-UW-01 | a stream without its evidence object is not priced | `bmu_registration`, `cmu_prequalification`, `capacity_agreement`, `dno_flexibility_contract`, `aggregator_agreement`, `g99_approval`, `g100_compliance`, `mpan_registration`, `customer_consent`, `connection_agreement` | 1.0.0 roles | E; **G6** |
| 19 | WP-UW-05 | DNO flex conditional until a contract object exists | `dno_flexibility_contract` | `dno` | E |
| 20 | WP-BO-01 | expired is not absent; an assumption is never evidence | contract properties | | E, kept |
| 21 | WP-BO-02 | stored objects pass IC-4; expiry changes eligibility | the ten kinds its `delivers` lists | 1.0.0 roles | E |
| 22 | WP-BO-04 | no customer data before the NDA object | `non_disclosure_agreement` | `customer` | N type |
| 23 | WP-EDGE-07, WP-OPS-06 | signed commissioning record passes IC-4 | `commissioning_record` | `host`, `oem`, `independent_engineer` | N type; note a |
| 24 | WP-TEL-04 | each meter has certificate evidence | `meter_commissioning_certificate`; calibration: `conformity_certificate` | `meter_operator`, `certification_body` | E, N |
| 25 | WP-MKTGW-04 | FMAR record held as an evidence object | `fmar_registration` | `elexon` | N type |
| 26 | WP-OPS-02 | MW agreement registered, signed by each party | `method_agreement`, one object per counterparty, same `document_hash` | `funder` (Innovate UK), `research_partner` (PSAL), `host` | N type, N roles; note b |
| 27 | WP-TWIN-03 | study report registered | `technical_report` | `research_partner`, `independent_engineer` | N type |
| 28 | WP-THERM-04, WP-LEDGER-04, WP-THERM-09 | baseline method registered before any trial | `baseline_method` | `neura_self_issued` (ADR-D10 b), or `independent_verifier` | N type, N roles |
| 29 | WP-CORE-03 | chassis v2 carries IC-4 references | `EvidenceRef` (1.x range) | | no type needed |
| 30 | IC-11 `trial_evidence` | trial records behind class `trialled` | `trial_record` | `neura_self_issued`, or `host`, `independent_verifier` | N type |
| 31 | IC-11 `meter_evidence` | meter records behind class `metered` | `metered_data_record` | `meter_operator`, `host` (never self-issued) | N type |
| 32 | IC-11 `baseline_method` | method fixed in advance | `baseline_method` | as row 28 | N |
| 33 | IC-11 SLA `evidence` | the customer SLA document | `service_level_agreement` | `customer`, `host` | N type |
| 34 | IC-11 `calibration_evidence`, IC-2 `ModelledRun.evidence` | BMS history, design data, calibration data | `operational_data_record`; offers: `connection_offer` | `host`, `customer`, `oem`, `dno` | N type, E |
| 35 | IC-2 `registered_evidence` | a recorded publisher response | `regulatory_publication` | publisher's role | E |
| 36 | IC-1 `source` | the signed offer or agreement | `connection_offer`, `connection_agreement` | `dno`, `idno` | E |

Notes:
- **a.** Neura's toolkit writes the commissioning record. It becomes evidence only when a third party issues
  or signs it. Neura cannot self-issue it: ADR-D10 limits the Neura role to pre-registration records.
- **b.** Neura is a party to the MW agreement. Neura's own signature is not an evidence object, for the same
  reason. The criterion is met by one object per counterparty.

Gaps, each with its owner. None blocks this package:

| Gap | What is missing | Owner | Why not closed here |
|---|---|---|---|
| G1 | WP-GRC-07's "decision" is Neura's own decision. It is not a third-party document and not a pre-registration record, so under ADR-D10 (b) it cannot be an evidence object | Coordinator: reword the criterion (the opinion is evidence; the decision is an ADR). See owner question 2 | Adding it would widen the Neura role past the owner's limit |
| G2 | Assessment SOW, MSA, site-access agreement, lender reliance letter and performance wrapper have no type. "With its required fields" cannot be expressed: IC-4 has no per-type fields | WP-GRC-09 (P4, gated D-1, D-8) | The template set depends on D-1. A types-only minor later touches IC-4 alone (§5). Per-type fields would be a shape change |
| G3 | The licence term ("derived outputs may appear in lender packs") is not a field. IC-4 records that the licence exists and who issued it | WP-DATA-09 | The consumer reads the document; a term register is its product |
| G4 | The G99/G100 checklist items beyond the two approvals (witness test, PGMD) are not yet listed | WP-CONN-08 (P4) | The item list does not exist yet |
| G5 | A sign-off cannot say which control mode it covers. One `control_authority_signoff` at site scope would open every mode. It is kept apart from `critical_facilities_signoff` so a resilience-floor sign-off (WP-SITE-04) can never open closed loop | WP-THERM-08 (P6) | Needs either one type per mode or a subject field. P6; decide with the mode list |
| G6 | No type for DFS registration, ancillary response-unit registration, or a PPA or supply contract. Which type each stream requires is WP-UW-01's map | WP-UW-01 (gated D-3) | I could not verify the GB document names; guessed members rot a contract. Types-only minors are cheap after this release |

The executor commits this inventory as `src/gridq/contracts/IC4_INVENTORY.md` (same table, same columns),
with a test that every backticked type and role in it is a member of the tagged enums (§6 step 6).

## 4. The exact IC-4 1.1.0 delta (`evidence.py`)

### 4.1 Constants

```python
CONTRACT_VERSION: Final = "1.1.0"
SUPPORTED_VERSIONS: Final = ("1.0.0", "1.1.0")
GIT_TAG: Final = "contracts/ic4-evidence-object/v1.1.0"
```

`EvidenceObject.contract_version: Literal["1.0.0", "1.1.0"] = CONTRACT_VERSION`.

### 4.2 `EvidenceType`: 16 new members (15 become 31), appended after `COUNTERPARTY_RATE`

| Member | Value | One-line meaning for the docstring comment |
|---|---|---|
| `DATA_PROCESSING_AGREEMENT` | `data_processing_agreement` | An executed customer DPA |
| `DATA_SHARING_AGREEMENT` | `data_sharing_agreement` | An executed DSA with a DNO, NESO or Elexon |
| `NON_DISCLOSURE_AGREEMENT` | `non_disclosure_agreement` | An executed NDA |
| `LEGAL_OPINION` | `legal_opinion` | Counsel's written opinion |
| `CONFORMITY_CERTIFICATE` | `conformity_certificate` | A certificate from an accredited body (Cyber Essentials Plus, ISO 27001, meter calibration) |
| `TECHNICAL_REPORT` | `technical_report` | A third party's report of a technical assessment (RMS/EMT, independent engineer) |
| `DATA_LICENCE` | `data_licence` | A data provider's licence or written authorisation |
| `METHOD_AGREEMENT` | `method_agreement` | A counterparty's signed agreement to a definition, method or tolerance fixed in advance |
| `SERVICE_LEVEL_AGREEMENT` | `service_level_agreement` | A customer or host SLA document |
| `COMMISSIONING_RECORD` | `commissioning_record` | A commissioning record issued or signed by a third party |
| `CONTROL_AUTHORITY_SIGNOFF` | `control_authority_signoff` | A host's sign-off that Neura may write to site plant |
| `FMAR_REGISTRATION` | `fmar_registration` | An asset's record in the flexibility market asset register |
| `METERED_DATA_RECORD` | `metered_data_record` | Meter data issued by the meter operator or host |
| `OPERATIONAL_DATA_RECORD` | `operational_data_record` | A third party's own data extract (BMS history, design data, constraint data) |
| `BASELINE_METHOD` | `baseline_method` | A counterfactual or baseline method document, registered before the data it will judge |
| `TRIAL_RECORD` | `trial_record` | The record or report of a flex trial |

### 4.3 `IssuerRole`: 6 new third-party members (16 become 22), appended after `INDEPENDENT_ENGINEER`

`LEGAL_ADVISER = "legal_adviser"`, `CERTIFICATION_BODY = "certification_body"`,
`DATA_PROVIDER = "data_provider"`, `FUNDER = "funder"`, `RESEARCH_PARTNER = "research_partner"`,
`INDEPENDENT_VERIFIER = "independent_verifier"`.

`research_partner` and `independent_verifier` are separate because the spec keeps PSAL's verifier
independence apart from its model-development role (build-or-partner table, and B6-05).

`IssuerRole`'s docstring keeps its first sentence true: it has no Neura member. It gains a pointer to
`SelfIssuerRole`.

### 4.4 The restricted Neura role

```python
class SelfIssuerRole(StrEnum):
    """Neura as issuer of its own pre-registration records (ADR-D10 option b). The value is the
    self-issued marker: it is on the wire in every such object. Not an `IssuerRole`, so it can
    never be a publisher role (IC-2) or the party an instruction serves (IC-11)."""
    NEURA_SELF_ISSUED = "neura_self_issued"

#: The only evidence types Neura may issue itself. Third parties may issue them too.
SELF_ISSUABLE_TYPES: Final[frozenset[EvidenceType]] = frozenset(
    {EvidenceType.BASELINE_METHOD, EvidenceType.TRIAL_RECORD}
)

class Issuer(ContractModel):
    name: NonEmptyStr
    role: IssuerRole | SelfIssuerRole
    reference: NonEmptyStr | Unknown
```

`EvidenceObject.is_self_issued -> bool`: `isinstance(self.issuer.role, SelfIssuerRole)`.

Checked in memory against pydantic 2.13.4 strict mode: `"dno"` parses to `IssuerRole`,
`"neura_self_issued"` to `SelfIssuerRole`, `"neura"` is refused; the schema is
`anyOf [IssuerRole, SelfIssuerRole]`.

### 4.5 Validation rules added to `EvidenceObject` (in `_check_document_and_dates` or a new `after` validator)

Write the two 1.0.0 member sets out literally, not computed from the enums:
`V1_0_0_EVIDENCE_TYPES` (the 15) and `V1_0_0_ISSUER_ROLES` (the 16).

| Rule | Refuses | Message must contain |
|---|---|---|
| R1 down-level type | `contract_version == "1.0.0"` with a type outside `V1_0_0_EVIDENCE_TYPES` | `not in IC-4 1.0.0` |
| R2 down-level role | `contract_version == "1.0.0"` with a role outside `V1_0_0_ISSUER_ROLES` (so any self-issued object) | `not in IC-4 1.0.0` |
| R3 restricted issuer | `is_self_issued` with a type outside `SELF_ISSUABLE_TYPES` | `Neura may self-issue only pre-registration records` |
| R4 no self-verification | `is_self_issued` with `verification` = `Verified` and `verifier_role == neura_internal` | `cannot verify its own document` |

A self-issued object may be `Unverified`, or `Verified` by `independent_third_party` (a countersignature).

Unchanged and still tested: every assumption guard, the zero-byte hash rule, the date rules, finality,
`serialise_evidence`. The 1.0.0 tests for them run unedited.

### 4.6 The consumer refusal functions

```python
class EvidenceRefused(ValueError): ...
class SelfIssuedEvidenceRefused(EvidenceRefused): ...
class SyntheticEvidenceRefused(EvidenceRefused): ...
class EvidenceNotFound(LookupError): ...

def require_third_party(obj: EvidenceObject) -> EvidenceObject:
    """The refusal ADR-D10 requires. Returns ``obj`` when a third party issued it.
    Raises `SelfIssuedEvidenceRefused` when Neura issued it. Call this wherever
    third-party evidence is required; that is everywhere unless the README table says otherwise."""

def resolve_ref(
    ref: EvidenceRef,
    objects: Iterable[EvidenceObject],
    *,
    admit_self_issued: bool = False,
    include_synthetic: bool = False,
) -> EvidenceObject:
    """Find the object a reference points at, and refuse what the caller did not ask for."""
```

Behaviour:
- `require_third_party`: `TypeError` unless `type(obj) is EvidenceObject`. It re-validates from
  `canonical_json(obj)`, as `serialise_evidence` does, so a `model_construct` bypass is caught. Then it
  raises `SelfIssuedEvidenceRefused` if `obj.is_self_issued`. The message names the type and the hash.
- `resolve_ref`, in this order:
  1. no object whose `content_hash()` equals `ref.evidence_hash`: `EvidenceNotFound`;
  2. `obj.contract_version != ref.contract_version`: `EvidenceRefused` (the reference's version claim is
     checked here, where the object is in hand);
  3. `record_class == "synthetic_fixture"` and not `include_synthetic`: `SyntheticEvidenceRefused`;
  4. `obj.is_self_issued` and not `admit_self_issued`: `SelfIssuedEvidenceRefused` (calls
     `require_third_party`);
  5. return the object.
- `assess(..., admit_self_issued: bool = False)`: a self-issued object is skipped unless admitted, exactly
  as a synthetic fixture is skipped unless included. Existing calls are unchanged.
- `EvidenceCheck` gains `best_is_self_issued: bool` (`False` when absent). `EvidenceCheck` has no frozen
  schema and no stored instances. A consumer that admits self-issued objects needs it to label the result.
- `evidence_ref(obj)` sets `contract_version=obj.contract_version`, not the module constant. Today it would
  label a reference to a 1.0.0 object as 1.1.0.

Add all new names to `__all__`. Export `require_third_party` and `resolve_ref` from
`gridq/contracts/__init__.py`.

The README gains this table. It is the consumer rule; the functions enforce its default:

| Slot or use | Self-issued admitted? |
|---|---|
| Registering or looking up a baseline method (WP-LEDGER-04, WP-THERM-09) | Yes, explicitly |
| IC-11 `baseline_method`, and `trial_evidence` of a `trialled` surface | Yes, explicitly; any output that cites it prints "self-issued by Neura" |
| IC-11 `meter_evidence`, SLA `evidence`, `calibration_evidence` | No |
| IC-1 `source`; every IC-2 reference | No |
| Eligibility (`underwriting_basis_v1`), the DPA guard, lender packs, the claims register | No |
| Anything not listed | No |

### 4.7 What IC-4 1.1.0 offers WP-GRC-05 (the guard itself is not planned here)

- `EvidenceType.DATA_PROCESSING_AGREEMENT` and `DATA_SHARING_AGREEMENT`, with the values the spec names.
- `assess()` with its 1.0.0 call shape. The spec's `require_dpa` runs unchanged; a DPA can never be
  self-issued (R3), and the default would skip it anyway.
- `EvidenceCheck` for `dpa_evidence.json`, now with one more field, `best_is_self_issued`.
- `serialise_evidence` and `EvidenceObject.model_validate_json` for the files-first registry.
- A synthetic DPA fixture (valid, verified, `NoExpiry`, `customer`, `SiteScope`) for the guard's tests.
- Public intake decision (a), "show once, store nothing", needs **nothing** from IC-4: no visitor-issued
  type and no visitor issuer role are added.

### 4.8 `EvidenceRef`

```python
class EvidenceRef(ContractModel):
    kind: Literal["evidence_ref"]
    contract: Literal["IC-4"]
    contract_version: Version1x      # was Literal["1.0.0"]
    evidence_hash: Sha256
```

`Version1x` is new in `_base.py`:
`Annotated[str, StringConstraints(pattern=r"^1\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")]`. Checked in memory: accepts
`1.0.0` and `1.10.0`; refuses `2.0.0`, `1.1`, `1.01.0`, `0.9.0` and a trailing newline.

## 5. Design note: the `EvidenceRef` pin (goes into the README as its own section)

**Choice: a reference accepts a stated compatible range, any `1.x.y`. A contract's own version stays an
exact enumerated literal.** The same rule applies to `ConnectionAgreementRef`, `ScenarioSetRef` and
`AvailabilityRef`.

Reasons:
1. **The hash is the binding, not the version.** `evidence_hash` covers the object's own
   `contract_version`. The version inside a reference is a claim nobody can check without the object. An
   exact pin there protects nothing.
2. **An exact pin would orphan evidence.** If IC-1 1.1.0 could reference only IC-4 1.1.0 objects, a
   connection offer registered at 1.0.0 could not be cited without re-registering it under a new hash.
3. **Safety lives at dereference.** Whether an object is self-issued is a property of the object.
   `resolve_ref` refuses a version mismatch, an object of a version the installed IC-4 cannot parse, and a
   self-issued object, in the one place where the object is in hand.
4. **Cost.** At least one more IC-4 minor is already known (gaps G2 and G6). With an exact pin it would
   re-version IC-1, IC-2, IC-11, and by then IC-12 and IC-8 as well.

Why a pattern and not the list `["1.0.0", "1.1.0"]`: a list changes with every IC-4 minor, which is the
cascade again.

**What a 1.0.0 reader does with a 1.1.0 object: it refuses it.** The 1.0.0 model and schema pin
`contract_version` to `"1.0.0"`. This is correct. A 1.0.0 reader has no concept of self-issued and no
`require_third_party`, so it must not read a Neura-issued record as third-party evidence. A 1.0.0 reader of
IC-1, IC-2 or IC-11 likewise refuses a 1.1.0 instance, and refuses a reference whose version is not
`"1.0.0"`.

**What a 1.1.0 reader does with a 1.0.0 object: it accepts it,** same meaning, same hash.

**Cost of the next IC-4 minor, stated now:**

| The next minor adds | Contracts re-versioned |
|---|---|
| Evidence types only | IC-4 |
| A self-issuer role or a wider `SELF_ISSUABLE_TYPES` | IC-4 (and an owner decision: it widens ADR-D10) |
| A third-party issuer role | IC-4, IC-2 and IC-11, because their schemas embed the `IssuerRole` list. Not IC-1 |
| A field on `EvidenceObject` | Major. Every 1.x hash would change |

Each such release also appends one string to `SUPPORTED_VERSIONS` and to the model's own version literal.

The roadmap's third criterion ("a test fails if any contract module pins an IC-4 version other than the
current one") is met in this form: no module outside `evidence.py` holds an IC-4 version at all, and the
range `EvidenceRef` accepts must admit `evidence.CONTRACT_VERSION` (§6 step 6).

## 6. The re-version of IC-1, IC-2 and IC-11

Common to all three:
- `CONTRACT_VERSION = "1.1.0"`, `SUPPORTED_VERSIONS = ("1.0.0", "1.1.0")`, `GIT_TAG` ends `/v1.1.0`.
- Own `contract_version: Literal["1.0.0", "1.1.0"] = CONTRACT_VERSION`.
- **Down-level rule:** an instance labelled `"1.0.0"` may hold only what the 1.0.0 schema allowed. Each
  embedded reference must carry `contract_version == "1.0.0"`, and each `IssuerRole` value must be in
  `evidence.V1_0_0_ISSUER_ROLES`. One `after` validator on the top-level model. Message contains
  `not in IC-<n> 1.0.0`.
- Module and class docstrings say 1.1.0. No other docstring text changes. The IC-11 sentences on firm kW
  are **not** touched.
- No field added, removed or renamed. No other validation changed.

| Contract | What changes | Why minor |
|---|---|---|
| **IC-1** `connection.py` | `EvidenceBacked.evidence` now accepts a `1.x` IC-4 reference (inherited from `EvidenceRef`). Down-level: a 1.0.0 agreement's reference must be `1.0.0` | One value set widens. The three archetype fixtures and their hashes are untouched |
| **IC-2** `curtailment.py` | (1) IC-4 references accept `1.x`. (2) `ConnectionAgreementRef.contract_version` and `ScenarioSetRef.contract_version` become `Version1x`; `scenario_set_ref()` uses the set's own version. (3) `publisher_role` inherits the six new third-party roles. Down-level covers all three | Value sets widen. Weights, series, events and the consistency checks are untouched. Both fixtures and their hashes are untouched, and the modelled fixture still links to the IC-1 hybrid fixture by the same hash |
| **IC-11** `thermal_flex.py` | (1) IC-4 references accept `1.x`. (2) `AvailabilityRef.contract_version` becomes `Version1x`; `availability_ref()` uses the surface's own version. (3) `on_behalf_of` inherits the six new roles. Down-level on the surface (references) and on the instruction (`on_behalf_of`, surface reference). The acknowledgement changes only its own version literal | Value sets widen. Cells, curves, rebound, limits, `check_instruction`, `verify_acknowledgement` and the meaning of a declared kW are untouched. All six fixtures and hashes are untouched |

The CHANGELOG entry for each of the three names only this: "version references accept the 1.x range; own
version 1.1.0; (IC-2, IC-11) the IC-4 third-party role list it embeds grew by six".

`SelfIssuerRole` is not a valid `publisher_role` or `on_behalf_of`. That needs no code: the fields are typed
`IssuerRole`. It needs a test.

## 7. Order of work, with the check after each step

Work on a branch off `31da157` (suggested `roadmap/wp-bo-06`). Commit after each green step. Create no tag.

**The standard check block.** Run all of it after every step, from the worktree root:

```
C1  PYTHONPATH="$PWD/src" ../uk/.venv/bin/python -m pytest -o addopts="" -q -W ignore tests/contracts
C2  PYTHONPATH="$PWD/src" ../uk/.venv/bin/python -m gridq.contracts --check
C3  shasum -a 256 src/gridq/contracts/schemas/*.v1.0.0.schema.json        # equals the §0 table, all 8 files
C4  git diff --exit-code 31da157 -- src/gridq/contracts/schemas/'*.v1.0.0.schema.json' \
        tests/contracts/fixtures/'ic*.json' tests/contracts/fixtures/'ic*.jsonl' \
        src/gridq/contracts/identity.py src/gridq/contracts/audit.py
C5  git diff 31da157 -- tests/contracts/_contracts_support.py | grep '^-' | grep -c 'sha256:'   # must print 0
C6  ../uk/.venv/bin/python -m ruff check .
C7  ../uk/.venv/bin/python -m ruff format --check src tests .github/ci
C8  PYTHONPATH="$PWD/src" ../uk/.venv/bin/python -m mypy
```

C6 to C8 are the CI `lint` job's three commands (`.github/workflows/ci.yml`), on the pinned tool versions.
C1 must never show fewer than 563 passed, and the skip count stays 5. If a contract source is edited twice
within a second, clear `src/gridq/contracts/__pycache__` first (HANDOFF_LOG warning, 2026-11-17).

### Step 0. Preflight
- Confirm HEAD is `31da157` or a descendant that leaves §0's files unchanged, the tree is clean, and no
  `contracts/*/v1.1.0` tag exists.
- Run the whole block. **Check:** it reproduces §0.

### Step 1. Guard the archive before touching a model
- `__main__.py`: add `ARCHIVED: dict[str, str]`, file name to sha256, for the six files in §0's first
  table. `--check` also verifies each archived file's bytes against its hash (`DRIFT` and exit 1 if not).
  `--write` refuses to write any path in `ARCHIVED` whose new text differs from the file (exit 2, nothing
  written).
- Tests: the committed archive passes; a tampered copy in `tmp_path` fails `--check`; `--write` against a
  drifted model leaves the archived file untouched. Give `main` an optional schema-dir argument for this, or
  monkeypatch `SCHEMA_DIR`.
- **Check:** block green. C2 prints 8 `ok` lines plus 6 archive lines.

### Step 2. `_base.py`: `Version1x`
- Add the type and export it. Tests for the accept and refuse cases in §4.8.
- **Check:** block green. C2 unchanged, which proves IC-13 and every 1.0.0 schema ignore the addition.

### Step 3. IC-4 internals (nothing here reaches another contract's schema)
- §4.1, §4.2, §4.4, §4.5, §4.6 and `assess`. **Not yet:** §4.3 (roles) and §4.8 (`EvidenceRef`).
- New fixtures in `tests/contracts/fixtures/v1_1_0/` (a new folder, so the existing globs and the
  `len(...) == n` assertions in the 1.0.0 tests are undisturbed):
  `ic4_data_processing_agreement`, `ic4_legal_opinion`, `ic4_self_issued_baseline_method` (unverified),
  `ic4_self_issued_trial_record` (verified by an independent third party), all `.synthetic.json`, all
  `record_class: synthetic_fixture`, issuer names containing `SYNTHETIC`. The legal-opinion fixture waits
  for step 4 (it needs `legal_adviser`).
- `_contracts_support.py`: `IC4_V110_FIXTURES` and a separate `GOLDEN_HASHES_V110`. Existing entries are
  not edited (C5).
- New test file `tests/contracts/test_ic4_v110.py`. At least:
  - each new fixture validates, is 1.1.0, and matches its golden hash; key order and `PYTHONHASHSEED` do
    not change it (reuse the canonical-hash tests by parametrising over the new lists);
  - R1 to R4, one test each, plus the admitted cases beside them;
  - `"neura_self_issued"` on a `connection_offer` is refused; on a `baseline_method` it is accepted and
    `is_self_issued` is true;
  - `require_third_party`: returns a third-party object; raises on each self-issued fixture; `TypeError`
    for a non-`EvidenceObject`; catches a `model_construct` object whose role was swapped;
  - `resolve_ref`: each of the five outcomes in §4.6, in order;
  - `assess`: a self-issued match is `ABSENT` by default and found with `admit_self_issued=True`, with
    `best_is_self_issued` true; a third-party match outranks nothing it did not outrank before;
  - `evidence_ref` of a 1.0.0 fixture says `1.0.0`; of a 1.1.0 fixture says `1.1.0`;
  - the two literal 1.0.0 sets equal the enum lists read from the archived
    `ic4_evidence_object.v1.0.0.schema.json`;
  - every assumption test in `test_ic4_evidence_object.py` also passes on a 1.1.0 self-issued payload
    (an assumption merged onto a self-issued baseline method is still refused).
- Edits allowed in `test_ic4_evidence_object.py`, and only these: `CONTRACT_VERSION == "1.1.0"`; the two
  per-fixture version assertions compare with `"1.0.0"`; `test_evidence_ref_points_at_the_content_hash`
  compares with `obj.contract_version`.
- Run `--write`. It creates `ic4_evidence_object.v1.1.0.schema.json` and rewrites the rest byte-identical.
- **Check:** block green. C2 lists the new 1.1.0 file as live and the 1.0.0 file under the archive.
  `git status` shows no modified schema, one new one.

### Step 4. The coupled change: roles, `EvidenceRef`, and IC-1, IC-2, IC-11 to 1.1.0
These cannot be separated: the moment `EvidenceRef` or `IssuerRole` changes, three schemas drift. Do them
in this order inside the step, and run the block once at the end.
1. `evidence.py`: §4.3 and §4.8.
2. `connection.py`, `curtailment.py`, `thermal_flex.py`: §6.
3. `--write`. It must create exactly five files: `ic1_connection_agreement`,
   `ic2_curtailment_scenario_set`, `ic11_thermal_flex_availability`, `ic11_flex_instruction`,
   `ic11_flex_acknowledgement`, each `.v1.1.0.schema.json`. If it exits 2, an archived file would have
   changed: STOP.
4. Fixtures in `fixtures/v1_1_0/`, with golden hashes in `GOLDEN_HASHES_V110`:
   - `ic4_legal_opinion` (from step 3);
   - `ic1_hybrid_evidence_backed`: the hybrid archetype's terms, version 1.1.0, `source` an
     `evidence_backed` reference;
   - `ic2_modelled_v110`: a modelled set at 1.1.0 with a `1.1.0` IC-1 reference to the fixture above, built
     by a new function in `_ic2_fixtures.py`;
   - `ic11_surface_trialled_self_issued`: a `trialled` surface at 1.1.0 whose `trial_evidence` and
     `baseline_method` are `1.1.0` references to the hashes of the two self-issued IC-4 fixtures, built by
     a new function in `_ic11_fixtures.py`. Existing builder functions and their output must not change;
     the "committed equals a fresh build" tests prove it.
5. Tests, in `test_ic1_…`, `test_ic2_…`, `test_ic11_…` or new `*_v110.py` files:
   - each new fixture validates and matches its golden hash;
   - for each contract, the down-level rule: take a 1.0.0 fixture, set one reference to `1.1.0` (and, for
     IC-2 and IC-11, one role to `legal_adviser`), and it is refused; the same payload relabelled `1.1.0`
     is accepted;
   - `publisher_role` and `on_behalf_of` refuse `"neura_self_issued"` at both versions;
   - a reference version of `2.0.0` or `0.9.0` is refused at both versions (the existing IC-1 test for
     `0.9.0` stays as it is);
   - the cross-link: `resolve_ref` on the trialled surface's references refuses by default and returns
     the object with `admit_self_issued=True, include_synthetic=True`;
   - `availability_ref` and `scenario_set_ref` carry the instance's own version.
   Edits allowed in the three existing test files, and only these: the `CONTRACT_VERSION == "1.0.0"`
   assertions become `"1.1.0"`; per-fixture version assertions compare with `"1.0.0"`.
- **Check:** block green. `git status --short src/gridq/contracts/schemas` shows six `??` files in total
  and no ` M`. `git status --short tests/contracts/fixtures` shows only the new folder.

### Step 5. Schema delta tests (the mechanical proof of "naming only that change")
New `tests/contracts/test_schema_delta_v110.py`. For each contract, load the archived 1.0.0 file and the
1.1.0 file, diff them as JSON, and assert the set of changed paths equals a pinned list. Allowed kinds:
- `properties.contract_version`: `const`/`default` `1.0.0` becomes `enum [1.0.0, 1.1.0]`, default `1.1.0`;
- `contract_version` inside `$defs.EvidenceRef`, `ConnectionAgreementRef`, `AvailabilityRef`: `const`
  becomes the `Version1x` `pattern`;
- `$defs.IssuerRole.enum`: exactly the six values appended (IC-2 and the IC-11 instruction);
- a `description` whose only change is the token `v1.0.0` to `v1.1.0`;
- IC-4 only: `$defs.EvidenceType.enum` plus sixteen, `$defs.SelfIssuerRole` added, `Issuer.role` becomes
  the `anyOf`, and the `EvidenceObject` description.

Also assert: `required` lists and every property name are identical between the two versions of each
schema (no field added, removed or renamed).
- **Check:** block green. If a path outside the list appears, STOP; do not widen the list.

### Step 6. The pin test and the inventory test
- `test_no_contract_module_pins_an_ic4_version`: by AST, no module in `gridq/contracts/` other than
  `evidence.py` contains a class with `contract: Literal["IC-4"]` or the string `"IC-4"` beside a version
  literal; in every live schema, each `$defs.EvidenceRef.properties.contract_version` is the `Version1x`
  pattern; `re.fullmatch(pattern, evidence.CONTRACT_VERSION)`; `SUPPORTED_VERSIONS[-1] == CONTRACT_VERSION`
  in all four modules.
- Commit `src/gridq/contracts/IC4_INVENTORY.md` (§3). Test: every backticked value in its type and role
  columns is a member of `EvidenceType`, `IssuerRole` or `SelfIssuerRole`; every row's status is E, N or a
  G-number that appears in the gap table with an owner.
- **Check:** block green.

### Step 7. Mutation checks: every new guard
Reuse the IC-11 harness's rules: the mutant is applied in memory in a fresh interpreter; red means pytest
exit 1; a mutant that cannot load exits 3; a control run on the unmutated source must pass. Move the runner
into `_contracts_support.py` with the module name as a parameter, and leave `test_ic11_thermal_flex.py`'s
own list working (add to it; do not rewrite it). Each mutant's `old` string must occur exactly once.

| # | Module | Guard reverted | Test that must go red |
|---|---|---|---|
| 1 | evidence | R1 down-level type | 1.0.0 payload with a 1.1.0 type is refused |
| 2 | evidence | R2 down-level role | 1.0.0 payload with a 1.1.0 role is refused |
| 3 | evidence | R3 restricted issuer | self-issued `connection_offer` is refused |
| 4 | evidence | R4 self-verification | self-issued, `neura_internal` verified is refused |
| 5 | evidence | `require_third_party` raise | raises on a self-issued fixture |
| 6 | evidence | `require_third_party` re-validation | `model_construct` swap is caught |
| 7 | evidence | `assess` skip of self-issued | self-issued match is `ABSENT` by default |
| 8 | evidence | `resolve_ref` self-issued refusal | refuses by default |
| 9 | evidence | `resolve_ref` synthetic refusal | refuses a fixture by default |
| 10 | evidence | `resolve_ref` version match | refuses a reference whose version differs from the object's |
| 11 | evidence | `evidence_ref` uses the object's version | reference to a 1.0.0 object says `1.0.0` |
| 12 | evidence | `SELF_ISSUABLE_TYPES` widened by one type | a test pins the set to exactly the two types |
| 13 | _base | `Version1x` pattern loosened to any major | `2.0.0` reference is refused |
| 14 | connection | IC-1 down-level | 1.0.0 agreement with a `1.1.0` reference is refused |
| 15 | curtailment | IC-2 down-level, references | as above for a set |
| 16 | curtailment | IC-2 down-level, role | 1.0.0 set with `legal_adviser` is refused |
| 17 | thermal_flex | IC-11 surface down-level | as above for a surface |
| 18 | thermal_flex | IC-11 instruction down-level | 1.0.0 instruction with a new role or a `1.1.0` surface reference is refused |
| 19 | __main__ | archive hash check | tampered archived schema fails `--check` |
| 20 | __main__ | `--write` archive refusal | drifted model cannot overwrite an archived file |

The fifteen existing IC-11 mutants must still be red.
- **Check:** block green; 20 new mutants red with exit 1, each with a passing control.

### Step 8. Documents
- `README.md`: the version table (four contracts at 1.1.0, both schema files listed, new tags); "IC-4:
  what evidence means here" rewritten for ADR-D10 (b), including the §4.6 table; the §5 design note as its
  own section; "Changing a contract" gains the archive step and the down-level rule; the fixtures section;
  the mutation count.
- `CHANGELOG.md`: one dated entry, four sub-entries. IC-4 lists types, roles, the restricted issuer, the
  functions and the rules. IC-1, IC-2 and IC-11 name only the §6 change. State that IC-13 is unchanged and
  all eight 1.0.0 schemas are byte-identical.
- `__init__.py` docstring and exports. Module docstrings.
- `docs/changelog.md`, and a HANDOFF_LOG entry by
  `python -m gridq.dev.team_sync add-handoff --summary "…"`.
- **Check:** block green (docstring edits can move a schema description: C2 and step 5 catch it).

### Step 9. Final gate, then hand to the coordinator
- The block once more, from a clean `__pycache__`.
- The CI fast tier once, since `gridq.contracts` ships in it:
  `PYTHON=../uk/.venv/bin/python PYTHONPATH="$PWD/src" .github/ci/run_tests.sh fast -q`.
- Report: pass and skip counts, mutant count, mypy file count, the list of new files, and the output of
  C3 beside §0's table. Do not tag, merge or edit the roadmap.

## 8. Tags (the coordinator creates them)

Four annotated tags, all on the **same** commit: the commit on `roadmap/p1-foundations` that lands this
package (the merge commit, as with IC-11).

```
contracts/ic4-evidence-object/v1.1.0
contracts/ic1-connection-agreement/v1.1.0
contracts/ic2-curtailment-scenario-set/v1.1.0
contracts/ic11-thermal-flex/v1.1.0
```

- No IC-13 tag. The five `v1.0.0` tags are never moved or deleted.
- Before tagging, the coordinator re-runs C2 and C3 on that commit.
- After tagging, the coordinator's roadmap duties (Atlas `CLAUDE.md` rule 8): WP-BO-06 status with
  evidence; gap G1's rewording of the WP-GRC-07 criterion; gaps G2 to G6 noted on their packages; the DPA
  guard spec's P1 marked delivered; re-render and republish the Build Map.

## 9. STOP conditions

Stop, change nothing further, and report to the coordinator if any of these happens:

1. Any of the eight `v1.0.0` schema files differs from §0 (C3 or C4), or `--write` exits 2.
2. Any existing fixture file changes, or any existing `GOLDEN_HASHES` entry changes (C4, C5).
3. An existing test needs an edit beyond the ones steps 3 and 4 list. In particular: the guard word list,
   `test_neura_or_self_cannot_be_an_issuer`, any assumption test, and any `len(...FIXTURES) == n`.
4. `identity.py`, `audit.py` or either IC-13 schema would change.
5. The step 5 schema delta shows a path outside the allowed kinds, or any field is added, removed or
   renamed in any contract. A new field on `EvidenceObject` is a major version, not this package.
6. A new enum value trips the guard word list and no honest rename exists.
7. The work seems to need a new dependency (`jsonschema`, `hypothesis` or any other).
8. A mutant is not red with exit 1, or its control fails, and the cause is not a typo in the mutant string.
9. C1 falls below 563 passed, or the skip count is not 5 and the real IC-2 fixtures are absent.
10. Anything outside `src/gridq/contracts/`, `tests/contracts/`, `docs/changelog.md` and the HANDOFF_LOG
    would need to change. That includes `dpa_guard.py` (WP-GRC-05), a registry (WP-BO-02), and any wiring
    of `require_third_party` into pricing, eligibility or a revenue stream (CLAUDE.md §2.0, D57: this
    package adds a contract function and no gate).
11. The meaning of a declared IC-11 kW, `FlexPoint`, `check_instruction` or `verify_acknowledgement` would
    change, in code or in words.
12. You conclude that an exact pin, a `self_issued` boolean field, or `neura` as a member of `IssuerRole`
    would be better. Report the reason; do not switch.
13. `Neura` would need to self-issue any type beyond `baseline_method` and `trial_record`. That widens
    ADR-D10 and is the owner's.
14. HEAD has moved so that §0's files differ, or a `v1.1.0` contract tag already exists.

## 10. Questions for the owner

Neither blocks the build. Each has a default that the plan already follows.

1. **A `trialled` surface that rests only on Neura's own trial records.** ADR-D10 (b) lets Neura issue
   trial records, marked self-issued. The plan's default: IC-4 refuses them everywhere unless a consumer
   opts in; the only opt-ins are the baseline-method slots and `trial_evidence` of a `trialled` surface; and
   any output that cites one says "self-issued by Neura". Lender packs and eligibility never opt in. Is
   that the line you want, or should a lender-facing output refuse to call such a surface `trialled` until
   a third party issues or countersigns the record?
2. **WP-GRC-07, "opinion and decision are recorded as evidence objects".** Counsel's opinion gets a type.
   The decision to rely on the exemption or apply is Neura's own, and under ADR-D10 (b) Neura may issue only
   pre-registration records. The plan's default: the decision is recorded as an ADR, not as evidence, and
   the coordinator rewords the criterion. Say if you want it otherwise; that would widen the Neura role.

## 11. Amendment, 2026-11-23

Written by Fable 5.2 after the executor finished steps 0 to 3 (`397262c`, `90cd65a`, `6cad84a`) and stopped
at step 4 under §9 condition 3. Where this section and §§2 to 10 disagree, this section wins. No source or
test was changed to write it.

### 11.1 The owner's two answers (recorded in `atlas/roadmap/adr/ADR-D10.md`, Decision)

- **§10 question 1:** "Yes, with the label (as planned)". Nothing changes.
- **§10 question 2:** "Widen Neura's role". The restricted Neura role now covers baseline methods, trial
  records **and Neura's own decision records**. The WP-GRC-07 criterion stands as written. Gap **G1 is
  withdrawn**, and so is the coordinator's reword in §8.

### 11.2 The widened role: design

**Type.** One new member, appended after `TRIAL_RECORD`:

| Member | Value | Docstring comment |
|---|---|---|
| `DECISION_RECORD` | `decision_record` | A recorded decision of the issuing party (for Neura: an ADR or board minute). Evidence that the decision was made on a date; never evidence that what it decides is true |

It passes the guard word list. `EvidenceType` now gains 17 members (15 become 32). A third party may issue
a `decision_record` too, as with the other two self-issuable types.

**What a self-issued decision record must carry.** No field is added. Two new rules, on
`is_self_issued and evidence_type is DECISION_RECORD` only:

| Rule | Refuses | Message must contain |
|---|---|---|
| R5 named decision | `issuer.reference` is `Unknown` | `a decision record names its decision` |
| R6 dated decision | `effective_from` is `Unknown` | `a decision record states when it takes effect` |

So a decision record always carries: the Neura entity (`issuer.name`), the decision's own id
(`issuer.reference`, for example the ADR id), the hash of the decision document, the date decided
(`issued_on`), the date it takes effect, and a scope. R3 and R4 already apply: Neura cannot mark its own
decision verified.

**Who may opt in.** Only a consumer that records, looks up or displays the decision itself, by that type,
and it prints "self-issued by Neura". Today that is WP-GRC-07's register. Nobody else. A decision record
never opens a gate, satisfies a consent, prices a stream, marks a claim true (WP-GRC-04) or appears as
third-party support in a lender pack. The customer-segment rule that WP-UW-02 consumes is a versioned
input; it may cite the decision's hash, but the eligibility engine does not admit the object.

**Admission is now per type.** With three self-issuable types, one of them general, a bare
`admit_self_issued=True` on `resolve_ref` would let a decision record sit in a baseline-method slot. So
`resolve_ref` gains `expected_type` (§11.4), and admitting a self-issued object without naming the type is
an error. `assess` already takes `evidence_type`, so it needs no change.

**A gap this exposed (G7, new).** IC-4 has no organisation-wide scope. A licence decision, a legal opinion,
a Cyber Essentials certificate, a data licence and the MW agreement are not about one site or one stream.
Owner: WP-BO-02 (the registry), with the next IC-4 minor. Not closed here: a new scope variant changes what
`scope_covers` and `assess` answer, which is a design of its own. It would touch IC-4 only. Until then such
documents are registered per site, as the DPA guard spec already does. The new fixtures are site-scoped.

### 11.3 Exact change to §4.4

```python
#: The only evidence types Neura may issue itself (ADR-D10 option b, widened by the owner on
#: 2026-11-23): its pre-registration records and its own decision records. Third parties may
#: issue these types too.
SELF_ISSUABLE_TYPES: Final[frozenset[EvidenceType]] = frozenset(
    {EvidenceType.BASELINE_METHOD, EvidenceType.TRIAL_RECORD, EvidenceType.DECISION_RECORD}
)
```

- `SelfIssuerRole`'s docstring: "pre-registration records" becomes "pre-registration and decision records".
- R3's message becomes `Neura may self-issue only its pre-registration and decision records (…)`. The
  prefix `Neura may self-issue only` is unchanged; the two tests in `test_ic4_v110.py` that match the
  longer text are updated.
- §4.5 gains R5 and R6 (§11.2).
- The module docstring and the `EvidenceObject` docstring name the three types.

### 11.4 Exact change to §4.6

```python
def resolve_ref(
    ref: EvidenceRef,
    objects: Iterable[EvidenceObject],
    *,
    expected_type: EvidenceType | None = None,
    admit_self_issued: bool = False,
    include_synthetic: bool = False,
) -> EvidenceObject:
```

Order of checks:
0. `admit_self_issued` is true and `expected_type` is `None`: `ValueError`, message contains
   `name the expected_type`. Raised before any lookup.
1. not found: `EvidenceNotFound`;
2. version differs from the reference's: `EvidenceRefused`;
3. `expected_type` is given and the object's type differs: `EvidenceRefused`, message contains
   `expected` and both type values;
4. synthetic and not included: `SyntheticEvidenceRefused`;
5. self-issued and not admitted: `SelfIssuedEvidenceRefused`;
6. return the object.

`require_third_party`, `assess` and `EvidenceCheck` are unchanged.

The README table of §4.6 gains two rows and one sentence:

| Slot or use | Self-issued admitted? |
|---|---|
| Recording, looking up or displaying Neura's own decision (WP-GRC-07), by type `decision_record` | Yes, explicitly; the output prints "self-issued by Neura" |
| Using a decision record to open a gate, satisfy a consent, price a stream, mark a claim true, or support a lender pack | No |

"A decision record is evidence that Neura decided something on a date. It is never evidence that the thing
decided is true, or that a third party agreed to it."

### 11.5 Exact change to §9 condition 13

> 13. Neura would need to self-issue any type beyond `baseline_method`, `trial_record` and
> `decision_record`, or a consumer other than the ones in the README table would need to admit a
> self-issued object. Either widens ADR-D10 and is the owner's.

### 11.6 Other sections that follow from the widening

- **§3 inventory, row 7:** WP-GRC-07 "decision recorded" is `decision_record`, issuer `neura_self_issued`,
  status N. Remove G1 from the gap table; add G7 (§11.2). `IC4_INVENTORY.md` is written this way.
- **§7 step 5:** the IC-4 allowed kinds read "`$defs.EvidenceType.enum` plus seventeen", and also allow
  `$defs.EvidenceType.description` (ruling 2 below).
- **§7 step 7:** mutant 12 pins the set to exactly the three types. Four more mutants, 24 in all:

| # | Module | Guard reverted | Test that must go red |
|---|---|---|---|
| 21 | evidence | R5 named decision | self-issued decision record with an unknown reference is refused |
| 22 | evidence | R6 dated decision | self-issued decision record with an unknown `effective_from` is refused |
| 23 | evidence | `resolve_ref` type check | a decision record behind a reference resolved with `expected_type=BASELINE_METHOD` is refused |
| 24 | evidence | `resolve_ref` admit-needs-type | `admit_self_issued=True` with no `expected_type` raises |

- **§7, the check block:** C1 must now never show fewer than **778** passed; the skip count stays 5.
- **§10:** both questions are answered; see §11.1.

### 11.7 Step 3A: an addendum the executor runs before step 4

All of it is in `evidence.py` and the executor's own step-3 test files. Nothing here reaches another
contract's schema.

1. `evidence.py`: `DECISION_RECORD`; §11.3; R5 and R6; §11.4; the `EvidenceType` docstring (ruling 2).
2. Fixture `fixtures/v1_1_0/ic4_self_issued_decision_record.synthetic.json`: `neura_self_issued`, a known
   reference, a known `effective_from`, `Unverified`, site scope, issuer name containing `SYNTHETIC`. Add
   its hash to `GOLDEN_HASHES_V110`.
3. Tests in `test_ic4_v110.py`:
   - the fixture validates, is self-issued, and matches its golden hash;
   - R5 and R6, one test each, and a third-party `decision_record` with an unknown reference is accepted;
   - R3 still refuses a self-issued `connection_offer`, `legal_opinion` and `conformity_certificate`;
   - `SELF_ISSUABLE_TYPES` equals exactly the three types;
   - `require_third_party` raises on the decision fixture; `assess` reports it `ABSENT` by default;
   - `resolve_ref`: checks 0 and 3 of §11.4; a decision record resolves only with
     `expected_type=DECISION_RECORD, admit_self_issued=True`; existing `resolve_ref` tests that admit
     self-issued objects now pass `expected_type`;
   - an assumption merged onto the decision fixture is still refused.
4. `--write`. It rewrites `ic4_evidence_object.v1.1.0.schema.json` in place. That is allowed: 1.1.0 is not
   tagged (the IC-11 pre-tag fixes set the precedent). It must change no other file.
- **Check:** the full block green; C1 at least 778 passed, 5 skipped. `git status --short
  src/gridq/contracts/schemas` shows exactly one modified file, the IC-4 1.1.0 schema. C3 equals §0.
  Commit as "WP-BO-06 step 3A".

Step 4 then runs as written in §7, with the rulings below. Its trialled-surface cross-link test passes
`expected_type` (`TRIAL_RECORD` and `BASELINE_METHOD`) when it admits.

### 11.8 Ruling on the IC-1 test edit

**Approved.** `tests/contracts/test_ic1_connection_agreement.py:86` uses `"1.1.0"` as its example of a
version to refuse. That was true at 1.0.0 and is false by design now. Change the value to `"1.0.1"`. You
may also add `("contract_version", "2.0.0")`, so the list equals the one in the IC-2, IC-4 and IC-11 tests.
Nothing else in that test changes. My step 4 list of allowed edits missed this line; the error is the
plan's, and stopping was right.

The same narrow licence applies to any other existing test that uses the literal `"1.1.0"` only as an
example of a refused version: replace it with `"1.0.1"`. Any other edit to an existing test is still
§9 condition 3.

### 11.9 Rulings on the seven departures

| # | Departure | Ruling |
|---|---|---|
| 1 | `Version1x` uses `[0-9]`, not `\d` | **Accepted, and better than the plan.** The plan's in-memory check did not try a non-ASCII digit. Step 5 pins the pattern by comparing with `_base.VERSION_1X_PATTERN`, not with a second copy of the string |
| 2 | `IssuerRole` and `EvidenceType` docstrings left unchanged | **`IssuerRole`: accepted, and it stays unchanged through step 4.** Its text is copied into the IC-2 and IC-11 schemas, and §4.3's "gains a pointer" would have moved a description outside step 5's allowed kinds. Put the pointer to `SelfIssuerRole` in a `#:` comment above the class. Its sentence is still true: `IssuerRole` has no Neura member. **`EvidenceType`: change it, in step 3A.** Its docstring reaches the IC-4 schema only, and "plus the two kinds WP-GRC-03 and A6 need" is now wrong for 32 members. Say what the list is now, and keep the sentence that there is no assumption-like member |
| 3 | Temporary `type: ignore` on `evidence_ref` | **Accepted until step 4 only.** Step 4 removes it when `EvidenceRef.contract_version` becomes `Version1x`. Step 4's check adds: `grep -c "type: ignore" src/gridq/contracts/evidence.py` prints 0, as it does at `31da157` |
| 4 | Some step-3 tests deferred to step 4 | **Accepted, with a condition.** The step 4 commit message lists each deferred test by name. By the end of step 4 every bullet of §7 step 3 has a named test, including the `ic4_legal_opinion` fixture and `evidence_ref` of a 1.1.0 object. A bullet with no test is a STOP |
| 5 | The v1.1.0 hash tests are in their own file | **Accepted.** It leaves `test_contracts_canonical_hash.py` unedited, which is what C4 and §9 condition 3 want |
| 6 | `require_third_party` judges the re-validated object | **Accepted.** That is the intent of §4.6: the object is judged as the wire would carry it, and the caller's own object is returned only after that copy passes |
| 7 | The `evidence.py` module docstring is rewritten | **Accepted.** It is not in any schema. It rightly drops "a self-issued document" from the list of things 1.0.0 refused, which the code never did. Step 3A updates it to name decision records |
