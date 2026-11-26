> **DRAFT — NEEDS LEGAL REVIEW. NOT LEGAL ADVICE.**

# Performance Wrapper (heads of terms)

Template `performance_wrapper`, version 0.2.0, heads of terms. An engineering agent wrote it on 2026-11-26 for WP-GRC-09, from spec D2, ADR-D1 and the WP-GRC-13 scope. Counsel has not reviewed it and no counterparty has seen it. Insurer terms are unavailable until WP-GRC-13. This template holds only the structure that the insurer's terms will fill.

How to read it: each double-braced field is a required field, and its ask is in the manifest `contract_template_set.json`. Each double-bracketed reference names another contract by its version range in the same manifest. No party is named.

## Parties [clause:parties]

- Provider: {{field:provider_legal_name}}.
- Risk carrier: {{field:risk_carrier_legal_name}}.
- Insured party: {{field:insured_party}}.
- Effective date: {{field:effective_date}}.

## Risk carrier [clause:risk_carrier]

- The risk carrier is an insurer. An MGA or a floor provider cannot carry the risk yet, because no evidence issuer role exists for either (inventory gap G8, owner WP-GRC-13).
- A person chooses and signs the insurer (XG-20). No insurer is named here.

## Cover [clause:cover]

- The insurer covers any shortfall of delivered value against the underwritten value.
- The Provider carries only a warranty of its own obligations under its MSA.

## Underwritten value [clause:underwritten_value]

- Underwritten value: {{field:underwritten_value}} pence, taken from the dispatch envelope {{field:underwritten_envelope}} [[ref:ic3_dispatch_envelope]].

## Fee [clause:fee]

- Wrapper fee: {{field:wrapper_fee_agreed}} basis points of underwritten value, payable only through the insurer partner (XG-20).
- The list price on record is 2.5% of underwritten value (list price on record, not agreed with any customer).

## Pay-out basis [clause:payout_basis]

- Pay-outs are calculated from reconciliation records by method version through [[ref:ic8_verifier_interface]]: unavailable until WP-LEDGER-07 / WP-GRC-13.

## Insurer terms [clause:insurer_terms]

- Premium, deductible, exclusions, claims procedure and policy wording: unavailable until WP-GRC-13.

## Policy period [clause:policy_period]

- Period: {{field:policy_period}} months, to be agreed with the insurer.

## Regulatory perimeter [clause:regulatory_perimeter]

- Counsel decides whether the wrapper is a contract of insurance or another regulated activity (FCA PERG 6).
- The Provider's position is {{field:fca_perimeter_position}}. Before any offer, it is recorded as counsel's legal opinion under [[ref:ic4_evidence_object]].

## Financing variants [clause:variant_schedule]

- Direct, host funded: the Host is the insured party.
- IPP, third-party owner: the owner or its financier is the insured party.
- Power-provision lessor: the lessor or its financier is the insured party.

## Governing law [clause:governing_law]

- Governing law and jurisdiction: {{field:governing_law}}.

## Execution [clause:execution]

- The insurer's signed copy is registered as evidence under [[ref:ic4_evidence_object]]. Its issuer is the insurer.
