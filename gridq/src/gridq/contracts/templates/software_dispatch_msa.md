> **DRAFT — NEEDS LEGAL REVIEW. NOT LEGAL ADVICE.**

# Software and Dispatch Master Services Agreement (template)

Template `software_dispatch_msa`, version 0.2.0, full text. An engineering agent wrote it on 2026-11-26 for WP-GRC-09, from spec D2 and §8, ADR-D1 and ADR-D2. Counsel has not reviewed it and no counterparty has seen it. It is a starting point for counsel, not an agreement to send.

How to read it: each double-braced field is a required field, and its ask is in the manifest `contract_template_set.json`. Each double-bracketed reference names another contract by its version range in the same manifest. No party is named: the parties are roles. Any figure that is not on record is left to be agreed.

## 1. Parties and definitions [clause:parties]

1.1 This agreement is between {{field:provider_legal_name}} (the **Provider**) and {{field:customer_legal_name}} (the **Customer**). It takes effect on {{field:effective_date}}.

1.2 **Site** means {{field:site_reference}}.

1.3 **Host** means the person that controls the Site's premises and IT load. Where the Customer is not the Host, the Customer obtains the Host's agreement to the terms that bind the Host (clauses 4, 5 and 7).

1.4 **Dispatch envelope** means the operating bounds the Provider issues for the Site under [[ref:ic3_dispatch_envelope]].

1.5 **IT outage** means a loss or degradation of the IT service the Host provides at the Site, below the Host's own IT service levels as the Host has disclosed them to the Provider in writing.

1.6 **Safety hierarchy** means the order set out in clause 4.

## 2. Services [clause:services]

2.1 The Provider supplies these services: {{field:services_in_scope}}. They cover the software platform (forecasting, optimisation, telemetry and reporting) and, where ordered, dispatch of flexible assets of {{field:contracted_capacity_kw}} kW.

2.2 Dispatch means that the Provider issues setpoints and operating bounds, and certified OEM controllers at the Site execute them (clause 3). The Provider does not execute market trades.

2.3 Integration of the Site's assets and data, and the Operate service, are charged under clause 10.

## 3. Responsibility boundary [clause:responsibility_boundary]

3.1 The Provider optimises the Site's assets and owns the dispatch envelope [[ref:ic3_dispatch_envelope]], as ADR-D2 option (d) sets out.

3.2 The route-to-market partner, {{field:route_to_market_partner}}, executes market actions under its own agreement (the flexibility services template). The Provider offers it availability only within the limits of clause 4.

3.3 The OEM owns certified control and protection through {{field:oem_controller}}. Nothing in this agreement lets the Provider bypass or override a certified protection function.

3.4 The site edge gateway holds the safety-critical constraints on its own. The Provider's cloud optimises within them, and no safety function is lost if the cloud link fails.

3.5 The Host keeps operational control of its premises, its IT load and its critical facilities.

## 4. Safety hierarchy [clause:safety_hierarchy]

4.1 Every instruction the Provider issues, and every optimisation it runs, follows this order, highest first. A lower tier is never traded against a higher one:

1. Life safety.
2. IT service levels and thermal limits.
3. Connection compliance (import limit, ramp), as recorded under [[ref:ic1_connection_agreement]].
4. Resilience reserve.
5. Contracted market delivery.
6. Merchant revenue.

4.2 Where a market commitment conflicts with a higher tier, the higher tier prevails. Between the Provider and the Customer, the resulting shortfall is a permitted non-delivery. Any penalty between the route-to-market partner and the market is governed by the partner's own terms. Counsel note: confirm the permitted non-delivery against the partner agreement.

4.3 The Host may suspend the Provider's write access to any plant at any time, without notice and without liability (manual override). The Provider's systems give effect to a suspension within the time the control safety case sets.

## 5. Control authority staging [clause:control_authority_staging]

5.1 The Provider's authority over Site plant is staged (ADR-D2). The stage at signature is {{field:initial_control_stage}}.

- **L0, read-only.** The Provider reads data and writes nothing.
- **L1, shadow.** For at least four weeks, the Provider computes instructions and records them without sending them.
- **L2, battery closed loop.** The Provider sends setpoints to the battery's certified controller.
- **L3, chiller narrow band.** At one site, the Provider adjusts chiller setpoints within a narrow band agreed with the Host.
- **L4, workload advisory.** The Provider advises on compute workload turn-down, in advisory and shadow modes only. It never writes to workload.

5.2 No stage with a write path (L2 or L3) starts until all three of these are in place for the Site:

- the Host's critical-facilities lead has signed off the write path (XG-11), and the sign-off is registered as evidence under [[ref:ic4_evidence_object]];
- the control safety case for the Site is released (WP-EDGE-08);
- the Provider holds a load-control licence or has confirmed an exemption (XG-17).

5.3 The Host's critical-facilities contact for sign-off and for any suspension is {{field:host_critical_facilities_contact}}.

5.4 Both parties record each move to a higher stage in writing. Either party may move the Site back to a lower stage at any time.

## 6. Service levels [clause:service_levels]

6.1 The Provider targets platform availability of {{field:platform_availability_target}} basis points of each calendar month, measured as the parties agree.

6.2 The Provider responds to a severity-one incident within {{field:incident_response_time}} minutes, at any hour.

6.3 Service credits, if any, are as follows: {{field:service_credits}}. Service credits are the Customer's only financial remedy for a missed service level, except where clause 7 applies.

6.4 A service level is not missed to the extent that the miss follows from any of these:

- the Provider applying the safety hierarchy;
- a Host suspension under clause 4.3;
- a failure of systems the Provider does not control.

## 7. IT-outage carve-out [clause:it_outage_carve_out]

7.1 The Provider is not liable for an IT outage unless the outage was caused by an instruction the Provider issued in breach of the safety hierarchy, or outside the operating bounds in force at the time.

7.2 In particular, the Provider is not liable for an IT outage caused by any of these:

- a failure of the grid or of the Site's supply;
- the Host's own plant, IT systems or staff;
- a certified OEM controller or protection function acting as designed;
- an instruction of the Customer or the Host;
- the Host's suspension of the Provider's write access.

7.3 Where clause 7.1 applies, the Provider's liability for the IT outage is treated as follows: {{field:it_outage_liability_treatment}}. Where a sublimit is chosen, it is {{field:it_outage_sublimit}} pence.

7.4 The Provider gives no warranty of the Host's service levels to the Host's own customers. The Customer indemnifies the Provider against their claims, except to the extent that clause 7.1 applies. Counsel note: confirm the indemnity is enforceable and insurable.

## 8. Step-in [clause:step_in]

8.1 The Customer may step in to operate the Site's dispatch itself or through a replacement operator if any of these occurs: {{field:step_in_triggers}}. So may a financier the Customer nominates, under a direct agreement.

8.2 Step-in starts on {{field:step_in_notice_period}} hours' written notice. In an emergency affecting any of the first three tiers of the safety hierarchy, the Host's manual override under clause 4.3 applies at once instead.

8.3 Step-in lasts no longer than {{field:step_in_maximum_period}} days unless extended in writing. During step-in:

- the Provider gives the step-in operator read access to the data and operating bounds it needs;
- the Provider is relieved of its service levels for the assets stepped into.

## 9. Liability cap [clause:liability_cap]

9.1 In any contract year, each party's total liability under or in connection with this agreement is capped at {{field:liability_cap}} pence. The cap is set on this basis: {{field:liability_cap_basis}}.

9.2 The cap does not apply to liability that the law does not allow to be limited (counsel to list), or to the heads that counsel and the parties agree to leave uncapped.

9.3 Neither party is liable for loss of profit, revenue or business, or for indirect or consequential loss, except as counsel and the parties agree.

## 10. Fees and payment [clause:fees_and_payment]

10.1 Integration: {{field:integration_fee_agreed}} pence. The list price on record is £5,000 (list price on record, not agreed with any customer). What integration covers is to be agreed.

10.2 Operate: {{field:operate_fee_agreed}} pence per megawatt per year of contracted capacity. The list price on record is £10,000 per MW per year (list price on record, not agreed with any customer).

10.3 Invoices are payable within {{field:payment_terms}} days.

## 11. Data, records and verification [clause:data_and_records]

11.1 Personal data is processed under the Customer's executed data processing agreement, registered as evidence under [[ref:ic4_evidence_object]].

11.2 The Provider keeps dispatch, telemetry and reconciliation records for the Site and makes them available to the independent verifier through [[ref:ic8_verifier_interface]] (binding unavailable until WP-LEDGER-07).

11.3 Each party owns the data it brings. The Provider owns its software, models and methods. The Customer receives a licence to use the outputs for its own business.

## 12. Insurance [clause:insurance]

12.1 The Provider holds professional indemnity and public liability insurance of at least {{field:provider_insurance_cover}} pence for each claim, as its broker confirms.

## 13. Term and termination [clause:termination]

13.1 The minimum term is {{field:minimum_term}} months from the effective date.

13.2 After the minimum term, either party may end the agreement on {{field:termination_notice_period}} days' written notice.

13.3 Either party may end the agreement at once if the other is insolvent, or commits a material breach that it does not remedy within the agreed period. Termination for convenience during the minimum term: {{field:termination_for_convenience}}.

13.4 On termination:

- the Site returns to stage L0 at once;
- the Provider hands over the operating bounds and records the Customer needs;
- data is returned or deleted under clause 11.

## 14. Financing variants [clause:variant_schedule]

14.1 This agreement is used under one of three ADR-D1 financing variants, stated in the executed terms. Under the chosen variant, the asset owner is {{field:asset_owner}}.

- **Direct, host funded.** The Customer is the Host and owns the assets. Nothing changes.
- **IPP, third-party owner.** A third party owns the battery and contracts with the Provider as the Customer. The Host signs the site access and installation agreement. Step-in under clause 8 extends to the owner's financier, under a direct agreement.
- **Power-provision lessor.** A lessor holds the leased import right or assets, contracts with the Provider as the Customer, and leases to the Host. The lessor's financier has the step-in rights of clause 8.

## 15. Governing law [clause:governing_law]

15.1 Governing law and jurisdiction: {{field:governing_law}}.

## 16. Execution [clause:execution]

16.1 Authorised signatories sign for the Provider and for the Customer. The Customer's signed copy is registered as evidence under [[ref:ic4_evidence_object]]. Its issuer is the Customer or the Host, never the Provider.
