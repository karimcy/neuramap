# WP-GRC-09 plan: contract template set (IC-4 1.2.0 and NEW-IC-15)

Branch `roadmap/wp-grc09-templates`, worktree `uk-grc09`. Olly is the contributor. Written
2026-11-26, as built.

## 1. The work package

WP-GRC-09 releases `contract_template_set`. It has seven templates: the Assessment SOW, the
Software and Dispatch MSA, the data-sharing agreement, the aggregator/flexibility services
agreement, the site access and installation agreement, the lender reliance letter, and the
performance wrapper. Insurer terms in the wrapper are unavailable until WP-GRC-13.

Acceptance:

1. Each template is registered as an evidence-object type with its required fields.
2. The MSA carries the safety hierarchy, the IT-outage carve-outs, step-in and the liability caps as
   named clauses.
3. The templates reference IC-3 and IC-8 artefacts by contract version, not by prose.

Binding rules from the brief:

- Every template is a draft that needs legal review, and is marked so. Legal review is a person-only
  step. No template is presented as reviewed.
- No party names, prices or terms that are not on record. A placeholder carries its ask.
- A new IC-4 minor keeps the 1.0.0 and 1.1.0 schemas byte-identical, and it follows the re-version
  rule WP-BO-06 set (`src/gridq/contracts/README.md`, the cost table).

## 2. Decision (Fable 5.2, under DEL-2026-11-25-1: the most innovative option within the rules)

1. **IC-4 1.2.0 is a types-only minor, so it re-versions IC-4 alone.** It appends five types:
   - `assessment_statement_of_work`
   - `master_services_agreement`
   - `site_access_installation_agreement`
   - `lender_reliance_letter`
   - `performance_wrapper_agreement`

   No issuer role is added. The v1.1.0 schema is archived and pinned by SHA-256. R7 limits an
   object labelled 1.1.0 to the 1.1.0 types. Inventory gap G2 closes.
2. **"With its required fields" lives in a new contract, NEW-IC-15 `ContractTemplateSet` 1.0.0.**
   It does not go into IC-4, where a per-type field would be a major version. IC-15 holds the IC-4
   types and roles as strings, checked against the live enums, and reaches IC-4 only through
   `EvidenceRef`. As a result, a later IC-4 types-only minor still re-versions IC-4 alone.

   Legal review is a closed union of two states:
   - `draft_needs_legal_review`, which all seven templates ship as;
   - `counsel_reviewed`, which binds the reviewed text's SHA-256 and an `EvidenceRef` to a
     third-party `legal_opinion`.

   `ExecutedContractTerms` records one executed contract, and `check_executed_terms` checks it.
3. **References.** A `ContractVersionRef` names a contract and the range `1.x`, and is either
   `frozen` (minimum version and git tag) or `not_yet_frozen`:
   - IC-3 waits on WP-SIZE-07;
   - IC-8 waits on WP-LEDGER-07, with gates D-2 and XG-22.

   No IC-3 or IC-8 field name is invented. A field bound to a `not_yet_frozen` contract can only be
   `Unknown`. Acceptance 3 is reported as "the reference mechanism is in place; the binding
   completes when IC-3 and IC-8 freeze".
4. **Texts.** Each template text lives at `templates/<id>.md`. It opens with a DRAFT header, has
   clause anchors and field and reference tokens, and is pinned by its hash. The MSA is written in
   full; the other six are heads of terms. The data-sharing agreement cites the Atlas WP-GRC-05
   heads of terms instead of copying them.
5. **Prices.** `list_price_on_record` admits only ADR-D1's four figures:
   - £20k per assessment;
   - £5k integration;
   - Operate at £10k per MW per year;
   - a 2.5% wrapper fee through an insurer (XG-20).

   Every other term is `to_be_agreed`, with its ask.
6. **No new issuer role.** The wrapper admits an `insurer` only. MGA and floor provider are
   unavailable as gap G8 (owner WP-GRC-13), because a new role would re-version IC-2 and IC-11.
7. **ADR-D1's three financing variants** are manifest data, and each template has a variant
   schedule clause.
8. **Tags.** Annotated tags `contracts/ic4-evidence-object/v1.2.0` and
   `contracts/ic15-contract-template-set/v1.0.0` go on the final commit once the suite is green.
9. **Out of scope:**
   - a rendering engine;
   - docx or PDF output;
   - WP-BO-02 integration;
   - IC-2 and IC-11 changes;
   - insurer terms;
   - party names.

## 3. Steps

1. IC-4 1.2.0: the types, R7, the archive pin, and the schema delta pinned.
2. IC-15: the module, the two schemas, the seven texts and the manifest.
3. Acceptance tests (`test_ic15_contract_templates.py`).
4. Synthetic fixtures (`_ic15_fixtures.py`, `fixtures/v1_2_0/`).
5. Inventory: G2 closed, G8 added. The embedders test is widened.
6. Mutation checks: 27 guards with controls (`test_mutations_v120.py`).
7. Documents: README, both changelogs, this plan, and a HANDOFF_LOG entry.

## 4. Person-only steps (never done by an agent)

- Legal review of all seven templates, the MSA and the wrapper first.
- Engaging counsel.
- Choosing and signing an insurer, MGA or floor provider, and taking the FCA PERG 6 position
  (XG-20).
- Agreeing prices, caps, service levels and termination with any customer.
- Signing any contract or reliance letter.
- The host's critical-facilities sign-off (XG-11).
- The load-control licence or exemption (XG-17).
- Registering NEW-IC-15 in the roadmap and ADRs. The coordinator does this in Atlas, because this
  worktree does not edit Atlas.
