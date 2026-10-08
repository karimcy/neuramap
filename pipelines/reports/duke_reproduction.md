# Duke "Rethinking Load Growth" reproduced with the European engine

Generated 2026-10-08T14:37Z. Input: EIA-930 hourly demand, 22 balancing authorities, 2016–2024 (Adjusted demand where present). Engine: `pipelines/eu_load_growth.py` (two seasonal thresholds, App. C rate averaged over years, goal-seek, cleaning per App. B including the erroneous-peak rule).

## 22-BA totals: same engine vs published

| Limit | GW (ours → Duke) | hours/yr | ≥50 % retained | ≥75 % | ≥90 % | mean event h |
|---|---|---|---|---|---|---|
| 0.25% | 77.4 → 76 | 87 → 85 | 85 → 88 % | 59 → 60 % | 28 → 29 % | 4.2 → 1.7 |
| 0.5% | 99.5 → 98 | 183 → 177 | 87 → 88 % | 60 → 60 % | 29 → 29 % | 4.7 → 2.1 |
| 1% | 126.8 → 126 | 378 → 366 | 88 → 88 % | 61 → 60 % | 30 → 29 % | 5.4 → 2.5 |
| 1.5% | 145.3 → – | 575 → – | 89 → 88 % | 62 → 60 % | 30 → 29 % | 6.0 → – |
| 2% | 159.8 → – | 768 → – | 89 → 88 % | 62 → 60 % | 30 → 29 % | 6.5 → – |
| 3% | 182.3 → – | 1153 → – | 89 → 88 % | 61 → 60 % | 30 → 29 % | 7.3 → – |
| 5% | 214.9 → 215 | 1903 → 1848 | 89 → 88 % | 61 → 60 % | 29 → 29 % | 9.0 → 4.5 |

GW, hours and retention reproduce within 2–3 %. Mean event duration does not: on the same data our run-length definition gives about 2.2× the published figure at every limit, and none of the alternatives tested (median run, runs at ≥10 % or ≥25 % depth, energy-equivalent hours per event) matches 1.7 / 2.1 / 2.5 / 4.5 h. The report's duration definition is therefore not recoverable from its text; European and US durations must be compared on the same engine, not against the published figure.

## Per balancing authority at 0.5 %

| BA | Name | Peak GW | S / W threshold GW | ours GW | strict-core GW | Duke Fig. 8 | diff | h/yr | ≥50 % | event h | peak errors fixed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| PJM | PJM | 153.1 | 153.1 / 139.0 | 17.82 | 17.82 | 17.8 | +0 % | 148.7 | 81.2 | 5.51 | 5 |
| MISO | MISO | 120.8 | 120.8 / 104.3 | 14.80 | 14.80 | 14.8 | +0 % | 174.0 | 86.8 | 5.26 | 0 |
| ERCO | ERCOT | 85.5 | 85.5 / 78.2 | 9.98 | 9.98 | 10.0 | -0 % | 116.3 | 69.2 | 6.35 | 0 |
| SWPP | SPP | 56.0 | 56.0 / 51.0 | 9.66 | 9.66 | 9.7 | -0 % | 183.4 | 89.1 | 5.73 | 0 |
| SOCO | Southern Co | 48.1 | 48.1 / 47.4 | 7.70 | 7.70 | 7.7 | -0 % | 207.6 | 92.3 | 4.62 | 0 |
| CISO | CAISO | 51.1 | 51.1 / 32.7 | 4.99 | 2.96 | 5.0 | -0 % | 222.3 | 93.9 | 2.94 | 0 |
| TVA | TVA | 34.4 | 31.8 / 34.4 | 4.51 | 4.51 | 4.5 | +0 % | 158.6 | 84.0 | 5.47 | 1 |
| FPL | FPL | 31.1 | 31.1 / 24.2 | 5.99 | 4.65 | 4.2 | +43 % | 237.8 | 95.8 | 5.1 | 8 |
| NYIS | NYISO | 32.1 | 32.1 / 25.1 | 3.96 | 3.96 | 4.0 | -1 % | 191.1 | 88.4 | 4.49 | 0 |
| ISNE | ISO-NE | 25.8 | 25.8 / 20.7 | 3.54 | 3.54 | 3.5 | +1 % | 188.2 | 89.0 | 3.91 | 0 |
| DUK | Duke Energy Carolinas | 21.6 | 21.5 / 21.6 | 2.82 | 2.82 | 2.8 | +1 % | 156.8 | 83.8 | 4.51 | 0 |
| FPC | Duke Energy Florida | 12.6 | 12.6 / 12.4 | 2.06 | 2.06 | 2.1 | -2 % | 196.8 | 91.6 | 4.05 | 6 |
| BPAT | BPA | 11.5 | 9.4 / 11.5 | 1.90 | 1.90 | 1.9 | +0 % | 204.2 | 90.7 | 4.89 | 5 |
| AZPS | APS | 8.3 | 8.3 / 6.9 | 1.62 | 1.62 | 1.6 | +1 % | 160.3 | 84.1 | 4.99 | 7 |
| PACE | PacifiCorp East | 9.6 | 9.6 / 8.0 | 1.45 | 1.45 | 1.5 | -4 % | 226.1 | 92.1 | 4.08 | 16 |
| SRP | SRP | 8.4 | 8.4 / 4.7 | 1.11 | 1.11 | 1.5 | -26 % | 186.2 | 88.0 | 3.78 | 2 |
| CPLE | Duke Energy Progress | 14.4 | 12.5 / 14.4 | 1.30 | 1.30 | 1.3 | -0 % | 139.2 | 78.2 | 4.89 | 0 |
| PSCO | Xcel Colorado | 9.9 | 9.9 / 7.5 | 1.16 | 1.16 | 1.2 | -4 % | 185.9 | 89.1 | 3.87 | 0 |
| PACW | PacifiCorp West | 4.7 | 4.1 / 4.7 | 0.99 | 0.99 | 1.0 | -1 % | 195.1 | 91.6 | 5.45 | 8 |
| PGE | Portland GE | 4.5 | 4.5 / 4.2 | 0.92 | 0.92 | 0.9 | +3 % | 224.0 | 91.9 | 4.52 | 2 |
| SC | Santee Cooper | 5.3 | 4.9 / 5.3 | 0.66 | 0.66 | 0.7 | -6 % | 160.9 | 84.5 | 5.03 | 0 |
| SCEG | Dominion SC | 4.9 | 4.9 / 4.8 | 0.57 | 0.57 | 0.6 | -4 % | 156.2 | 82.7 | 4.39 | 0 |

Notes: the erroneous-peak rule (one- or two-hour excursions in the top 2 % of the series more than 10 % above both bounding hours, replaced by interpolation) is what brings PJM from 47.6 to 17.8 GW: the raw feed carries 192 GW on 28 Jul 2020 and 176 GW on 29 Jul 2020 against a true peak near 153 GW, which the report corrected by hand ("erroneous peaks ... explicitly corrected", App. B). CAISO reproduces only with the full-window threshold (summer peak on 6 Sep 2022, outside Jun–Aug), consistent with the report's footnote 20; the strict core-month threshold gives 2.96 GW against the published 5.0. FPL (+43 %) retains a suspicious 31.1 GW at 21:00 on 30 Jul 2021 that the rule does not catch; SRP (−26 %) is a 1.5 GW system where the rule removes two July 2023 heat-wave hours that may be genuine.
