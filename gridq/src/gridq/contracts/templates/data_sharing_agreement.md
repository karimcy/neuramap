> **DRAFT — NEEDS LEGAL REVIEW. NOT LEGAL ADVICE.**

# Data-Sharing Agreement (heads of terms)

Template `data_sharing_agreement`, version 0.1.0, heads of terms. An engineering agent wrote it on 2026-11-26 for WP-GRC-09. Counsel has not reviewed it and no counterparty has seen it.

How to read it: each double-braced field is a required field, and its ask is in the manifest `contract_template_set.json`. Each double-bracketed reference names another contract by its version range in the same manifest. No party is named.

## Parties [clause:parties]

- Provider: {{field:provider_legal_name}}.
- Counterparty: {{field:counterparty_legal_name}}.
- Effective date: {{field:effective_date}}.
- Each party is a separate controller for the data it holds.

## Source of the heads of terms [clause:heads_of_terms_source]

- The common core and the counterparty schedules are those of the WP-GRC-05 heads of terms, `atlas/roadmap/governance/data_protection/data_sharing_agreements.md`. This template cites them and does not copy them. That document is itself a draft that needs legal review.
- The schedule that applies: {{field:counterparty_schedule}}.

## Purpose and data items [clause:purpose_and_data]

- Purpose: {{field:purpose}}. The data is used for nothing else.
- Data items: {{field:data_items}}. Nothing beyond the list is shared.

## Lawful basis [clause:lawful_basis]

- Each party states its own lawful basis. The Provider's is {{field:lawful_basis}}, confirmed by counsel with a recorded assessment.

## Onward sharing [clause:onward_sharing]

- No onward sharing without the discloser's written consent. Lenders and the independent verifier see only aggregated or customer-authorised data, through [[ref:ic8_verifier_interface]] (binding unavailable until WP-LEDGER-07).

## Security and access [clause:security_and_access]

- Data is encrypted in transit and at rest.
- Access is by named role only, enforced through identity claims [[ref:ic13_identity_claims]], and every access is audited.
- A breach is notified to the other party without undue delay.

## Retention, review and termination [clause:review_and_termination]

- Data is retained under the Provider's retention schedule. Shared data is deleted when the purpose ends.
- The agreement is reviewed every {{field:review_interval}} months, and after a significant complaint, a breach or a change in the data.
- On termination, each party returns or deletes the data and confirms it in writing.

## Financing variants [clause:variant_schedule]

- The data flows do not change with the variant.
- Direct, host funded: the data is the Host's site data.
- IPP, third-party owner: the owner is a further controller for its asset data and signs its own data-sharing terms.
- Power-provision lessor: the same applies to the lessor.

## Governing law [clause:governing_law]

- Governing law and jurisdiction: {{field:governing_law}}.

## Execution [clause:execution]

- The counterparty's signed copy is registered as evidence under [[ref:ic4_evidence_object]]. Its issuer is the counterparty.
