> **DRAFT — NEEDS LEGAL REVIEW. NOT LEGAL ADVICE.**

# Site Access and Installation Agreement (heads of terms)

Template `site_access_installation`, version 0.1.0, heads of terms. An engineering agent wrote it on 2026-11-26 for WP-GRC-09. Counsel has not reviewed it and no counterparty has seen it.

How to read it: each double-braced field is a required field, and its ask is in the manifest `contract_template_set.json`. Each double-bracketed reference names another contract by its version range in the same manifest. No party is named.

## Parties [clause:parties]

- Provider: {{field:provider_legal_name}}.
- Host: {{field:host_legal_name}}.
- Effective date: {{field:effective_date}}.

## Site and location [clause:site_and_location]

- Site: {{field:site_reference}}.
- Equipment location: {{field:installation_location}}.

## Access [clause:access]

- Access arrangements (hours, notice, escort and induction): {{field:access_arrangements}}.
- The Host may refuse or end access at once on safety grounds.

## Installation works [clause:installation_works]

- Works: {{field:works_description}}, completed by {{field:works_completion_date}}. Competent installers carry them out under the Host's permit-to-work.
- Before energisation, the connection approvals the works need are registered as evidence under [[ref:ic4_evidence_object]].

## Connection compliance [clause:connection_compliance]

- The works, and the equipment's operation, stay within the import limit and the other terms recorded under [[ref:ic1_connection_agreement]].

## Critical facilities [clause:critical_facilities]

- No write path to site plant is commissioned until the Host's critical-facilities lead has signed it off (XG-11). The sign-off is registered as evidence under [[ref:ic4_evidence_object]].
- The Host's critical-facilities contact: {{field:host_critical_facilities_contact}}.

## Equipment ownership [clause:equipment_ownership]

- The installed equipment is owned by {{field:asset_owner}}. It is not a fixture of the premises (counsel to confirm).

## Insurance [clause:insurance]

- Insurance each party holds for the works and the equipment: {{field:insurance_requirements}}.

## Fees [clause:fees]

- Site licence fee, if any: {{field:site_licence_fee}} pence per year. The figure is to be agreed.

## Removal and make-good [clause:removal_and_make_good]

- On termination, the equipment owner removes the equipment and makes good the premises: {{field:make_good_obligations}}.

## Term and termination [clause:term_and_termination]

- Term: {{field:term}} months. The agreement ends early if the MSA for the Site ends.

## Financing variants [clause:variant_schedule]

- Direct, host funded: the Host owns the equipment.
- IPP, third-party owner: the owner is a party, with rights of access and quiet enjoyment. Its financier may need a direct agreement.
- Power-provision lessor: the lessor is a party, with the same rights.

## Governing law [clause:governing_law]

- Governing law and jurisdiction: {{field:governing_law}}.

## Execution [clause:execution]

- The Host's signed copy is registered as evidence under [[ref:ic4_evidence_object]]. Its issuer is the Host.
