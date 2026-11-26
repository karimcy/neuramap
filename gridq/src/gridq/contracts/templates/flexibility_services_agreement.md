> **DRAFT — NEEDS LEGAL REVIEW. NOT LEGAL ADVICE.**

# Aggregator / Flexibility Services Agreement (heads of terms)

Template `flexibility_services_agreement`, version 0.2.0, heads of terms. An engineering agent wrote it on 2026-11-26 for WP-GRC-09, from spec D2 and ADR-D2. Counsel has not reviewed it and no counterparty has seen it.

How to read it: each double-braced field is a required field, and its ask is in the manifest `contract_template_set.json`. Each double-bracketed reference names another contract by its version range in the same manifest. No party is named.

## Parties [clause:parties]

- Provider: {{field:provider_legal_name}}.
- Flexibility counterparty: {{field:counterparty_legal_name}}. This is either a route-to-market aggregator, or a DNO or IDNO that buys flexibility.
- Site: {{field:site_reference}}.
- Effective date: {{field:effective_date}}.

## Roles [clause:roles]

- The counterparty executes market actions and holds the market registrations.
- The Provider optimises and offers availability. It does not trade (ADR-D2).
- Services in scope: {{field:services_offered}}.

## Availability [clause:availability]

- The availability offered never exceeds the limits of the dispatch envelope [[ref:ic3_dispatch_envelope]].
- Thermal (cooling) flexibility is offered as availability under [[ref:ic11_thermal_flex]].

## Instructions and acknowledgements [clause:instructions]

- The counterparty sends instructions and the Provider acknowledges each one as accepted, partial or rejected, under [[ref:ic11_thermal_flex]], through {{field:instruction_channel}}.

## Safety override [clause:safety_override]

- The spec §8 safety hierarchy binds every instruction. Life safety, IT service levels and thermal limits, connection compliance and resilience reserve all rank above contracted delivery.
- An instruction that would breach a higher tier is rejected, and the rejection is not a breach of this agreement.

## Settlement and verification [clause:settlement_and_verification]

- Delivery is settled through the counterparty's own settlement.
- Disputes about delivered volumes are resolved on the records available to the independent verifier through [[ref:ic8_verifier_interface]] (binding unavailable until WP-LEDGER-07).

## Revenue share [clause:revenue_share]

- The Provider's share is {{field:revenue_share}} basis points of the counterparty's receipts for the Site. The figure is to be agreed.

## DNO counterparty [clause:dno_counterparty]

- Where the counterparty is a DNO or IDNO, its own standard flexibility agreement governs: {{field:counterparty_standard_terms}}. This template then supplies only what that agreement lacks.

## Termination [clause:termination]

- Either party may end the agreement on {{field:termination_notice_period}} days' written notice.
- Market commitments already made are honoured or transferred, as the counterparty's market rules require.

## Financing variants [clause:variant_schedule]

- Direct, host funded: the Host's assets are offered.
- IPP, third-party owner: the owner's assets are offered, and the owner's consent is recorded.
- Power-provision lessor: the lessor's consent is recorded.

## Governing law [clause:governing_law]

- Governing law and jurisdiction: {{field:governing_law}}.

## Execution [clause:execution]

- The counterparty's signed copy is registered as evidence under [[ref:ic4_evidence_object]], in one of two forms:
  - an aggregator agreement issued by the aggregator;
  - a DNO flexibility contract issued by the DNO or IDNO.
