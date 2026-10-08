# NESO registers — ingest summary

_Generated 2026-07-22T23:11:29.488233Z from anonymous NESO CKAN (api.neso.energy). See PROVENANCE.md for URLs/licences._

## 1. TEC Register

- Source: **Transmission Entry Capacity (TEC) register** — TEC Register
- Licence: NESO Open Data Licence
- Resource last modified: 2026-07-21T15:03:17.394122
- **Projects: 2,215**  ·  **Total TEC (MW Increase/Decrease): 609,627.8 MW**

### Projects by Connections-Reform Gate

| Gate | Projects |
|---|---|
| (untagged) | 1,429 |
| 1 | 725 |
| 2 | 61 |

### TEC (MW) by Gate

| Gate | MW |
|---|---|
| (untagged) | 330,797 |
| 1 | 269,819 |
| 2 | 9,012 |

### Projects by Project Status

| Project Status | Projects |
|---|---|
| Scoping | 1,503 |
| Built | 372 |
| Consents Approved | 171 |
| Awaiting Consents | 150 |
| Under Construction/Commissioning | 19 |

### Projects by Connections-Reform Stage

| Connections-Reform Stage | Projects |
|---|---|
| (untagged) | 1,940 |
| 1 | 130 |
| 2 | 124 |
| 3 | 16 |
| 4 | 4 |
| 5 | 1 |

### Projects by Host TO

| Host TO | Projects |
|---|---|
| NGET | 1,290 |
| SPT | 465 |
| SHET | 445 |
| OFTO | 15 |

### Projects by Agreement Type

| Agreement Type | Projects |
|---|---|
| Direct Connection | 1,763 |
| Embedded | 452 |

### TEC (MW) by technology (Plant Type split on ';')

| Technology | MW | (share of total) |
|---|---|---|
| Energy Storage System | 286,329 | 47.0% |
| PV Array (Photo Voltaic/solar) | 99,854 | 16.4% |
| Wind Offshore | 91,339 | 15.0% |
| Wind Onshore | 37,620 | 6.2% |
| CCGT (Combined Cycle Gas Turbine) | 33,670 | 5.5% |
| Demand | 21,730 | 3.6% |
| Nuclear | 11,390 | 1.9% |
| Pump Storage | 10,079 | 1.7% |
| Reactive Compensation | 5,484 | 0.9% |
| Interconnector | 4,500 | 0.7% |
| OCGT (Open Cycle Gas Turbine) | 2,873 | 0.5% |
| Biomass | 2,163 | 0.4% |
| Tidal | 534 | 0.1% |
| Thermal | 438 | 0.1% |
| Gas Reciprocating | 318 | 0.1% |
| Hydrogen | 297 | 0.0% |
| CHP (Combined Heat and Power) | 267 | 0.0% |
| LAES (Liquid Air Energy Storage) | 250 | 0.0% |
| Hydro | 225 | 0.0% |
| Waste | 173 | 0.0% |
| Coal | 50 | 0.0% |
| Substation | 48 | 0.0% |
| Oil & AGT (Advanced Gas Turbine) | 0 | 0.0% |

<details><summary>TEC (MW) by raw Plant Type combo (top 20)</summary>


| Plant Type combo | MW |
|---|---|
| Energy Storage System | 179,162 |
| Energy Storage System;PV Array (Photo Voltaic/solar) | 138,165 |
| Wind Offshore | 86,750 |
| CCGT (Combined Cycle Gas Turbine) | 28,697 |
| Demand;Energy Storage System;PV Array (Photo Voltaic/solar) | 21,654 |
| Wind Onshore | 21,095 |
| Energy Storage System;PV Array (Photo Voltaic/solar);Wind Onshore | 14,047 |
| Energy Storage System;Wind Onshore | 11,750 |
| Demand;Energy Storage System;PV Array (Photo Voltaic/solar);Reactive Compensation | 11,067 |
| Energy Storage System;Nuclear;PV Array (Photo Voltaic/solar);Wind Onshore | 11,000 |
| Pump Storage | 9,651 |
| Nuclear | 7,620 |
| PV Array (Photo Voltaic/solar) | 6,939 |
| Demand;Energy Storage System | 5,889 |
| Demand;PV Array (Photo Voltaic/solar) | 5,020 |
| CCGT (Combined Cycle Gas Turbine);Energy Storage System | 4,278 |
| CCGT (Combined Cycle Gas Turbine);Energy Storage System;OCGT (Open Cycle Gas Turbine) | 4,250 |
| Biomass;Demand | 3,906 |
| Interconnector | 3,600 |
| Energy Storage System;Wind Offshore | 3,200 |

</details>

## 2. Historic Demand Data — coverage check

- Source: **Historic Demand Data** — Historic Demand Data 2026 (latest year = **2026**)
- Licence: NESO Open Data Licence
- Resource last modified: 2026-07-22T08:20:15.345921
- Rows (half-hourly settlement periods): **8,708**
- Date range: **2026-01-01 → 2026-07-01** (182 distinct days over a 182-day span)
- Periods-per-day distribution: `{48: 179, 46: 1, 45: 1, 25: 1}` (48 = normal; 46 = spring clock-change day; 50 = autumn — all valid)
- Calendar gaps (missing days): **0**
- Days with an unexpected period count (not 46/48/50): **2** → {'2026-05-29': 45, '2026-05-30': 25}
- National Demand (ND, MW): min 12,626 · mean 26,660 · max 47,382
- **Coverage verdict: GAPS DETECTED (see above)** for the 2026 file — note this is the current, year-to-date file.
