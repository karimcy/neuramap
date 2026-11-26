> **DRAFT — NEEDS LEGAL REVIEW. NOT LEGAL ADVICE.**

# Lender Reliance Letter (heads of terms)

Template `lender_reliance_letter`, version 0.2.0, heads of terms. An engineering agent wrote it on 2026-11-26 for WP-GRC-09, from spec D2 and risk R11. Counsel has not reviewed it and no counterparty has seen it.

How to read it: each double-braced field is a required field, and its ask is in the manifest `contract_template_set.json`. Each double-bracketed reference names another contract by its version range in the same manifest. No party is named.

## Addressee [clause:addressee]

- From {{field:provider_legal_name}} to {{field:lender_legal_name}} (the Lender).
- In connection with {{field:financing_reference}}.
- Dated {{field:effective_date}}.

## Relied materials [clause:relied_materials]

- The report: {{field:relied_report}}, identified by its title, date and content hash.
- The connection terms it used: {{field:relied_connection_agreement}}, an instance of [[ref:ic1_connection_agreement]].
- The curtailment scenarios it used: {{field:relied_curtailment_scenarios}}, an instance of [[ref:ic2_curtailment_scenario_set]].
- The operating bounds it used: {{field:relied_dispatch_envelope}}, a dispatch envelope [[ref:ic3_dispatch_envelope]].

## Purpose [clause:purpose]

- The Lender may rely on the relied materials only for this purpose: {{field:permitted_purpose}}.

## No wider duty [clause:no_wider_duty]

- The Provider owes the Lender no duty beyond this letter, and no other person may rely on it.
- The relied materials are modelled. The report states which inputs were backed by evidence and which were assumed.

## Liability cap [clause:liability_cap]

- The Provider's total liability to the Lender under this letter is capped at {{field:reliance_liability_cap}} pence. The figure is to be agreed.

## Lender access [clause:lender_access]

- The Lender gets read-only access to the relied materials and to later performance records, granted through identity claims [[ref:ic13_identity_claims]].
- Performance that the independent verifier has checked is shown through [[ref:ic8_verifier_interface]] (binding unavailable until WP-LEDGER-07).

## Duration [clause:duration]

- Reliance ends on {{field:reliance_expiry_date}}.

## Assignment [clause:assignment]

- The Lender may pass reliance on to a successor lender or a security trustee only on the terms counsel sets.

## Financing variants [clause:variant_schedule]

- Direct, host funded: the Lender finances the Host.
- IPP, third-party owner: the Lender finances the owner.
- Power-provision lessor: the Lender finances the lessor.
- The relied materials are the same in each variant.

## Governing law [clause:governing_law]

- Governing law and jurisdiction: {{field:governing_law}}.

## Countersignature [clause:countersignature]

- The Lender countersigns to accept these terms. The Lender's countersigned copy is registered as evidence under [[ref:ic4_evidence_object]]. Its issuer is the Lender.
