# Export and levy treatment: what GB actually charges on a battery's charging energy

**Author:** Olly · **Date:** 2026-08-16 · **Status:** desk research, investigation only, no code changed.

**Scope.** This document establishes which GB levies are payable on electricity imported to charge a
battery that later **exports**, versus one that **discharges behind the meter**, and what that does to
the export case for the seven modelled sites. It is written because the export case turns on levy
treatment and that had never been written down. It does **not** propose that levy treatment be changed
to make a site viable. Two of its three main conclusions make the export case look worse, not better,
and the third is conditional on evidence nobody in this project has yet obtained.

**Related:** `docs/00_project_boundary.md` (D46 study basis) · `docs/bess_constants_register.md`
(the levy rows cited throughout) · `docs/validation_requirements.md` (D45) ·
`docs/derived_assumptions_log.md`.

---

## 1. Summary of findings

1. **The arithmetic is confirmed and reproducible.** Break-even export price is
   `(energy_band + DUoS_band + levy_stack) / RTE + c_deg`. That formula reproduces all three
   break-even figures on the record exactly, to the last decimal place, from the site JSON inputs
   alone (section 2). The levy stack is 30 to 62 per cent of break-even depending on the site.

2. **The framing needs one correction, and it matters.** The "roughly 7.4 p/kWh volumetric levy stack"
   is the **grid-supplied** stack. It applies to Harwell, Ewelme, Bishops Cleeve (both) and Knowl
   Hill. It does **not** apply to Tanhouse or Colnbrook, which are 100 per cent private wire. Their
   "levy" line is a derived contractual residual (13.140 and 4.131 p/kWh), not the GB statutory
   stack, and no Ofgem or HMRC relief reaches it. The two sites the break-even figures were quoted
   from are precisely the two where GB levy treatment matters least. Section 8 handles them
   separately.

3. **The published GB relief is real, is narrower than the industry shorthand, and is not one thing
   but four separate legal regimes with four different tests** (section 4). Of the 7.698 p/kWh
   modelled at Harwell, **5.585 p/kWh has an identifiable published relief route** (RO, CfD, BSUoS)
   and **2.114 p/kWh does not** (CCL, FiT, Nuclear RAB, AAHEDC). Every relief route requires the
   storage to hold an **electricity generation licence** and requires the **supplier** to make a
   declaration and pass the benefit through.

4. **The single most important regulatory fact found is Ofgem's own, and it is directly on point.**
   Ofgem's P395 decision, footnote 5:

   > "The distinction between demand and Final-Demand must be noted. The former may relate to
   > electricity demand which is utilised for purposes under the licence e.g. electricity 'consumed'
   > by Storage facilities which is then re-exported. This electricity should be exempt from FCLs."

   In principle, therefore, **export-bound charging energy is the relieved case and behind-the-meter
   charging energy is not.** That inverts the intuition. It does not, on its own, change any number
   here, because the entitlement is conditional (section 5) and the model's current treatment is the
   correct conservative default until the conditions are evidenced.

5. **Behind-the-meter and export are asymmetric for a reason that has nothing to do with relief, and
   the model already gets it right.** Levies are billed on **boundary import**, and exports are
   explicitly **not netted off imports** (Ofgem RO guidance, quoted in section 6). A BTM discharge
   reduces boundary import and so reclaims the levy automatically; an export does not. That is why a
   flat levy costs only the round-trip losses on the BTM case and costs the **whole charged kWh** on
   the export case. The code implements exactly this and I found no defect in it (section 6).

6. **Levy relief does not rescue the export case at these sites, and that is a legitimate outcome.**
   On the only relief case with published support (case B: RO, CfD and BSUoS), **no** grid site clears
   the campaign's derived flat export tariff of GBP 109.61/MWh; the best, Bishops Cleeve MRF at
   114.69, is 1.05x above it. Removing the entire stack (case C, which nothing in section 4 supports,
   since CCL alone has no published relief) would put three grid sites 11 to 14 per cent below that
   figure, but 109.61 is a one-day summer mean and not a forward annual curve, so a margin of that
   size settles nothing in either direction. What relief changes is whether a **shaped** export
   route is even arithmetically possible: under today's treatment no grid site's break-even sits
   below the peak half-hour of the sourced price day; under the established-relief case, four of five
   do.

7. **The residual question is not answerable from published rules.** Whether any of this reaches the
   customer's bill depends on the **supplier contract** and, for the network residuals, on a **DNO /
   DCUSA certification** the sites appear unable to satisfy. Section 9 states the exact questions and
   who to put them to. This is the honest limit of desk research and I am not going to disguise it.

---

## 2. The arithmetic, verified

A charge-then-export round trip breaks even when the export price covers the delivered cost of the
charged kWh plus the cycle wear on the kWh that leaves:

```
break_even_export_price = (energy_band + DUoS_band + levy_stack) / RTE  +  c_deg
```

with `RTE = 0.88` (`round_trip_efficiency.ac_round_trip`, gb_v1_9) and
`c_deg = GBP 21.3/MWh` (`battery_opex.degradation_marginal_cost_gbp_per_mwh`, gb_v1_9, charged in the
objective per `milp_solver.py:1196`).

Applied to the cheapest charging band in each site's own JSON, this reproduces every break-even figure
on the record:

| Site | Charge band | DUoS | Levy stack | Formula gives | On the record |
|---|---:|---:|---:|---:|---|
| Tanhouse | 6.351 p night | 0.000 | 13.1396 p | **242.78** | 242.78 (task brief) |
| Colnbrook | 6.272 p night | 0.000 | 4.1314 p | **139.52** | 0.13952 (HANDOFF_LOG 752) |
| Knowl Hill | 15.410 p flat | 0.028 | 7.3880 p | **280.69** | 0.28069 (HANDOFF_LOG 752) |

Three independent exact matches. The formula is therefore safe to apply to the sites whose break-even
was not separately published. Those figures are **desk arithmetic on record inputs**, not run outputs,
and are labelled as such wherever they appear below. They should be re-derived by a run before they
are quoted to anyone outside the project.

| Site | Levy stack (p/kWh) | Break-even (GBP/MWh) | Levy share of break-even |
|---|---:|---:|---:|
| Bishops Cleeve MRF | 7.3880 | 178.15 | 47.1% |
| Ewelme (invoice anchored) | 7.3880 | 181.03 | 46.4% |
| Harwell | 7.6980 | 184.55 | 47.4% |
| Bishops Cleeve Ash Plant | 7.3880 | 200.53 | 41.9% |
| Knowl Hill | 7.3880 | 280.69 | 29.9% |
| Colnbrook (private wire) | 4.1314 | 139.52 | 33.6% |
| Tanhouse (private wire) | 13.1396 | 242.78 | 61.5% |

The levy line is the largest single term in the grid-site break-evens: at Harwell it is 7.698 p/kWh
against a 6.640 p/kWh night commodity rate and a 0.028 p/kWh green DUoS rate.

**Per-levy contribution.** Each levy line contributes `levy / RTE` to break-even:

| Line | Value on the site record (GBP/MWh charged) | Contribution to break-even (GBP/MWh) |
|---|---:|---:|
| RO | 32.730 | 37.19 |
| BSUoS | 13.115 | 14.90 |
| CfD interim levy rate | 10.000 | 11.36 |
| CCL | 8.010 | 9.10 |
| FiT (Harwell; 4.90 elsewhere, unconfirmed) | 8.000 | 9.09 |
| Nuclear RAB | 4.683 | 5.32 |
| AAHEDC | 0.442 | 0.50 |
| **Total (Harwell)** | **76.980** | **87.48** |

---

## 3. Which stack applies to which site

| Site | Supply | Levy lines carried | GB statutory levy stack? |
|---|---|---|---|
| Harwell | grid (assumed tariff) | CCL, RO, CfD_ILR, BSUoS, AAHEDC, NuclearRAB, FiT | yes |
| Ewelme (x2 variants) | grid | same, FiT unconfirmed | yes |
| Bishops Cleeve Ash Plant | grid | same, FiT unconfirmed | yes |
| Bishops Cleeve MRF | grid | same, FiT unconfirmed | yes |
| Knowl Hill (x2 variants) | grid | same, FiT unconfirmed | yes |
| **Colnbrook** | **100% EfW private wire** | one line: `private_wire_non_commodity_residual` 4.1314 p | **no** |
| **Tanhouse** | **100% EfW private wire + solar** | one line: `private_wire_non_commodity_residual` 13.1396 p | **no** |

The two private-wire site records say so explicitly and correctly: "PRIVATE-WIRE SITE, THE GB GRID
NETWORK STACK IS DELIBERATELY NOT APPLIED", and the residual is described in the record itself as "a
DERIVED RESIDUAL, not a sourced rate ... its composition is unknown". Nothing in sections 4 to 7
applies to them. See section 8.

---

## 4. The per-levy table: what is actually payable on export-bound charging

**Read the status column as the answer to one question: is there a published GB rule under which this
line is not payable on electricity imported by a licensed storage operator and subsequently
exported?**

| Line | p/kWh (Harwell) | Legal regime | Charging basis | Relief on export-bound charging? | Authority |
|---|---:|---|---|---|---|
| **RO** | 3.2730 | Electricity Act 1989 s.32; RO Order 2015 (SI 2015/1947) | ROCs per MWh "supplied to customers in Great Britain" | **Yes, established in Ofgem's own supplier guidance.** Supply volumes "should therefore not include ... electricity imported by a licensed generator in relation to their operations as a generator" | Ofgem, *Renewables Obligation Guidance for Suppliers*, v10, 2 April 2026, Appendix 4, A.4.4 |
| **CfD supplier obligation** | 1.0000 | EMR Regulations 2014; collected by EMRS on BM Unit Demand volumes | "Final Consumption Levy" on Final-Demand | **Yes.** FCLs "should not be recovered via demand that is used by licenced generation (including storage) for the purposes set out in their licence" | Ofgem, *BSC P395 decision*, 6 Oct 2022, p.1 and fn.5; implemented 2 Nov 2023 |
| **Capacity Market supplier charge** | **not in the modelled stack** | Electricity Capacity (Supplier Payment etc.) Regulations 2014 SI 2014/3354 | peak-window (16:00 to 19:00 working days Nov to Feb) relevant demand only | **Yes**, same FCL regime as CfD. Note it is not a flat volumetric levy at all: charging outside the peak window attracts none of it | SI 2014/3354 reg.2(1); Ofgem P395 |
| **BSUoS** | 1.3115 | CUSC; CMP308 (from 1 Apr 2023), CMP361 | "Final Demand" only | **Yes, on a purpose test.** "Final Demand is currently defined as electricity consumed other than for the purposes of generation or export onto the electricity network" | NESO, *Draft BSUoS Tariffs for 2026/27*, v1.0 Oct 2025, p.9; Ofgem CMP308 decision; BSC P383/P419 |
| **CCL** | 0.8010 | Finance Act 2000 Sch 6 | taxable supply of electricity to a business consumer | **No published relief found.** HMRC Notice CCL1/3, the authoritative list of CCL reliefs, contains no storage provision. The generation relief is for **input fuel** used in a generating station, not for electricity supplied to a battery | HMRC, *Excise Notice CCL1/3*, sections 3 and 6 (searched in full; no occurrence of storage relief) |
| **FiT levelisation** | 0.8000 | Energy Act 2008; FIT Order; Ofgem levelisation | "total electricity supplied in Great Britain" | **Ambiguous in Ofgem's own document.** FIT guidance 8.32 says the total "should be determined using the same methodology as the one used under the RO" (which excludes it), but 8.33 as printed lists the same three RO exclusion bullets under the word "**include**". See the note below | Ofgem, *FIT Guidance for Licensed Electricity Suppliers*, v17.1, Sept 2024, 8.32 to 8.34 |
| **Nuclear RAB revenue levy** | 0.4683 | Nuclear Energy (Financing) Act 2022; Nuclear RAB Model (Revenue Collection) Regulations 2023 SI 2023/254 | "amount of electricity supplied by that supplier", less EII excluded electricity | **Not established.** Administered by LCCC/EMRS on a "supplied" basis, so the same legal logic plausibly applies, but I found no storage exclusion in the regulations or in any EMRS guidance, and the levy post-dates P395 | SI 2023/254 Part 3; declared gap |
| **AAHEDC** | 0.0442 | NESO ESO Licence Condition F8 | recovered from all authorised suppliers on a GB demand base (273.7 TWh forecast for 2026/27, which is not the BSUoS Final Demand base of ~261 TWh) | **No.** The register's source states AAHEDC is not avoidable even by a licensed non-final-demand site | NESO 2026/27 AAHEDC forecast tariff; `bess_constants_register.md` line 484 |
| **TCR residuals (TNUoS and DUoS)** | not volumetric | Ofgem Targeted Charging Review 2019; DCUSA; CUSC section 11 | **fixed GBP/site/day by band** | **Not applicable to this question.** Post-TCR the residual is not charged on energy at all, so it is neither payable on nor avoidable by charging energy. It is a fixed line the battery cannot move. The separate "No Residual" tariff needs Non-Final Demand Site certification (section 5) | NESO Jan 2026 Final TNUoS Tariffs Table 10; DCUSA Appendix 3 |

**The FiT drafting problem, stated plainly.** Ofgem's FIT supplier guidance 8.33 reads "For the
avoidance of doubt, supply volumes should therefore **include**: losses in transmission ...,
electricity imported by a licensed generator in relation to their operations as a generator, and any
adjustment for exports (ie exports should not be netted off against imports)." The identical three
bullets appear in the RO guidance under "should therefore **not** include". The third bullet is
internally contradictory as printed under "include": "any adjustment for exports (ie exports should
not be netted off against imports)" is a statement of what to exclude. On the face of it this is a
transcription error in the FIT document, and 8.32's cross-reference to the RO methodology points the
other way. **I am not resolving it.** For modelling purposes FiT stays charged, which is the
conservative direction. It is a question for Ofgem, not for us.

---

## 5. The four tests, and why they are not one test

The industry shorthand "storage is exempt from final consumption levies" collapses four separate
regimes with four different eligibility tests. Getting one does not get the others.

**Test 1: the Electricity Act 1989 definition of "supply" (RO, CfD, CM, FiT, Nuclear RAB).**
The obligation on a licensed supplier is measured on electricity **supplied to customers**. Ofgem and
BEIS clarified jointly that imports to a licence-operated generator fall outside that definition, and
P395 records this as "compatible with the definition of 'supply' in the Electricity Act 1989". The
operative words in both the P395 decision and the RO guidance are the same: **licensed** generator,
import **relating to their operations as a generator**.

**Test 2: the settlement machinery (CfD and CM only).** Legal entitlement is worthless without a
mechanism to remove the volumes from the data EMRS bills on. That mechanism is BSC P383 (as amended by
P419) plus P375, extended by **P395** to "complicated" SVA sites, that is, "SVA sites with Final-Demand
(colloquially termed 'complex sites' or 'co-located sites')". This is the piece that matters for a
behind-the-meter battery on a working commercial site: P383/P375 alone covered only "simple sites ...
which do not have Final-Demand present". P395 is what opens the door to a site with real load. It uses
"the processes developed by P383 (as updated by P419) and P375 for asset registration and 'Behind the
Boundary Point' metering", and requires "Suppliers and CVA Registrants ... to declare the details of
the sites they wish to have the volumes adjusted using three distinct declarations."

Two consequences follow, and both are load-bearing:

- **The relief is claimed by the supplier, not by the customer.** Elexon's material states that only
  Import Suppliers benefit from EMR AMSID declarations (**search-index summary of Elexon text, not the
  Elexon page itself**, which was not retrievable; see section 9c). Whether the benefit reaches the
  site's bill is a **contract** question, not a regulatory one.
- **The machinery is per metered facility, not per kWh of purpose.** It excludes the declared storage
  asset's metered import. It has no concept of "this 40 per cent of the battery's import was for
  export and this 60 per cent served site load". A battery that does both is not obviously
  declarable, and I found no published rule that resolves it. This is the sharpest open question in
  the whole document.

**Test 3: BSUoS (CUSC).** The definitional test is about **purpose**: Final Demand is "electricity
consumed other than for the purposes of generation or export onto the electricity network". The
machinery for an embedded (SVA) site is the SVA Non-Final Demand Facility declaration under P383/P419
(P419 implemented 23 February 2023). Same split as test 2: a purpose-based rule, facility-based
machinery.

**Test 4: the network residual (DCUSA / CUSC section 11), which these sites fail.** The
"Non-Final Demand Site" certificate that removes the TCR fixed residual requires "a Single Site at
which **only** Electricity Storage and/or Electricity Generation occurs, with an export MPAN and an
import MPAN with associated metering equipment which **only** measures export from ... and import for
or directly relating to ... (and not export from another source and/or import for another activity)".
A waste transfer station or a research campus with process load cannot satisfy "not ... import for
another activity". NESO's own guidance is quoted in the register to the same effect: "if a site has a
generator or a generator with storage facility alone, the residual charges should not apply. However,
if there is anything else at that site such as a factory or a data centre then that whole site would
be liable." This test is a clean **no** for all seven sites as currently connected, and it is one of
the reasons the "separate storage MPAN" question in section 9 is worth asking.

**The licence.** Every route in tests 1 to 3 turns on holding a generation licence. Under the
Electricity (Class Exemptions from the Requirement for a Licence) Order 2001, a battery at the scale
modelled here (0.1 to 5 MW) is **licence exempt by default**. Ofgem's October 2020 storage licensing
decision created a storage-specific condition (SLC E1) requiring licensed storage operators to
"provide accurate information regarding their electricity storage facility to their relevant
suppliers" so that charges are calculated correctly. Commentary on that decision is explicit that the
point of it is to let an otherwise-exempt operator weigh voluntarily taking a licence in order to
secure FCL avoidance against the obligations a generation licence brings. **A licence is available and
it is a commercial decision, not a size gate.** I have not costed the obligations.

---

## 6. Why export is levy-penalised and BTM is not, and why the code is right

Levies are billed on **boundary import**. Ofgem's RO guidance is explicit that exports do not offset:
supply volumes "should therefore not include ... **any adjustment for exports (i.e. exports should not
be netted off against imports)**".

So on the default (no relief) treatment:

- **BTM cycle.** Charge 1 kWh: boundary import rises 1 kWh, levy paid on 1 kWh. Discharge 0.88 kWh
  into site load: boundary import falls 0.88 kWh, levy avoided on 0.88 kWh. Net levy cost = the
  round-trip losses, 0.12 kWh. This is why a flat levy creates no artificial spread on the BTM case.
- **Export cycle.** Charge 1 kWh: levy paid on 1 kWh. Export 0.88 kWh: no import to net against, no
  levy recovered. Net levy cost = the **whole** charged kWh, 8.3 times the BTM leakage at 88 per cent
  RTE.

The engine implements exactly this and I checked it rather than assuming it:

- `optimisation/milp_solver.py:1184-1185` prices charge and BTM discharge at
  `energy_price + duos + levy_rate`, so the objective's levy term is `levy x (discharge - charge)`,
  the net-import convention.
- `optimisation/milp_solver.py:1685-1706` builds the schedule with `discharge_kw=discharge_sol`
  (behind the meter only) and `export_kw=export_sol` as a **separate** array.
- `finance/cashflows.py:654-655` charges `levy_rate x max(0, charge_kwh - discharge_kwh)` where
  `discharge_kwh` comes from `annual_discharge_kwh` (`optimisation/schedule.py:113-116`), which sums
  `discharge_kw` only.

Because export never enters `discharge_kwh`, the exported kWh receives **no levy offset** in finance,
which is correct. I looked for the opposite defect (export wrongly netting the levy and flattering the
study basis) and it is not there.

**One thing the code does not do: say so.** The no-relief assumption is implicit in the arithmetic and
appears nowhere as a declared, sourced treatment. Under D45 that is the wrong shape for an input this
large: 47 per cent of a grid site's break-even rests on an unstated assumption. Section 10 specifies
the fix.

---

## 7. Sensitivity: what relief would do, and what it would not

Three treatments, same formula, same site inputs. **All figures below are desk arithmetic on record
inputs, using the formula verified in section 2. They are not run outputs.**

- **A (today):** every levy line charged on export-bound charging energy.
- **B (established relief only):** RO + CfD + BSUoS removed, that is 5.5845 p/kWh. These are the three
  lines with a published route in section 4. Requires a generation licence, supplier declarations and
  pass-through.
- **C (bounding case):** the entire levy stack removed. Included only as a bound. It is not supported
  by anything in section 4, since CCL alone has no published relief.

| Site | A: today | B: RO+CfD+BSUoS relieved | C: all levies removed |
|---|---:|---:|---:|
| Bishops Cleeve MRF | 178.15 | 114.69 | 94.20 |
| Ewelme (invoice anchored) | 181.03 | 117.57 | 97.07 |
| Harwell | 184.55 | 121.09 | 97.07 |
| Bishops Cleeve Ash Plant | 200.53 | 137.07 | 116.57 |
| Knowl Hill | 280.69 | 217.23 | 196.73 |
| Colnbrook (private wire) | 139.52 | **no change** | 92.57 |
| Tanhouse (private wire) | 242.78 | **no change** | 93.47 |

Against the campaign's sourced export tariff (Elexon MID, 2026-07-13, 48 half-hours: mean
**109.61**, min 66.28, max **147.22** GBP/MWh, `data/fetched/elexon/mid_2026-07-13.csv`):

- **Case B does not clear the flat mean anywhere.** The best grid site, Bishops Cleeve MRF at 114.69,
  is still 1.05x above 109.61. The gap is smaller than the uncertainty in a one-day summer mean, so
  the honest reading is "not established either way", not "nearly viable".
- **Case B changes what a shaped route could do.** Under A, **no** grid site's break-even sits below
  that day's peak half-hour of 147.22. Under B, four of the five do (Bishops Cleeve MRF 114.69, Ewelme
  117.57, Harwell 121.09, Bishops Cleeve Ash 137.07); Knowl Hill at 217.23 does not, and would not
  under any levy treatment.
- **Neither case helps the two private-wire sites at all**, because they carry no GB levy to relieve.

**Direction of effect, stated as required.** Every relief in case B **lowers** break-even, that is, it
**improves** the export case. That is the direction that needs more justification, not less, and it is
the reason this document recommends **no change to the default** and a per-line, per-site evidence
requirement before any of it is applied (section 10).

**Three things push the other way and are not in the table.**

1. **The Capacity Market supplier charge is missing from the modelled stack entirely.** The register
   records "No authoritative public GBP/kW supplier charge rate was locatable" and notes a
   second-tier scaling of roughly GBP 11/MWh. Adding it would raise every case-A break-even by roughly
   12.5 GBP/MWh. It is FCL-relievable and it is peak-window only, so it lands mostly on case A.
2. **The RO rate on the record is understated by construction.** The site records note the register
   "flags this as its own derivation, not a published rate, and notes the true pass-through EXCEEDS it
   because ROCs trade above buy-out."
3. **The export price is a wholesale reference, not an achievable price.** Elexon MID is a market
   index. A real route to market deducts a trading or optimiser fee and carries imbalance exposure,
   and the modelled tariff is a **one-day summer mean**, not a forward annual curve. Every one of
   those moves the achievable price down.

Taken together: the case-A break-evens are, if anything, **too low**, and the export price is, if
anything, **too high**. The conclusion that export is loss-making at these sites is robust to levy
treatment.

---

## 8. Tanhouse and Colnbrook: a different question entirely

The two sites the break-even figures were quoted from are 100 per cent private wire off Grundon's
energy-from-waste plant. What their records call `levies` is a single line, back-solved as the
difference between an invoiced blended delivered cost and a volume-weighted commodity cost. The site
record says so: "it is the arithmetic difference between two invoice figures, its composition is
unknown".

Three consequences.

1. **No GB levy relief can touch it.** RO, CfD, CM, FiT and Nuclear RAB are obligations on **licensed
   suppliers**. A licence-exempt private-wire supply sits outside them by construction, and BSC P442
   (approved by Ofgem May 2024; secondary sources give the implementation date variously as February
   and July 2025, so treat the date as unconfirmed) formalised the machinery for excluding exempt
   supply volumes from the CfD and Capacity Market levies via an Exempt Supply Notification Agent.
   **It is therefore unlikely that the residual is policy cost at all.** It is far more likely to be
   EfW contract margin, capacity or standby charges, metering and administration, and at Tanhouse the
   solar arrangement.

2. **The 3.2x gap between the two sites is itself the evidence that it is contractual.** Tanhouse's
   residual is 13.140 p/kWh against Colnbrook's 4.131 p/kWh, on the same private wire, with commodity
   rates differing by only 0.5 p/kWh. No statutory levy behaves that way. A contract does. The
   Tanhouse record already flags this as "STILL UNEXPLAINED AND MATERIAL".

3. **Export is not a consent question here, it is a connection question.** Both sites are recorded as
   100 per cent non-grid. Exporting requires a grid connection, an export MPAN, a MEC and G99, none of
   which is evidenced to exist. The D46 study-basis run correctly exported zero, and at Tanhouse the
   break-even is 2.21x the derived tariff, so the zero is an economic decision on top of a structural
   one.

**Tanhouse's 61.5 per cent levy share of break-even is therefore not a levy finding. It is a finding
about one unexplained contractual line, and it is the highest-value single data ask in the estate.**

---

## 9. The honest limit of desk research, and the exact questions

Published rules settle the entitlement. They do not settle the bill. Three things are simply not
determinable from Ofgem, NESO, ENA or legislation, and pretending otherwise would be worse than
useless.

**(a) Pass-through is a supplier contract term.** The FCL relief lands on the **Import Supplier's**
EMRS liability. Nothing obliges the supplier to pass it to the customer, and nothing published tells
us what any of these sites' contracts say. Four of the seven sites have no confirmed tariff at all
(Harwell's whole stack is an explicit analogy from Ewelme).

**(b) The mixed-purpose battery is unresolved in the published rules.** A battery that both serves
site load and exports has import of two legal characters. The declaration machinery is per metered
facility. I found no published rule apportioning a single asset's import between relievable and
non-relievable purpose, and no published treatment of the round-trip loss fraction, which is consumed
and never re-exported.

**(c) Elexon's operational guidance could not be retrieved.** `elexon.co.uk` returned HTTP 403 to
automated retrieval on every attempt (consistent with the note already in
`bess_constants_register.md`), and `bscdocs.elexon.co.uk` serves only navigation metadata to a
non-browser client. The P395 and P375 declaration requirements are therefore established from
**Ofgem's** decision documents and from search-index summaries of Elexon material, not from Elexon's
own text. Anyone relying on the detail should open those pages in a browser.

### Questions to put, and to whom

**To each grid site's electricity supplier** (Harwell, Ewelme, Bishops Cleeve x2, Knowl Hill):

1. On this MPAN, which lines do you charge volumetrically on import and at what rate for the current
   charging year: CCL, RO, FiT levelisation, CfD supplier obligation, Capacity Market supplier charge,
   Nuclear RAB, BSUoS, AAHEDC? (This also closes the register's open Capacity Market rate gap.)
2. If a battery behind this MPAN is registered as an asset under BSC P375/P395 with an EMR AMSID
   declaration, will you exclude the declared storage import from the **CfD and Capacity Market**
   elements of FCL, and will that exclusion be reflected in our price or retained by you?
3. Will you apply the same exclusion to the **RO** and **FiT** lines, per Ofgem's RO Guidance for
   Suppliers Appendix 4 A.4.4?
4. Will you submit an **SVA Non-Final Demand Facility declaration** for BSUoS exclusion under BSC
   P383/P419, and pass the exclusion through?
5. Do you require the storage to sit in a separate legal entity holding a **generation licence**, and
   what evidence do you need?
6. How would you treat charging energy that is **discharged behind the meter** rather than exported?
   Will you accept a split of one asset's import between relievable and non-relievable purpose, and on
   what metering? What happens to the round-trip losses?
7. Do you charge **CCL** on storage import, and if the operator obtains HMRC "directed utility" status
   or is otherwise treated as an electricity utility, would supplies to it fall outside CCL?

**To the DNO** (SSEN SEPD for the SSEN-area sites, NGED elsewhere):

8. Would a battery behind an existing C&I import MPAN with process load qualify as a **Non-Final
   Demand Site** under DCUSA section 10 and Appendix 3? If not, what separation (separate import and
   export MPANs, separate metering, separate connection) would be required, and what would it cost?
9. What export capacity (MEC) is available at this connection, and what is the G99 route and timescale?

**To HMRC:**

10. Is there any CCL relief for electricity supplied to an electricity storage facility that
    subsequently exports to the grid? Notice CCL1/3 lists none. If the operator sells the exported
    electricity, can it be treated as an electricity utility so that supplies to it are outside the
    scope of CCL?

**To Grundon / the EfW operator** (Tanhouse and Colnbrook, the highest-value ask in the estate):

11. Line-by-line composition of the non-commodity element of the private-wire supply, 13.140 p/kWh at
    Tanhouse and 4.131 p/kWh at Colnbrook on the 2025 invoiced basis, and why they differ by 3.2x on
    the same wire.
12. Would battery charging volumes bear the same non-commodity charge? Would they bear it if the
    energy is subsequently exported rather than consumed on site?
13. Does any grid connection exist at either site, and what is its capacity?

---

## 10. What to change in the code, specified but not done

I was read-only over `src/`, `tests/`, `assumptions/` and `data/`. Nothing was changed. This is the
specification, handed back.

**The defect being fixed is not a wrong number. It is an unstated assumption carrying 47 per cent of a
break-even.** Today `levy_rate_gbp_per_kwh` (`optimisation/milp_solver.py:209`) sums every key in
`site.tariff_stack.levies` and applies the total to every charged kWh, and `finance/cashflows.py:637`
does the same on the net-import convention. The treatment "no line is relieved on export-bound
charging" is correct as a default and is nowhere declared, sourced or reportable.

**Recommended change (basis-level, not per-kWh).** The regulatory finding in section 5 is that the
machinery is per facility, not per kWh, so a per-kWh split would model something that does not exist.
Model the basis instead:

1. **New assumption-library section** `levy_treatment`, keyed by levy **line name**, each entry a
   `DerivedAssumption`-shaped record: `{ relievable_on_export_bound_charging: true | false |
   needs_review, basis: <citation>, status, owner_or_reviewer, last_reviewed }`. **Seed every line at
   `false`** with basis "no relief established for this site; see `docs/export_levy_treatment.md`", so
   the default run is byte-identical to today. Sections 4 and 5 give the citation text for RO, CfD,
   CM, BSUoS, CCL, FiT, Nuclear RAB and AAHEDC.
2. **A run-level `levy_relief_basis`**, absent by default, with exactly two values: `none` and
   `declared_non_final_demand`. The second asserts that the storage is a separately declared facility
   under P383/P419/P395 with a generation licence, and it must carry the same `pending_permission`
   discipline D45 applies to consent: it is a **permission and a supplier contract term**, never a
   derived fact, and it must never be written as granted. It enters `input_hash` exactly as
   `study_basis` does, so a relieved run can never share a `run_id` with an unrelieved one.
3. **One helper, read in exactly two places** (`milp_solver`'s objective and `cashflows`' levy line),
   returning `(levy_charged_on_import, levy_relieved, unresolved_lines)`. If `unresolved_lines` is
   non-empty the run must not silently treat them as either relieved or charged: charge them and say
   so, per D45's "never silently read as zero" on the pricing path.
4. **Reachability, at the layer a reader reads.** The acceptance test must assert on the **rendered
   pack**, not on the helper: that every published pack states which levy-relief basis produced it,
   names the lines relieved and the lines not, and that a `declared_non_final_demand` pack carries the
   "requires supplier confirmation and a generation licence" label. Ten of the eleven inert-fix
   instances in this repo passed tests at their own layer.
5. **Do not implement a partial-relief split** until question 6 in section 9 has an answer. Under
   `declared_non_final_demand` the honest model is that the declared facility's whole import is
   relieved and its behind-the-meter discharge into site load is **not** a relievable purpose, so a
   run on that basis with material BTM discharge must record the split as an unresolved ask rather
   than resolve it silently.

**Second, smaller item.** The Capacity Market supplier charge is absent from every site's levy stack
(section 7, point 1). That is a **null on the pricing path** in the D45 sense: it reads as GBP 0 and
biases break-even down, that is, it flatters export. It should be sourced or carried as a declared
`needs_review` line that cannot read as zero. Question 1 in section 9 is the cheapest way to source it.

---

## 11. What I did not resolve

- **FiT:** Ofgem's own guidance contradicts itself (section 4). Not resolved. Modelled as charged.
- **Nuclear RAB:** no storage treatment found in SI 2023/254 or EMRS material. Declared gap. Modelled
  as charged.
- **Round-trip losses under relief:** P395's footnote relieves electricity "which is then
  re-exported"; the loss fraction is consumed and never re-exported. The facility-based machinery
  appears to exclude metered import including losses, but I found no published apportionment.
  Declared gap.
- **Mixed-purpose apportionment:** no published rule found (section 9b). This is the one that would
  most change the answer for a behind-the-meter asset.
- **Elexon operational text:** not retrievable by automated fetch (section 9c).
- **AAHEDC:** treated as not avoidable on the register's second-tier source. Immaterial at 0.5
  GBP/MWh of break-even; not chased further.
- **The Tanhouse residual:** unexplained, 3.2x Colnbrook's, 61.5 per cent of that site's break-even.
  Not a levy question. Question 11.
- **Generation licence cost of compliance:** not costed. It is the price of every relief in case B.

---

## Sources

Primary, retrieved 2026-08-16 unless stated.

- Ofgem, *Balancing and Settlement Code (BSC) P395: Aligning BSC Reporting with EMR Regulations, an
  enduring solution*, decision 6 October 2022, implementation 2 November 2023.
  https://www.ofgem.gov.uk/sites/default/files/2022-10/P395%20-%20Decision%20.pdf
- Ofgem, *Renewables Obligation Guidance for Suppliers*, version 10, Appendix 4 (A.4.3 to A.4.7).
  https://www.ofgem.gov.uk/sites/default/files/2025-03/Renewables-Obligation-(RO)-Guidance-Suppliers.pdf
  (The file's own cover states "Publication date: 2 April 2026, Version number: 10" while the hosting
  path reads `2025-03`. Cited on the cover date; flag the discrepancy if it is ever load-bearing.)
- Ofgem, *Feed-in Tariffs: Guidance for Licensed Electricity Suppliers*, version 17.1, September 2024,
  paragraphs 8.32 to 8.34.
  https://www.ofgem.gov.uk/sites/default/files/2024-09/FIT_Guidance_for_Licensed_Electricity_Suppliers_V17.1.pdf
- HMRC, *Excise Notice CCL1/3: Climate Change Levy, reliefs and special treatments for taxable
  commodities*, sections 2, 3 and 6.
  https://www.gov.uk/government/publications/excise-notice-ccl13-climate-change-levy-reliefs-and-special-treatments-for-taxable-commodities
- NESO, *Draft BSUoS Tariffs for 2026/27*, version 1.0, October 2025, page 9 (Final Demand
  definition). https://www.neso.energy/document/369526/download
- Ofgem, *CMP308: Removal of BSUoS charges from Generation*, decision.
  https://www.ofgem.gov.uk/decision/cmp308-removal-bsuos-charges-generation
- Ofgem, *BSC P419: Extension of P383 to include non-final Demand*, decision 25 April 2022,
  implemented 23 February 2023.
  https://www.ofgem.gov.uk/decision/balancing-and-settlement-code-bsc-p419-extension-p383-include-non-final-demand-p419
- Ofgem, *Decision: clarifying the regulatory framework for electricity storage, changes to the
  electricity generation licence*, 2 October 2020 (storage licence condition E1).
  https://www.ofgem.gov.uk/publications/decision-clarifying-regulatory-framework-electricity-storage-changes-electricity-generation-licence
- Ofgem, *Decision for BSC P442*, May 2024 (licence-exempt supply, exclusion of exempt volumes from
  CfD and Capacity Market levies).
  https://www.ofgem.gov.uk/sites/default/files/2024-05/Ofgem_decision_for_BSC_P442_Modification.pdf
- The Renewables Obligation Order 2015, SI 2015/1947, article 8.
  https://www.legislation.gov.uk/uksi/2015/1947/article/8
- The Electricity Capacity (Supplier Payment etc.) Regulations 2014, SI 2014/3354, regulation 2(1).
  https://www.legislation.gov.uk/uksi/2014/3354/regulation/2
- The Nuclear Regulated Asset Base Model (Revenue Collection) Regulations 2023, SI 2023/254, Part 3.
  https://www.legislation.gov.uk/uksi/2023/254
- The Electricity (Class Exemptions from the Requirement for a Licence) Order 2001 (Class A small
  generators, 10 MW / 50 MW thresholds), via secondary commentary.

Secondary and in-repo:

- `docs/bess_constants_register.md` rows: Non-Final Demand Site qualifying criteria (line 44); CCL main
  rate (line 316); CfD/CM/Nuclear RAB operational cost levies (line 319); AAHEDC tariff (line 321); CM
  supplier charge statutory basis (line 323); TNUoS TCR fixed bands (line 306); DCUSA Non-Final Demand
  Site criteria (line 328); Modo Energy import charges (lines 406, 447, 484); P442 (line 449).
- `examples/real/sites/*.json` tariff stacks and their provenance notes.
- `data/fetched/elexon/mid_2026-07-13.csv` (48 half-hours, mean 109.611667 GBP/MWh).
- `assumptions/gb_v1_9.yaml`: `round_trip_efficiency.ac_round_trip`,
  `battery_opex.degradation_marginal_cost_gbp_per_mwh`.
- `docs/shared_work/HANDOFF_LOG.md` entry 752 (published break-even range and the 7.4 p/kWh
  observation this document started from).

Retrieval note: `elexon.co.uk` and `bscdocs.elexon.co.uk` were not retrievable by automated fetch (403
and navigation-only respectively). Elexon-sourced statements here come from Ofgem decision documents
or from search-index summaries, and are labelled where that is the case.
