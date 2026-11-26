> **DRAFT — NEEDS LEGAL REVIEW. NOT LEGAL ADVICE.**

# Assessment Statement of Work (heads of terms)

Template `assessment_sow`, version 0.2.0, heads of terms. An engineering agent wrote it on 2026-11-26 for WP-GRC-09, from spec D2 and ADR-D1. Counsel has not reviewed it and no counterparty has seen it.

How to read it: each double-braced field is a required field, and its ask is in the manifest `contract_template_set.json`. Each double-bracketed reference names another contract by its version range in the same manifest. No party is named.

## Parties [clause:parties]

- Provider: {{field:provider_legal_name}}.
- Customer: {{field:customer_legal_name}}.
- Effective date: {{field:effective_date}}.

## Scope [clause:scope]

- Site: {{field:site_reference}}.
- The assessment covers: {{field:assessment_scope}}.

## Inputs and data [clause:inputs_and_data]

- The Customer provides: {{field:data_items_requested}}.
- Connection terms are recorded under [[ref:ic1_connection_agreement]]. Third-party documents the Customer supplies are registered as evidence under [[ref:ic4_evidence_object]].
- Personal data, if any, is processed under the Customer's data processing agreement.

## Deliverables [clause:deliverables]

- Deliverables: {{field:deliverables}}, by {{field:delivery_date}}.
- Where the scope includes curtailment analysis, the scenarios are delivered under [[ref:ic2_curtailment_scenario_set]].
- Where the scope includes an operating proposal, its bounds are delivered as a dispatch envelope [[ref:ic3_dispatch_envelope]].

## Fees [clause:fees]

- Assessment fee: {{field:assessment_fee_agreed}} pence. The list price on record is £20,000 (list price on record, not agreed with any customer).
- Payment is due within {{field:payment_terms}} days of invoice.

## Reliance and liability [clause:reliance_and_liability]

- The report is for the Customer's own use. A lender may rely on it only through the lender reliance letter template.
- Liability is capped at {{field:liability_cap}} pence. The figure is to be agreed, and counsel sets the exclusions.
- Results are modelled from the inputs supplied. The report states which inputs were assumed and which were backed by evidence.

## Financing variants [clause:variant_schedule]

- The party commissioning the assessment is {{field:commissioning_party}}.
- Direct, host funded: the Host commissions the assessment and pays for it.
- IPP, third-party owner: the prospective owner commissions the assessment, and the Host grants data access.
- Power-provision lessor: the lessor commissions the assessment, and the Host grants data access.

## Governing law [clause:governing_law]

- Governing law and jurisdiction: {{field:governing_law}}.

## Execution [clause:execution]

- The Customer's signed copy is registered as evidence under [[ref:ic4_evidence_object]]. Its issuer is the Customer or the Host.
