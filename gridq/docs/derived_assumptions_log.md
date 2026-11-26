# Derived assumptions log

**Assumption release:** `gb_v1_8` (GB, released 2026-08-12)
**Prepared by:** Olly, 2026-08-13
**Feeds:** the "Modelling assumptions" section of every report produced on this release
(`docs/validation_requirements.md` §8.1) and the technical validation report's logged assumption
register (§8.2).

## Why this document exists

A validation run needs a value for every input it reads. Where the customer's own evidence supplies
one, the run uses it. Where it does not, the run uses a value derived from a published source or from
a stated method, and **that substitution is only acceptable if it is disclosed in language a reader
can audit.** This log is that disclosure.

The standard it is written to (decision D45, as amended 2026-08-12): an assumption is admissible if
it is reasoned and declared, and inadmissible if it is silent. A missing input is never left empty on
a priced path, because an empty input does not read as "unknown" downstream. It reads as **zero**, and
a zero flows into P50, P90 and the debt cover ratio looking exactly like a measured result. Deriving a
published GB value (a network operator's own tariff table, a system operator's de-rating factor, an
official inflation forecast, a statutory formula) is **sourcing, not invention**. Where a search
genuinely failed, the value is **refused** rather than read as zero, and the failed search is recorded
(section 6).

**What none of this makes bankable.** Every entry in `gb_v1_8` carries the status `needs_review` or
`placeholder`. Nothing in the library is `confirmed`, by design, so no quote produced on this release
presents as bankable. A published national tariff is evidence that a rate exists; it is not evidence
that it is the rate on this site's bill. That check is a human step and it has not been taken yet.

---

## 1. Read this first: the changes that make a site look better

Three of the changes below improve modelled economics. They are listed first, on purpose, because a
correction that helps the numbers deserves more scrutiny than one that hurts them, not less.

**1. The Capacity Market non-delivery penalty rate fell by a factor of six (L5).** The rate was
carried at 0.25 of the clearing price per MWh of shortfall. The governing statute sets it at 1/24
(0.0416667). At the 2029-30 four-year-ahead clearing price of £27,100/MW/yr, the penalty rate falls
from £6,775/MWh of shortfall to £1,129/MWh. **Every quote carrying contracted Capacity Market revenue
improves**, because the bounded worst-case penalty line shrinks about six-fold. The correction was
made because the primary legislation was read directly and is explicit, not because the old figure was
inconvenient. The two caps that bound the worst case (200% of a monthly capacity payment, 100% of an
annual one) are unchanged.

**2. Capacity Market inflation indexation (L3).** Setting the inflation rate to 2.0% escalates
four-year-ahead Capacity Market revenue, which are inflation-linked agreements. That part of the change
is favourable. The same rate escalates operating cost, business rates, insurance, metering and network
operator charges, which is unfavourable. The two effects are **never netted into a single line**: they
are shown separately, and the net result is site-specific.

**3. Long-duration Capacity Market de-rating (L4).** Closing the 5 hour and 5.5 hour de-rating bins
raises the de-rated capacity a 5 hour asset can sell, because the previous lookup fell back to the
4.5 hour bin (0.4953 against the published 0.5509). **No site in the current campaign is affected**:
all five are shorter than 5 hours.

**One further favourable effect, and it deserves saying because its cause is a defect rather than
prudence (L1).** Two of the five campaign sites currently carry no distribution rate at all, which
prices one of only two floor revenue streams at zero. Giving them their own area's published rate adds
revenue that is missing today. For the other sites, which are priced on the retired East Midlands
proxy, the same closure reduces revenue. Both halves are stated in L1 rather than netted.

Everything else in this log moves the numbers the other way or leaves them unchanged. The two largest
unfavourable movements are the network charge closure (L1), which reduces modelled red-band avoidance
value on every site already priced on the retired proxy, and the transmission residual band table (L8),
which raises an unavoidable fixed line on the customer's bill by roughly £9,700 per site per year.

---

## 2. How to read an entry

Each entry states the same nine things, in the same order:

| Field | What it tells you |
|---|---|
| What was missing | The absent input, in plain words |
| Value now used | The figure the run consumes |
| Basis | The reasoning that justifies it, and the source of the **method** where the method comes from published work |
| Anchors | What the value or shape was calibrated or scaled to, and therefore which components are **measured** rather than modelled |
| Corroboration | Any independent check, and what it does and does not establish |
| Citation | The document, with a link and its publication or effective date |
| Direction of effect | Whether the change makes a site look better or worse |
| Confidence ceiling | The highest confidence band a result depending on this value can carry |
| Confirmation ask | Exactly what the customer must supply to retire the assumption |

"Measured" means taken from the customer's own evidence (an invoice, a meter, an agreement).
"Modelled" means produced by an assumption, however well reasoned.

---

## 3. Index of derivations

| # | Derivation | Value now used | Direction | Confidence ceiling |
|---|---|---|---|---|
| L1 | Distribution use of system band windows and unit rates, 13 of 14 licence areas | Each area's own 2026/27 published rates | **Worse** for the sites priced on the retired proxy; **better** for the two priced on no rate at all | Medium |
| L2 | Residual charging band thresholds | LV 80/150/231 kVA, HV 422/1000/1800 kVA | Enables L8; unfavourable in effect | Medium |
| L3 | Consumer price inflation rate | 2.0% per year | **Mixed**, stated per line, never netted | Medium |
| L4 | Capacity Market de-rating, 5h and 5.5h bins | 0.5509 / 0.6052 (T-4 2029-30); 0.6745 / 0.7329 (T-1 2026-27) | **Better** for 5h+ assets only; no campaign site affected | Medium |
| L5 | Capacity Market non-delivery penalty rate | 0.0416667 of clearing price per MWh | **Better**, about 6x reduction in the modelled penalty line | Medium |
| L6 | Capacity Market penalty caps | 200% monthly, 100% annual (unchanged) | Neutral, no number moved | Medium |
| L7 | Fixed operations and maintenance cost | £12,000/MW/yr (unchanged) | Neutral now; residual risk points **worse** | Low |
| L8 | Transmission demand residual band table | £38.180103/site/day at band LV4 | **Worse**, raises an unavoidable bill line | Medium |
| L9 | Demand Flexibility Service zone identifiers | Numbered 1 to 12, no published names | Neutral, no priced stream reads it | Low |
| L10 | Demand Flexibility Service guaranteed acceptance price | Definition only, current value refused | Neutral, off the priced path | Low |
| S1 | Connection voltage level, five campaign sites | Low voltage tariff applied | **Better**, and it is the largest single site-level uncertainty | Medium |
| S2 | Residual charging band, five campaign sites | Band 4, inferred from agreed capacity | **Worse** | Medium |
| S3 | Half-hourly load basis, five Grundon sites | Archetype within-day shape, scaled to the site's own invoices | Unknown and untestable; see section 5.3 | Medium (binding cap) |

---

## 4. Library derivations

### L1. Distribution use of system band windows and unit rates, 13 of 14 licence areas

| | |
|---|---|
| **What was missing** | Great Britain has 14 electricity distribution licence areas and each publishes its own charging bands and unit rates. The previous release held real values for one area (East Midlands) and nothing for the other 13, so any site outside the East Midlands was priced on another region's rates. |
| **Value now used** | Each licence area's own 2026/27 red, amber and green time windows, unit rates, fixed charges and capacity charges, transcribed from that area's published charging statement. The full table is in section 4.1.1. |
| **Basis** | Direct transcription of each network operator's own Use of System Charging Statement for the 2026/27 charging year (final rates, effective 1 April 2026), downloaded on 2026-08-12. Each area's entry carries the statement title, version, link and the SHA-256 digest of the exact file that was read, so the transcription is checkable against the same bytes. No value is interpolated, averaged or carried across areas. Each area's published structure is preserved rather than normalised: five areas publish one unit rate per tariff family with fixed charges that vary by residual band, and four vary the unit rates themselves by band. |
| **Anchors** | None needed. These are published rates, not calibrated estimates. What remains modelled at site level is which tariff family applies (see S1) and which residual band the site sits in (see S2). |
| **Corroboration** | The variation is confirmed by an independent 2026/27 benchmark (Modo Energy) finding that network charge optimisation is net positive in only 4 of 14 regions at high voltage, ranging from a gain of about £9,800/MW/yr in the South Eastern area to a cost of over £58,000/MW/yr in the North of Scotland. That establishes that a single national figure is unsafe in either sign. It does not verify any individual rate transcribed here. |
| **Citation** | Fourteen separate statements, listed with links in section 4.1.1. All are final versions effective 1 April 2026, downloaded 2026-08-12. |
| **Direction of effect** | **Split, and both halves are stated.** For the sites priced on the retired East Midlands proxy of 7.777 p/kWh, the rates that actually apply are lower (6.515 p/kWh in the West Midlands; 5.471 p/kWh in the Southern area at residual band 4), so modelled red-band avoidance revenue **falls**: worse. For Colnbrook and Tanhouse, which carry no distribution rate at all today and therefore price red-band avoidance at zero (section 8, item 3), the closure **adds a floor revenue stream that is currently zeroed**: favourable relative to that defect, though only because the present state is wrong rather than conservative. Across Great Britain the direction is mixed, because some areas are far higher (14.833 p/kWh in the South West). The full spread is 4.257 to 14.833 p/kWh for nominally the same low voltage half-hourly tariff, a factor of 3.5, which is the size of the error the old proxy could produce. |
| **Confidence ceiling** | **Medium.** The rate is published and traceable, but nobody has yet checked that it is the rate on this site's invoice, and the tariff family and band are still inferred (S1, S2). |
| **Confirmation ask** | One recent electricity invoice per site showing the distribution charge lines: the tariff name, the red, amber and green unit rates as billed, the fixed daily charge and the agreed capacity in kVA. |

**Two hazards this closure exposes, both preserved deliberately.** First, in South Wales the red
export credit (12.886 p/kWh) exceeds the red import charge (12.885 p/kWh) as printed, and in the
Southern area they are within 1%. A dispatch model without a hard charge-or-discharge switch can
exploit that near-equality and book a cycle that never happened physically. Second, fixed daily
charges for the same residual band differ by more than a factor of 1,000 across areas (2.35 p per
meter point per day in London against 3,592.14 in SP Manweb), so a fixed line must never be carried
across areas either.

#### 4.1.1 Evidence table, all 14 licence areas

Bank holidays take the **weekday** pattern in every area, so the red band applies on them. Every
statement prints its weekday row as "Monday to Friday (Including Bank Holidays)". Treating bank
holidays as green is described in the industry material as a common modelling error, and an earlier
brief in this project made exactly that error. Times are UK clock time (Europe/London), not UTC: a
series indexed in UTC is an hour out through British Summer Time.

| Licence area key | Area | Weekday red window | Weekend bands | Low voltage red rate (p/kWh) | Statement |
|---|---|---|---|---|---|
| NGED_EM | East Midlands | 16:00-19:00 | green only | 7.777 (all bands) | [Notice of charges, 1 April 2026](https://commercial.nationalgrid.co.uk/downloads-view-reciteme/684677) |
| NGED_WM | West Midlands | 16:00-19:00 | green only | 6.515 (all bands) | [Notice of charges, 1 April 2026](https://commercial.nationalgrid.co.uk/downloads-view-reciteme/684676) |
| NGED_SW | South West | 17:00-19:00 | amber and green | 14.833 (all bands) | [Notice of charges, 1 April 2026](https://commercial.nationalgrid.co.uk/downloads-view-reciteme/684674) |
| NGED_SWALES | South Wales | 17:00-19:30 | amber and green | 12.885 (all bands) | [Notice of charges, 1 April 2026](https://commercial.nationalgrid.co.uk/downloads-view-reciteme/684675) |
| UKPN_EPN | Eastern | 16:00-19:00 | green only | 8.149 (all bands) | [LC14 statement v1.6](https://media.umbraco.io/ukpn-cms/dlmiobrb/eastern-power-networks-lc14-statement-2026-v1-6.pdf) |
| UKPN_LPN | London | 11:00-14:00 and 16:00-19:00 | green only | 7.414 (no residual) to 4.257 (band 4) | [LC14 statement v1.3](https://media.umbraco.io/ukpn-cms/bqthjnqt/london-power-networks-lc14-statement-2026-v1-3.pdf) |
| UKPN_SPN | South Eastern | 16:00-19:00 | green only | 11.311 (all bands) | [LC14 statement v1.3](https://media.umbraco.io/ukpn-cms/m2aj0zfs/south-eastern-power-networks-lc14-statement-2026-v1-3.pdf) |
| SSEN_SEPD | Southern | 16:30-19:30 | amber and green | 8.530 (no residual) to 5.471 (band 4) | [SEPD DUoS charges 2026-27 v1.1](https://www.ssen.co.uk/globalassets/library/charging-statements-sepd/202627/sepd-duos-charges-2026-27_v1.1.pdf) |
| SSEN_SHEPD | North of Scotland | 16:00-19:00 | amber and green | 8.560 (no residual) to 7.850 (band 4) | [SHEPD DUoS charges 2026-27 v1.1](https://www.ssen.co.uk/globalassets/library/charging-statements-shepd/202627/shepd-duos-charges-2026-27_v1.1.pdf) |
| SPEN_SPD | SP Distribution | 16:30-19:30 | amber and green | 10.798 (all bands) | [LC14 statement 2026 v0.1](https://www.scottishpower.com/documents/d/guest/lc14-statement-2026_spd_v01) |
| SPEN_SPM | SP Manweb | 16:30-19:30 | amber and green | 11.785 (all bands) | [LC14 statement 2026 v0.1](https://www.scottishpower.com/documents/d/guest/LC14-Statement-2026_SPM_v01) |
| NPG_NORTHEAST | Northeast | 16:00-19:30 | green only | 8.522 (all bands) | [LC14 charging statement v0.3](https://www.northernpowergrid.com/sites/default/files/assets/Northern%20Powergrid%20%28Northeast%29%20-%20%202026-27%20LC14%20Charging%20Statement%20-%20v0.3.pdf) |
| NPG_YORKSHIRE | Yorkshire | 16:00-19:30 | green only | 5.829 (all bands) | [LC14 charging statement v0.3](https://www.northernpowergrid.com/sites/default/files/assets/Northern%20Powergrid%20%28Yorkshire%29%20-%20%202026-27%20LC14%20Charging%20Statement%20-%20v0.3.pdf) |
| ENWL | North West | 16:00-19:00 | amber and green | 9.417 (no residual) to 6.539 (band 4) | [Schedule of charges 2026-27 v1_1 final](https://www.enwl.co.uk/globalassets/about-us/regulatory-information/documents/current-charging-information/26-27/enwl---schedule-of-charges-and-other-tables--2026-27-v1_1-final.xlsx) |

Rates are re-fetched every charging year and must never be held flat. For context on why: across
2025/26 the average distribution charge fell about 6% while regional movements ran from +25% to -32%,
and capacity charges rose about 108% on average (Drax and NUS Consulting reporting).

### L2. Residual charging band thresholds

| | |
|---|---|
| **What was missing** | Four of the licence areas above vary their unit rates by residual charging band, and the transmission residual (L8) is banded the same way. Neither table can be read without knowing which band a site is in, and the band thresholds were not held anywhere. |
| **Value now used** | Low voltage: bands break at 80, 150 and 231 kVA. High voltage: 422, 1,000 and 1,800 kVA. Extra high voltage: 5,000, 12,000 and 21,500 kVA. The boundary is inclusive of the upper figure, so exactly 231 kVA is band 3, not band 4. Band 4 has no upper bound. |
| **Basis** | The published national threshold table, transcribed verbatim with its boundary rule. The bands were created by the regulator's Targeted Charging Review and the distribution thresholds published under the approved methodology. Allocation is on the 24 month average agreed import capacity for half-hourly sites, with re-banding possible under the industry code. |
| **Anchors** | None. This is a published table. What is inferred is the site's own band (S2). |
| **Corroboration** | The transmission residual table (L8) uses the same four-band structure and the same LV1 to LV4 and HV1 to HV4 labels, which confirms the two tables can be read off one band determination. |
| **Citation** | National Grid (Western Power Distribution), "Residual Banding: Amendments to Charging from April 2022", [guidance document](https://commercial.nationalgrid.co.uk/downloads-view-reciteme/623467), downloaded 2026-08-12. Bands created by Ofgem, "Targeted Charging Review: Decision and Impact Assessment", [21 November 2019](https://www.ofgem.gov.uk/decision/targeted-charging-review-decision-and-impact-assessment). |
| **Direction of effect** | Neutral in itself. Its effect arrives through L8 and S2, both of which are unfavourable: the campaign sites move from an assumed band 2 to band 4. |
| **Confidence ceiling** | **Medium.** The thresholds are published and firm; the band each site is billed under is not, because a site's band reflects its capacity history and any re-banding, not only its current capacity. |
| **Confirmation ask** | The agreed import capacity in kVA from the connection agreement, and the band as printed on an invoice. The invoice line settles it outright. |

### L3. Consumer price inflation rate

| | |
|---|---|
| **What was missing** | No inflation rate. Three questions were open: which index, what rate, and which lines are escalated. |
| **Value now used** | 2.0% per year, applied as a flat rate, on the consumer prices index (CPI) basis. |
| **Basis** | The official fiscal forecast has inflation falling from 3.4% in 2025 to 2.3% in 2026 and 2.0% from 2027 onwards, reaching the Bank of England target in late 2026 and staying near it to the end of the forecast period (2030-31). Using 2.0% flat understates 2026 escalation by 0.3 percentage points, which is immaterial over a 15 year horizon and conservative on inflation-linked revenue. CPI is chosen as the index rather than CPIH because it is the forecast basis, because the renewables obligation buy-out moved to CPI indexation on 1 April 2026, and because four-year-ahead Capacity Market agreements are CPI-indexed. CPIH is not interchangeable with CPI and is flagged if a CPIH-linked line appears. |
| **Anchors** | None. This is a forecast, not a calibration. |
| **Corroboration** | Two independent checks. A 15 year average CPI of 1.95% from the same forecaster's long-term determinants, used in the 2025 hurdle rate work for the energy department, which supports 2.0% as a long-run central figure. And the regulator's own 2025 long duration storage financial framework decision, which deflates the cost of capital at the Bank of England 2% target. Neither establishes the near-year path, only that 2.0% is the accepted long-run figure. |
| **Citation** | Office for Budget Responsibility, "Economic and fiscal outlook, March 2026", [obr.uk](https://obr.uk/efo/economic-and-fiscal-outlook-march-2026/). Corroboration: CEPA for DESNZ, "Hurdle rate estimates for electricity sector technologies", 2025, Table 4.9; Ofgem, "Decision, Financial Framework: LDES Window 1 Cap and Floor regime", September 2025. All read 2026-08-12. |
| **Direction of effect** | **Mixed, and reported per line rather than netted.** Escalating operating cost, business rates, insurance, metering and network operator charges makes every site look **worse**. Escalating inflation-linked four-year-ahead Capacity Market revenue makes Capacity Market sites look **better**. The net effect depends on the site's revenue mix. Note that the escalation machinery was not yet wired to any consumer at the time this release was cut, so the rate has no effect on a run until it is. |
| **Confidence ceiling** | **Medium.** A published central forecast is a reasonable basis, but a 15 year escalation path is a forecast throughout, and the discount rate basis has to be kept consistent with it (a nominal hurdle rate discounts nominal cashflows; the two must never be mixed). |
| **Confirmation ask** | Confirmation from portfolio finance of the index, the rate and the list of escalated lines, and confirmation that the discount rate is stated on the same real or nominal basis. |

### L4. Capacity Market de-rating, 5 hour and 5.5 hour bins

| | |
|---|---|
| **What was missing** | The de-rating curve held every duration bin from 0.5 to 12 hours except 5 and 5.5 hours. Under the conservative lookup rule (use the largest published bin at or below the asset's duration) a 5 hour asset fell back to the 4.5 hour factor. |
| **Value now used** | Four-year-ahead auction, delivery year 2029-30: 0.5509 at 5 hours and 0.6052 at 5.5 hours. One-year-ahead auction, delivery year 2026-27: 0.6745 and 0.7329. |
| **Basis** | The gap was closed by re-querying the same published system operator dataset, not by interpolating between neighbouring bins. The re-query also confirmed the top of the curve: the discrete bins from 8.5 to 12 hours (four-year-ahead) and 7.5 to 12 hours (one-year-ahead) all print 0.9216. |
| **Anchors** | None. These are published factors. |
| **Corroboration** | The published 5 hour factor is 0.5509 while five times the 1 hour factor is 0.5495. The 0.0014 difference is small but it is real, and it is the proof that interpolating the missing bins would have produced a wrong number rather than a harmless one. Only 2 of the 18 auction and delivery year combinations in the table are exactly proportional to duration. |
| **Citation** | NESO, Capacity Market de-rating factors, Data Portal resource `d94ff98d-39ba-40d6-9672-7433de0631ac`, [data portal page](https://www.neso.energy/data-portal/capacity-market-register/capacity_market_de-rating_factors), re-queried 2026-08-12. |
| **Direction of effect** | **Better, for 5 hour and 5.5 hour assets only.** A 5 hour asset previously sold de-rated capacity at 0.4953 and now sells at 0.5509, about 11% more. **No effect on any site in the current campaign**, all of which are shorter than 5 hours. |
| **Confidence ceiling** | **Medium.** The factors are published and current, but they are re-published for every auction and delivery year, so a factor is only true for the auction it belongs to. Auction type and delivery year must both be keyed on: the one-year-ahead factor is higher than the four-year-ahead factor in only 1 of the 6 delivery years where both are published, and lower in the other 5, so neither can substitute for the other. |
| **Confirmation ask** | Nothing from the customer. Re-query the dataset before each auction round and pin the auction the quote assumes. |

### L5. Capacity Market non-delivery penalty rate

| | |
|---|---|
| **What was missing** | Nothing was missing. The value carried was wrong, and it was wrong in the direction that penalised the site. |
| **Value now used** | 0.0416667 of the clearing price per MWh of shortfall, which is 1/24. Previously 0.25, which encodes a divisor of 4. |
| **Basis** | The statute was read directly. The Electricity Capacity Regulations 2014, Schedule 1 paragraph 5(3), in the current amended text with amendments applied through 23 July 2025, gives the penalty rate as `PRN = PEzx x (1/24)`, with PEzx the price in pounds per MW. The formula appears in the online version as an image, so it was verified from the PDF rendering of the same schedule. The pending 2026 amending instrument was checked and does not touch Schedule 1 paragraph 5, so the formula stands. |
| **Anchors** | None. This is primary legislation. |
| **Corroboration** | The library's own sourced description of the penalty regime already implied 1/24 while the rate key carried 0.25. The previous release held that contradiction open deliberately rather than resolve it in the wrong direction. It is now resolved toward the statute, and the two agree. |
| **Citation** | The Electricity Capacity Regulations 2014 (SI 2014/2043), Schedule 1 paragraph 5(3): [legislation.gov.uk](https://www.legislation.gov.uk/uksi/2014/2043/schedule/1), current amended text read 2026-08-12; formula verified from the [PDF rendering](https://www.legislation.gov.uk/uksi/2014/2043/schedule/1/data.pdf). |
| **Direction of effect** | **Better, and this is the most flattering change in the release.** It reduces a modelled cost, so it improves every quote that carries contracted Capacity Market revenue. At the 2029-30 four-year-ahead clearing price of £27,100/MW/yr the penalty rate falls from £6,775/MWh of shortfall to £1,129/MWh, so the bounded worst-case penalty line shrinks about six-fold. In cash terms, a 1 MW shortfall over one half-hour settlement period costs about £565 rather than about £3,388. |
| **Confidence ceiling** | **Medium.** The rate itself rests on primary legislation, which is the strongest basis available. The ceiling is set lower by what it multiplies: the Capacity Market revenue line it applies to is itself unconfirmed, and no entry on this release is confirmed. |
| **Confirmation ask** | Nothing further on the rate. What still needs confirming is the Capacity Market agreement itself: the auction, the delivery year, the de-rated capacity and the clearing price the site actually holds. |

**Context that belongs next to this number.** No system stress event has ever been declared since the
Capacity Market began, and there have been only 13 capacity market notices since 2019, so the expected
non-delivery penalty in the base case is close to zero either way. The real Capacity Market risk is
failing a satisfactory performance day or an extended performance test, which leads to termination.
That is a different kind of risk from losing hours of revenue and it cannot be modelled as an
availability haircut.

### L6. Capacity Market penalty caps

| | |
|---|---|
| **What was missing** | Nothing. The caps were held at the right values on weak evidence (perimeter research), and the evidence has been replaced. |
| **Value now used** | 200% of the monthly capacity payment, 100% of the annual capacity payments. **Both unchanged.** |
| **Basis** | Primary legislation (regulation 41(3A) of the 2014 regulations) plus the system operator's own auction static data, which records a 200% monthly cap and a 100% annual cap for every auction from the 2016-17 transitional round to the 2029-30 four-year-ahead auction. |
| **Anchors** | None. |
| **Corroboration** | The two sources agree with each other and with every auction on record, which is as close to settled as a public source gets. |
| **Citation** | SI 2014/2043 regulation 41(3A); NESO, [Capacity Market auction static data](https://www.neso.energy/data-portal/capacity-market-register/capacity_market_auction_static_data), read 2026-08-12. |
| **Direction of effect** | **Neutral. No number moved.** The status of both entries improved from placeholder to needs review because the evidence improved. |
| **Confidence ceiling** | **Medium.** Still not confirmed, because the caps that bind are the ones written into the site's own agreement. |
| **Confirmation ask** | The penalty and cap clauses of the capacity agreement, once one exists. |

### L7. Fixed operations and maintenance cost

| | |
|---|---|
| **What was missing** | The £12,000/MW/yr figure had no citable source at all. A review had also read it as roughly half of what it should be. |
| **Value now used** | £12,000/MW/yr. **Unchanged.** What changed is that it now has a basis and a bracket. |
| **Basis** | The review's comparison did not hold: it set an all-in operating cost figure against a key that covers pure operations and maintenance only, excluding insurance, business rates, metering and network operator charges, all of which this model books separately. Decomposed, a 2025 published grid scale portfolio implies about £24,600/MW/yr all-in. Removing the lines booked separately (£13,200/MW/yr at 1 MW: business rates £8,000, insurance at 0.5% of £500,000 capex, metering £1,200, network operator charges £1,500) leaves about £11,400/MW/yr for operations, maintenance, land rent, optimiser fees and portfolio overhead at grid scale. A national laboratory assessment gives a narrow scope lithium iron phosphate fixed operations and maintenance figure of about £2,500/MW/yr for a 1 MW two hour system, rising to about £4,000/MW/yr at four hours, on a scope covering minor maintenance plus amortised power-equipment refurbishment only. |
| **Anchors** | The reconciliation is checkable against the model's own separately booked lines: £12,000 plus £13,200 gives £25,200/MW/yr all-in at 1 MW, within 2.5% of the £24,600 published grid scale figure. |
| **Corroboration** | The two published sources bracket the figure from below and, at grid scale, from just above. Stated precisely, and correcting the release's own wording: £12,000 does not sit between them, it sits about 5% **above** the decomposed grid scale residual of £11,400. That is the conservative side, because a higher operating cost makes a site look worse. |
| **Citation** | Pacific Northwest National Laboratory, "Energy Storage Grand Challenge Cost and Performance Assessment 2022" (PNNL-33283), August 2022, Figure 4.3, [PDF](https://www.pnnl.gov/sites/default/files/media/file/ESGC%20Cost%20Performance%20Report%202022%20PNNL-33283.pdf). Gresham House Energy Storage Fund plc, "Annual Report and Financial Statements 2025", [PDF](https://greshamhouse.com/wp-content/uploads/2026/04/GRID-Annual-Review_25_20.04-FINAL.pdf). |
| **Direction of effect** | **Neutral today**, because no value moved. The residual risk points one way only: a 0.1 to 5 MW behind the meter site loses grid scale economies (call-out costs, per-site telecoms, small contract pricing), so the true figure is more likely above £12,000 than below it. Nothing published quantifies that premium. The commercial-scale fixed cost of 2.5% of capex published in the National Renewable Energy Laboratory's Annual Technology Baseline is **not** usable here, because it explicitly includes battery augmentation to hold rated capacity, which this model already prices separately. Using it would count degradation twice. |
| **Confidence ceiling** | **Low.** A bracketed estimate from grid scale comparators is not a quote for this asset class, and the direction of the residual error is known but unquantified. |
| **Confirmation ask** | One real operations and maintenance contract quote for a behind the meter commercial site at 0.1 to 1 MW scale, in pounds per year, with its scope stated. |

### L8. Transmission demand residual band table

| | |
|---|---|
| **What was missing** | The transmission network demand residual is a fixed daily charge set by band. The band table was not held, so the per-site figure had to be assumed. |
| **Value now used** | The published 2026/27 band table, effective 1 April 2026. At band LV4 the rate is £38.180103 per site per day. |
| **Basis** | Direct transcription of the system operator's final transmission tariff publication for 2026/27, table 10, the non-locational fixed bands applied to final demand sites. Band thresholds are fixed for the current price control period. The per-site pounds figure stays derived per site: it is the band rate multiplied by the days in the charging year, with the band from L2. |
| **Anchors** | The band comes from the site's agreed capacity (see S2). Nothing else is calibrated. |
| **Corroboration** | The 2025/26 publication was transcribed alongside it, which converts a previously unverified note about a large April 2026 rise into a measured one: total demand residual revenue rose from £3,836m to £6,265m, up 63% in a single year. Band LV4 rose from £22.739548 to £38.180103 per day, up 68%. High voltage band 2 rose 87%. |
| **Citation** | NESO, "January 2026 Final TNUoS Tariffs for 2026/27", table 10, [document](https://www.neso.energy/document/376336/download), effective 1 April 2026 to 31 March 2027. Comparison year: NESO, "January 2025 Final TNUoS Tariffs for 2025/26", table 10, [document](https://www.neso.energy/document/353086/download). |
| **Direction of effect** | **Worse for the customer's modelled bill.** The site stacks assumed band LV2 at £11.511840 per day. The evidenced capacities imply band LV4 at £38.180103 per day, which is 3.3 times higher and about £9,700 more per site per year. This charge is **entirely unavoidable by dispatch**: a behind the meter battery cannot reduce it, so it raises the customer's total cost without raising the saving the battery can deliver. Increasing import capacity can also raise it by pushing the site up a band, which is a real cost of oversizing a connection that sizing must not ignore. |
| **Confidence ceiling** | **Medium.** The band rates are published and firm. The band is inferred (S2), and the bands moved sharply year on year, so a forward model must re-fetch each January's publication rather than hold the figure flat. |
| **Confirmation ask** | The transmission residual line and band as printed on an invoice, or the agreed capacity from the connection agreement. |

### L9. Demand Flexibility Service zone identifiers

| | |
|---|---|
| **What was missing** | The service is zonal and the model held a zone count but no zone identifiers, so a site could not be allocated to a zone. |
| **Value now used** | The 12 zones are identified **by number only**, 1 to 12. No names are published. Allocation is by testing the site's coordinates against the official published zone polygons, and all meter points in one unit must fall in the same zone. |
| **Basis** | The official published zone map was downloaded and parsed. Each of the 12 polygon features carries a single property, a region number. There is no name field, so a zone can only ever be quoted as a number. |
| **Anchors** | None. |
| **Corroboration** | The zone count of 12 in the procurement rules matches the 12 polygons in the map file, which confirms the two documents describe the same set of zones. |
| **Citation** | NESO, "DFS 12 Zones GeoJson Map", [document](https://www.neso.energy/document/376656/download), downloaded 2026-08-12. Rules: NESO, "Demand Flexibility Service Procurement Rules" v5.0, effective 18 May 2026, [document](https://www.neso.energy/document/379391/download). |
| **Direction of effect** | **Neutral.** No priced stream reads this key. It matters because omitting the service entirely makes a model conclude that a 0.1 to 1 MW behind the meter battery earns no system operator revenue at all, which has been false since the service expanded on 9 April 2026. |
| **Confidence ceiling** | **Low** for any figure derived from this service, because no zone is allocated and no cleared price distribution is held. |
| **Confirmation ask** | A full site address or full postcode per site, which is what allocation to a zone requires. The five campaign sites currently carry district-level postcodes only. |

### L10. Demand Flexibility Service guaranteed acceptance price

| | |
|---|---|
| **What was missing** | A price the previous release recorded as a data ask. |
| **Value now used** | The **definition only**. The guaranteed acceptance price is a threshold published administratively from time to time, and it applies to service **tests**, not to live in-merit events: a test submission priced at or below it is accepted without assessment. The **current value is refused, not guessed** (see section 6). The winter 2022/23 figure of £3,000/MWh is carried as an order of magnitude marker for the previous scheme only. |
| **Basis** | The current procurement rules define the term and deliberately externalise the number, which is why no value appears in the rules themselves. |
| **Anchors** | None. |
| **Corroboration** | The 2022/23 published test price corroborates the order of magnitude for the previous scheme. It establishes nothing about the current one. |
| **Citation** | NESO, "Demand Flexibility Service Procurement Rules" v5.0, effective 18 May 2026, paragraph 12.1 and the defined term, [document](https://www.neso.energy/document/379391/download), downloaded and parsed 2026-08-12. Historic anchor: NESO, "Demand Flexibility Service: Guaranteed Acceptance Price" (winter 2022/23 scheme), [document](https://www.neso.energy/document/268856/download). |
| **Direction of effect** | **Neutral.** This is not a revenue rate and it is not on any pricing path. |
| **Confidence ceiling** | **Low** for any service-derived figure, for the same reason as L9. |
| **Confirmation ask** | The published guaranteed acceptance price for the current delivery year, when the system operator publishes one. |

---

## 5. Site level derivations

These apply to the five modelled Grundon sites (Bishops Cleeve, Colnbrook, Tanhouse and Knowl Hill,
all materials recovery operations, and Ewelme, a vehicle workshop and depot). Harwell is excluded from
S3 because it has real metered half-hourly data.

### S1. Connection voltage level

| | |
|---|---|
| **What was missing** | No evidence of whether each site takes supply at low or high voltage. The distribution tariff depends on it. |
| **Value now used** | The low voltage site-specific half-hourly tariff. |
| **Basis** | An engineering default in the absence of evidence, at the scale these sites operate. It is an assumption, not a reading. |
| **Anchors** | None. |
| **Corroboration** | None available. The agreed capacities recorded (250, 325 and 750 kVA) do not settle the question either way. |
| **Citation** | Not applicable. This is a stated assumption, and it is recorded in each site's own input file as such. |
| **Direction of effect** | **Better, and this is the largest single site-level uncertainty.** High voltage red rates are far lower than low voltage: in the East Midlands the high voltage red rate is 2.837 p/kWh against 7.777 at low voltage. If a site is in fact on high voltage, the red-band avoidance stream is overstated by roughly a factor of 2.7. |
| **Confidence ceiling** | **Medium**, and this is a candidate binding cap on any site whose value is dominated by red-band avoidance. |
| **Confirmation ask** | The connection voltage from the connection agreement, or the tariff name on an invoice, which states it. |

### S2. Residual charging band

| | |
|---|---|
| **What was missing** | The residual charging band for each site, needed to read both the distribution fixed charges and the transmission residual (L2, L8). |
| **Value now used** | Band 4, for every site with an evidenced capacity. |
| **Basis** | Inferred from the evidenced agreed capacities (250, 325 and 750 kVA) against the published low voltage thresholds of 80, 150 and 231 kVA. All three exceed 231 kVA, so all three imply band 4. |
| **Anchors** | The agreed capacity is evidenced. The band mapping is published. What is inferred is the connection of the two, and the low voltage assumption it rests on (S1). |
| **Corroboration** | The two banded tables agree on the band structure and labels, so a single band determination reads both consistently. One capacity does not survive scrutiny as an import figure: Knowl Hill's 325 kVA against 45,732 kWh a year would imply a 1.6% load factor if fully loaded, and the site's records note that landfill gas self-supply is a separate flow, so that connection is very likely sized for **generation export** rather than import. It should not be read as an import capacity, and Knowl Hill's band therefore rests on weaker evidence than the other two. |
| **Citation** | Thresholds as at L2. Site capacities from the customer's own records, per site. |
| **Direction of effect** | **Worse.** The site stacks previously assumed band 2. Band 4 raises the fixed and unavoidable charges by about £9,700 per site per year on the transmission residual alone (L8). |
| **Confidence ceiling** | **Medium.** A billed band reflects a 24 month average capacity and any re-banding history, so the band a site's current capacity implies is not always the band it is billed under. |
| **Confirmation ask** | The band as printed on an invoice. One invoice line retires both this and L8's per-site figure. |

### S3. Half-hourly load basis: what is measured and what is modelled

This is the derivation a reader should understand before reading any number that depends on the load
shape, which is most of them.

**What was missing.** None of the five Grundon sites has metered half-hourly data. The model requires
half-hourly data for every underwritten site, so a half-hourly series was synthesised.

**What the method does, and the split that matters:**

- **The within-day shape is modelled.** Each site is assigned an engineering archetype: a
  single-shift sorting plant with an overnight standing load for the four materials recovery sites,
  and a workshop and depot pattern for Ewelme. The shape says when in the day energy is used, and it
  is an assumption.
- **Annual energy is measured.** It comes from the site's own invoice totals, not from a benchmark.
- **Month-to-month seasonality is measured** for four of the five sites, because the shape is scaled
  to **12 monthly invoice totals**. Tanhouse is the exception: only an annual total was available, so
  its seasonality is modelled rather than measured. This should be stated wherever Tanhouse figures
  appear.
- **Peak demand is modelled, and it is an output of the archetype, not an observation.** There is no
  measured maximum demand for any of the five sites, so a load factor check against the modelled peak
  tests the archetype against itself. That circularity is real and it is why the confirmation ask
  below is what it is.

**Corroboration, and its exact limit.** The materials recovery archetype's peak-to-mean ratio is
independently corroborated. Its annual load factor on a flat-month basis is 45.2%. Elexon profile
class 3 (non-domestic unrestricted), computed from the published curve on the same basis, is 45.3%,
and the implied peaks agree to within 0.1 kW at every site. The archetype's peakiness is therefore not
arbitrary: it sits on top of a real measured Great Britain non-domestic curve. **That corroborates one
scalar, the peak-to-mean ratio, and nothing else.** The weekly pattern disagrees materially, and
profile class 3 is not a sector source: its classes are cut by metering configuration and load factor,
not by business type, its population is customers below 100 kW maximum demand (which excludes four of
the five sites), its values are group averages across customers, and its data vintage is 1997.

**No sector-matched source exists, and the search that established that is recorded.** A 26 entry
search log covering web searches, data portal queries, direct downloads and document reads found **no
published intraday electricity curve for a UK materials recovery facility or waste transfer station,
and none for a UK vehicle workshop, commercial garage or transport depot.** The waste sector
literature publishes energy intensity in kWh per tonne, not a shape. The building benchmark standard
publishes annual kWh per square metre, not a shape. Depot electrification studies publish charging
load, not the baseline building load. Two real, open-licensed UK half-hourly datasets were found and
both were rejected on their own documented methodology: one is a group average across customers below
100 kW, the other is a 95th to 98th percentile upper envelope built for network loading assessment,
whose percentile construction has erased the weekday and weekend pattern entirely (its Saturday and
Sunday levels come out slightly **above** its weekdays).

**Adopting a nominally sourced substitute would have made the numbers worse.** Both candidate curves
are multi-site aggregates, and aggregation raises load factor. Substituting either would have **cut
each site's modelled peak by 22% to 48%**: profile class 7 by about 22%, the network operator's
industrial envelope by about 38%. Applied to the largest site that is a fall from about 915 kW to
roughly 596 kW or 477 kW. Since peak drives peak-shaving value, distribution capacity exposure and the
battery power rating, a "sourced" curve here would have understated value and undersized the system
while **sounding** better in the provenance field. Keeping the archetype was therefore not the choice
that flatters the numbers, and the honest position on the archetype's own error is that its direction
is **unknown**, because nothing in the site data can currently test a within-day shape.

**Citation.** Corroboration source: Elexon Ltd (2015), "Electricity user load profiles by profile
class", published by the Electricity Association and supplied by Elexon, UKERC Energy Data Centre
catalogue `5af8ae29-86a7-4e8c-9fe4-1e2d99d9fb96`, [data at CEDA](https://data.ceda.ac.uk/edc/d1/5af8ae29-86a7-4e8c-9fe4-1e2d99d9fb96),
data vintage 1 January to 31 December 1997, catalogue publication 2015, open access. Rejected
candidate: UK Power Networks (2024), "Standard Profiles UK Power Networks Uses for Electricity
Demand", [open data portal](https://ukpowernetworks.opendatasoft.com/explore/dataset/ukpn-standard-profiles-electricity-demand/),
last modified 2024-12-03, CC BY 4.0. Both were downloaded and read, not cited from a search result.
Site energy: the customer's own monthly invoice totals, per site.

**Direction of effect.** Unknown and currently untestable. The measured components (annual energy,
and monthly seasonality for four of five sites) are not affected by the shape assumption at all. The
components the shape drives, which are peak demand, the split of energy across charging bands and
therefore red-band avoidance value, carry an error of unknown sign.

**Confidence ceiling. Medium, and for these five sites it is the binding cap.** No amount of
additional literature raises it: the search has established that the literature does not contain the
answer. Only site measurement raises it.

**Confirmation ask, in cost order.** First and cheapest: **the measured monthly maximum demand or
capacity-charge kVA readings from the existing invoices.** A measured peak pins each site's true load
factor, breaks the circularity described above, and for the first time makes the within-day shape
testable rather than merely stated. Second: metered half-hourly data for any one of the sites, which
retires the assumption outright. A metered year from any comparable UK operator would also serve, and
would beat every published source found.

---

## 6. Values still refused rather than assumed

Five figures were searched for and not found. Each stays empty, with the search recorded against it.
**None of them is read as zero.** Each sits off the priced floor by construction, so an empty value
cannot silently zero a revenue line: the affected pounds are refused, and the stream is simply not
priced, which is visible in the output rather than hidden in it.

| Missing figure | What was searched, on 2026-08-12 | Why it failed | What would settle it | Effect of refusing it |
|---|---|---|---|---|
| **Aggregator revenue share** (the percentage an aggregator takes) | Flexitricity, Limejump, enspired, Modo Energy and trade press material | No Great Britain platform publishes a revenue share percentage. The only public statement located is qualitative: that a typical agreement would see a revenue share as the aggregator's payment | An executed or quoted aggregator agreement stating the revenue share and any floor or minimum fee | Balancing mechanism revenue is gated upside and is never in the priced floor. Where flexibility revenue is priced, it is priced net of a site-level agreed cut supplied by a real agreement. The library's only figure remains a 20% placeholder, used nowhere on the floor |
| **Realised performance factor K** for frequency response products | System operator data portal and documents, flexibility factsheets from the regulator's data centre, trade press | The methodology, the auction results and a performance monitoring template are published. No distribution of realised K for Great Britain batteries is published anywhere locatable. Providers hold their own 20 Hz performance data; the public record does not | A published or own-fleet distribution of realised per-settlement-period k and block K from 20 Hz performance data | Frequency response is gated upside, never in the priced floor. Where ancillary revenue is priced from a customer-supplied agreed rate, performance risk is carried qualitatively rather than as a number |
| **Current guaranteed acceptance price** for the Demand Flexibility Service | System operator documents and news pages, the v5.0 rules, the v4.0 service terms | The rules deliberately externalise the number and no value for the post-9-April-2026 zonal service was locatable | The published price for the current delivery year | Not a revenue rate and not on any pricing path. The empty value means the system operator has not published one, never £0 |
| **Minimum offer size, SP Energy Networks** local flexibility | The Piclo market guide for that operator, which defers to the overarching flexibility services agreement | No minimum bid size is published | The minimum-bid clause of the overarching flexibility services agreement | Local flexibility is conditional and is only priced from an actual tender, so an empty threshold cannot zero a priced stream |
| **Minimum offer size, Northern Powergrid** local flexibility | The operator's procurement statements; the current statement was located only at 2021-22 vintage | No current minimum offer size is published | The current invitation to tender minimum, read from the live competition when a real tender in the site's zone is priced | As above |

Both operators now tender through the Piclo platform, which supersedes an earlier inference in the
project's constants register that they procure through Flexible Power. That correction is recorded
alongside the failed search.

**Two further empty values, empty on purpose rather than for want of searching.** An availability
price and a site-specific firm-commitment availability factor are both left empty by design: an
availability price is asserted only from an awarded tender, and a firm availability promise cannot be
de-rated for outages until there is an operational or manufacturer record to de-rate it with. Neither
is on the priced floor. The published fleet availability figure of 95%, which is a separate thing, is
held and used as an annual scalar on energy and payment volumes only.

**A note on the meaning of an empty value elsewhere in the library.** Where one network operator
publishes no minimum capacity threshold for a flexibility product, the model records that as empty and
reads it as "no threshold published", which is not the same as a threshold of zero. Where two
flexibility products publish no availability grace factor, the operator records those as not
applicable, and the model carries them as empty for the same reason. And on the residual band table,
the top band has no upper limit, so its upper bound is empty because the band is unbounded.

---

## 7. Where the underlying records live

| Record | Location |
|---|---|
| Every value, with its full source string, status, owner and review date | `assumptions/gb_v1_8.yaml` |
| The mechanical proof that only the six enumerated values changed from the previous release | `tests/test_assumptions_gb_v1_8.py` |
| The load-shape sourcing report, including the 26 entry search log and the derived profile tables | [`grundon_harwell/05_load_curve_sources.md`](grundon_harwell/05_load_curve_sources.md), prepared 2026-08-12, with the search record at [`05b_load_curve_search_log.md`](grundon_harwell/05b_load_curve_search_log.md). |
| The bill reconciliation against real invoiced spend, per site and per month | [`grundon_harwell/06_bill_reconciliation.md`](grundon_harwell/06_bill_reconciliation.md). The independent check on whether the modelled tariff and load chain is defensible: the three grid sites recover 74 to 85 percent of real invoiced delivered cost, every shortfall in the conservative direction. |
| The sizing diagnosis, establishing why no site produced a specification | [`grundon_harwell/07_sizing_diagnosis.md`](grundon_harwell/07_sizing_diagnosis.md). Measured, and it refutes the earlier stated explanation. |
| The market and battery constants register that most citations were transcribed through | `docs/bess_constants_register.md` |
| The standard this log is written to | `docs/validation_requirements.md` §8 and decision D45 in `docs/decisions.md` |

Each licence area's entry in the assumption release carries the SHA-256 digest of the exact file that
was read, so any figure in section 4.1.1 can be checked against the same bytes rather than against a
document that may since have been re-issued. Charging statements are re-issued during a charging year:
the in-year revisions inspected here touched other annexes, and every statement inspected states that
no notice has been issued to change the time bands. Re-fetch every charging year regardless.

---

## 8. Open items a reader should hold against every number

1. **Nothing is confirmed.** Every entry is `needs_review` or `placeholder`, so no output on this
   release is bankable. This is deliberate, not an oversight.
2. **The site inputs must be re-pointed before a run consumes this release.** The library now holds
   each licence area's own rates, but a run only benefits once each site's tariff stack reads its own
   area rather than the retired East Midlands proxy, and its own residual band rather than the assumed
   band 2. As of 2026-08-13 the tracked site inputs have not been re-pointed: four of them still carry
   the East Midlands proxy red rate of 7.777 p/kWh and the band 2 standing charge of £12.36264 a day,
   so the unfavourable corrections in L1, L2, L8 and S2 are **pending application and are not yet
   reflected in any figure**. Any report produced before that re-point overstates red-band avoidance
   value and understates the fixed bill line.
3. **Two site inputs still carry no distribution rate at all.** Colnbrook and Tanhouse, the two
   largest sites by annual energy, hold an empty red-band rate and no standing charge. An empty rate
   on this path does not read as unknown: it prices red-band avoidance, one of only two floor
   revenue streams, at **zero**. That is the exact failure decision D45 was raised to remove, and it
   is open at the time of writing. Neither site should be reported on until it is closed, and the
   rates that close it are now in the library (L1: Southern area, 8.530 p/kWh at no residual falling
   to 5.471 at band 4).
4. **The inflation rate has no consumer yet.** Nothing escalates until the escalation machinery is
   wired, so L3 currently changes no number.
5. **Peak demand is modelled for all five Grundon sites.** The cheapest evidence that would change
   that is the measured monthly maximum demand already sitting on the customer's invoices.
