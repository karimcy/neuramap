# IC-4 evidence inventory (WP-BO-06)

Where the roadmap needs an IC-4 evidence object, and which `EvidenceType` and issuer role meet it
in IC-4 1.2.0. Copied from the WP-BO-06 plan (`docs/shared_work/wp_bo06_plan.md` §3), with the
owner's widening of 2026-11-23 (plan §11: row 7 is a `decision_record`, G1 is closed and G7 is
added). WP-GRC-09 (IC-4 1.2.0 and IC-15, 2026-11-26) closes G2 at row 9 and adds G8. WP-OPS-01 (the candidate site dossier, 2026-11-26) adds G9. WP-SITE-05 (the on-site generation model, 2026-11-26) adds G10. `tests/contracts/test_ic4_pin_and_inventory.py` checks that every backticked type and role
below is a member of `EvidenceType`, `IssuerRole` or `SelfIssuerRole`, and that every status is E,
N or a gap listed below with an owner.

Method: every `acceptance` line in `roadmap.yaml` matching `evidence object`, `IC-4`, `evidence-object`,
`EvidenceRef` or `evidence registry` (30 lines in 25 packages; 24 of those packages say "evidence object"
itself, and four of the lines are WP-BO-06's own), plus `delivers` text and a second sweep for
`signed`, `certificate`, `opinion`, `pre-regist`. Then every `EvidenceRef` slot in IC-1, IC-2 and IC-11.
Of WP-BO-06's own lines, the first is this inventory and the fourth is row 37. Re-checked on 2026-11-25
against Atlas `0ed3764`; `test_every_roadmap_criterion_that_says_evidence_object_has_an_inventory_row`
fails when a package whose acceptance says "evidence object" has no row here (it runs when the Atlas
repo is checked out beside this one).

Status: **E** = a 1.0.0 member; **N** = added in 1.1.0 (row 9: in 1.2.0); **G** = gap, with its
owner.

| # | Package | Criterion (short) | Evidence type | Issuer role | Status |
|---|---|---|---|---|---|
| 1 | WP-GRC-03 | each §12 row has an evidence object | `regulatory_publication` | `ofgem`, `elexon`, `neso`, `government` | E |
| 2 | WP-GRC-04 | no claim true without an evidence object or test | any member | any | E |
| 3 | WP-GRC-05 | no customer dataset stored before its DPA object | `data_processing_agreement` | `customer` | N type |
| 4 | WP-GRC-05 | data-sharing agreement set (delivers) | `data_sharing_agreement` | `dno`, `idno`, `neso`, `elexon` | N type |
| 5 | WP-GRC-06 | Cyber Essentials Plus certificate | `conformity_certificate` | `certification_body` | N type, N role |
| 6 | WP-GRC-07 | opinion recorded | `legal_opinion` | `legal_adviser` | N type, N role |
| 7 | WP-GRC-07 | decision recorded | `decision_record` | `neura_self_issued` (ADR-D10 b, widened 2026-11-23) | N type |
| 8 | WP-GRC-08 | KPI tracker cites the MW definition object | `method_agreement` (row 26) | as row 26 | N |
| 9 | WP-GRC-09 | each template is an evidence-object type with required fields | 1.2.0: `assessment_statement_of_work`, `master_services_agreement`, `site_access_installation_agreement`, `lender_reliance_letter`, `performance_wrapper_agreement`; 1.1.0: `data_sharing_agreement`; 1.0.0: `aggregator_agreement`, `dno_flexibility_contract`. Required fields: the IC-15 template set | `customer`, `host`, `dno`, `idno`, `neso`, `elexon`, `aggregator`, `lender`, `insurer` | N types (1.2.0); **G8** |
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
| 21 | WP-BO-02 | stored objects pass IC-4; expiry changes eligibility | the ten kinds its delivers text lists | 1.0.0 roles | E |
| 22 | WP-BO-04 | no customer data before the NDA object | `non_disclosure_agreement` | `customer` | N type |
| 23 | WP-EDGE-07, WP-OPS-06 | signed commissioning record passes IC-4 | `commissioning_record` | `host`, `oem`, `independent_engineer` | N type; note a |
| 24 | WP-TEL-04 | each meter has certificate evidence | `meter_commissioning_certificate`; calibration: `conformity_certificate` | `meter_operator`, `certification_body` | E, N |
| 25 | WP-MKTGW-04 | FMAR record held as an evidence object | `fmar_registration` | `elexon` | N type |
| 26 | WP-OPS-02 | MW agreement registered, signed by each party | `method_agreement`, one object per counterparty, same document hash | `funder` (Innovate UK), `research_partner` (PSAL), `host` | N type, N roles; note b |
| 27 | WP-TWIN-03 | study report registered | `technical_report` | `research_partner`, `independent_engineer` | N type |
| 28 | WP-THERM-04, WP-LEDGER-04, WP-THERM-09 | baseline method registered before any trial | `baseline_method` | `neura_self_issued` (ADR-D10 b), or `independent_verifier` | N type, N roles |
| 29 | WP-CORE-03 | chassis v2 carries IC-4 references | none: an IC-4 reference in the 1.x range | | N (reference range; no type needed) |
| 30 | IC-11 `trial_evidence` | trial records behind class `trialled` | `trial_record` | `neura_self_issued`, or `host`, `independent_verifier` | N type |
| 31 | IC-11 `meter_evidence` | meter records behind class `metered` | `metered_data_record` | `meter_operator`, `host` (never self-issued) | N type |
| 32 | IC-11 `baseline_method` | method fixed in advance | `baseline_method` | as row 28 | N |
| 33 | IC-11 SLA `evidence` | the customer SLA document | `service_level_agreement` | `customer`, `host` | N type |
| 34 | IC-11 `calibration_evidence`, IC-2 `ModelledRun.evidence` | BMS history, design data, calibration data | `operational_data_record`; offers: `connection_offer` | `host`, `customer`, `oem`, `dno` | N type, E |
| 35 | IC-2 `registered_evidence` | a recorded publisher response | `regulatory_publication` | publisher's role | E |
| 36 | IC-1 `source` | the signed offer or agreement | `connection_offer`, `connection_agreement` | `dno`, `idno` | E |
| 37 | WP-BO-06 | an assumption still cannot be serialised as an evidence object; Neura is an issuer only as ADR-D10 decided | none: contract properties (the assumption guard; the restricted issuer of rows 7, 28 and 30) | | E, kept |

Notes:
- **a.** Neura's toolkit writes the commissioning record. It becomes evidence only when a third party issues
  or signs it. Neura cannot self-issue it: ADR-D10 limits the Neura role to pre-registration and
  decision records.
- **b.** Neura is a party to the MW agreement. Neura's own signature is not an evidence object, for the same
  reason. The criterion is met by one object per counterparty.

Gaps, each with its owner. None blocks this package:

| Gap | What is missing | Owner | Why not closed here |
|---|---|---|---|
| G3 | The licence term ("derived outputs may appear in lender packs") is not a field. IC-4 records that the licence exists and who issued it | WP-DATA-09 | The consumer reads the document; a term register is its product |
| G4 | The G99/G100 checklist items beyond the two approvals (witness test, PGMD) are not yet listed | WP-CONN-08 (P4) | The item list does not exist yet |
| G5 | A sign-off cannot say which control mode it covers. One `control_authority_signoff` at site scope would open every mode. It is kept apart from `critical_facilities_signoff` so a resilience-floor sign-off (WP-SITE-04) can never open closed loop | WP-THERM-08 (P6) | Needs either one type per mode or a subject field. P6; decide with the mode list |
| G6 | No type for DFS registration, ancillary response-unit registration, or a PPA or supply contract. Which type each stream requires is WP-UW-01's map | WP-UW-01 (gated D-3) | The GB document names were not verified; guessed members rot a contract. Types-only minors are cheap after this release |
| G7 | IC-4 has no organisation-wide scope. A licence decision, a legal opinion, a Cyber Essentials certificate, a data licence and the MW agreement are not about one site or one stream. Until it exists, such documents are registered per site | WP-BO-02 (the registry), with the next IC-4 minor | A new scope variant changes what `scope_covers` and `assess` answer: a design of its own. It would touch IC-4 only |
| G8 | No issuer role for an MGA or a floor provider. ADR-D1 sells the wrapper through an insurer, MGA or floor partner; IC-15's performance wrapper admits an `insurer` only and lists the other two as unavailable | WP-GRC-13 (with XG-20) | A third-party issuer role re-versions IC-4, IC-2 and IC-11 (README cost table). Which carrier signs is a person's choice under XG-20 |
| G9 | No type for a BESS funding commitment (a funder's letter or facility naming the asset). WP-OPS-01's dossier keeps `bess_funder` unknown for every site and lists this gap | WP-BO-02, with the next IC-4 minor (types only) | Which document a funder issues is not yet known; a guessed member rots the contract. A types-only minor is cheap |
| G10 | No type for an environmental permit or an MCPD / Specified Generator registration (a genset's run-hour and emissions limits). WP-SITE-05's onsite_generation_model takes the limits as a declaration, and each permit ask names this gap | WP-BO-02, with the next IC-4 minor (types only) | Which documents carry the limits (permit, registration, planning condition) needs a person to confirm; a guessed member rots the contract. Run-hour logs already close with `operational_data_record` |

G2 (closed by WP-GRC-09): the five missing template types are IC-4 1.2.0 members, a types-only
minor that re-versions IC-4 alone. "With its required fields" is met outside IC-4: the IC-15
`ContractTemplateSet` holds each template's required fields, and `check_executed_terms` checks an
executed contract's values against them, so no per-type field enters IC-4.
