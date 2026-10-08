# A European "Rethinking Load Growth" — review and execution plan

Written 8 October 2026. Goal: get OpportunityMap to the point where it can state, for Europe, what Norris et al. (Duke Nicholas Institute, Feb 2025) stated for the US: how much new load the existing system can absorb if that load accepts a small amount of curtailment, and show it interactively per bidding zone. Where data is short, concentrate on geographies where it is obtainable.

## 1. The method: Duke's, replicated exactly (decision 8 Oct 2026)

Decision: model Duke's methodology exactly; where a judgement call exists, take the optimistic side. The report (Norris, Profeta, Patino-Echeverri, Cowie-Haskell, Nicholas Institute, Feb 2025; PDF retrieved from the Internet Archive on 8 Oct 2026) defines the method in its "Analysis of Curtailment-Enabled Headroom" section and Appendices B–C. `pipelines/eu_load_growth.py` implements it step by step:

| Step | Duke (report text) | Implementation |
|---|---|---|
| Data | Hourly load, EIA-930, nine years 2016–2024, 22 balancing authorities (744 of 777 GW US summer peak); 2015 excluded as incomplete | Hourly means of ENTSO-E-mirror (energy-charts) and NESO data; nine most recent full years (2017–2025); a year with fewer than 8,000 hours is excluded, as Duke excluded 2015 |
| Cleaning (App. B) | Missing/zero values interpolated; spikes and low outliers replaced from neighbours | Same: linear interpolation of gaps, spike (>1.5× both neighbours) and drop (<0.5×) correction, counts logged per year |
| Thresholds | Two per system: maximum summer peak (Jun–Aug) and maximum winter peak (Dec–Feb) over all years; summer threshold applied Apr–Oct, winter threshold Nov–Mar. Footnote 20: in four cases (AZPS winter, FPL winter, CAISO summer and winter) the seasonal peak fell "within one month" of the core window and that peak was used | Same two thresholds and the same month mapping. **DUKE-ADAPT**: winter-peaking European systems have April and October loads well above the June–August maximum, so with the strict core-month threshold the existing load already breaches the summer threshold and the goal-seek returns 0 GW (GB and Germany at 0.25–0.5 %). We take each threshold as the maximum over all the months it governs (Apr–Oct, Nov–Mar). That is identical to the report wherever the seasonal peak falls in the core months, goes one month further than the report's own exception otherwise, and is never below the report's threshold (the optimistic side). The strict core-month variant is stored for every market so the difference is visible. |
| Curtailment (App. C) | `Curtailment_t(L) = max(0, Demand_t + L − Threshold_t)`; annual curtailed MWh ÷ (L × 8,760); rate for L = average of the annual rates over N years | Same formula, same averaging |
| Goal-seek | `scipy.optimize.root_scalar` for L at 0.25 / 0.5 / 1 / 5 % | Bisection to 1e-15 relative, same root |
| Statistics | Average hours/yr with any curtailment; hours retaining ≥50 / 75 / 90 % of the new load; mean event duration (consecutive hours, any magnitude); winter/summer share of curtailed energy; aggregate and seasonal load factors; curtailment rate vs load addition in 0.25 %-of-peak steps (Fig. 9, App. A) | All computed and stored per market and per limit |
| Caveats kept | No transmission constraints; no reserve margin; observed peak stands in for what planners provision | Same; stated in the panel |
| Optimistic sensitivities (not Duke) | — | Off by default, labelled: "all-time peak" single threshold; capability margin ×1.03 / ×1.05 |
| Beyond Duke | — | Battery-firmed load (chronological state-of-charge simulation with uptime thresholds), reported separately as the Neura contribution |

## 2. Results, nine years (2017–2025), ten markets, Duke thresholds

Run 8 Oct 2026 (`pipelines/eu_load_growth.py --years 2017-2025`; data: ENTSO-E actual load via energy-charts and the OPSD bulk file, NESO for GB). Summed peak 426 GW.

| Limit | 0.25 % | 0.5 % | 1 % | 1.5 % | 2 % | 3 % | 5 % |
|---|---|---|---|---|---|---|---|
| New flexible load, GW | 40.1 | 49.7 | 60.6 | 67.8 | 73.4 | 82.1 | 95.2 |
| Share of summed peak | 9.4 % | 11.7 % | 14.2 % | 15.9 % | 17.2 % | 19.3 % | 22.3 % |
| Hours/yr with any curtailment (avg) | 94 | 196 | 406 | 610 | 814 | 1,207 | 1,935 |
| Curtailed hours keeping ≥50 % | 88 % | 90 % | 91 % | 91 % | 91 % | 91 % | 91 % |
| Duke, US (744 GW): GW / hours | 76 / 85 | 98 / 177 | 126 / 366 | – | – | – | 215 / 1,848 |
| All-time-peak sensitivity (not Duke), GW | 50.8 | 61.8 | 75.0 | 84.0 | 91.2 | 102.6 | 120.0 |

| Market | Peak GW | Thresholds S / W (set) | 0.5 % | 1 % | 1.5 % | 2 % | h/yr at 1 % | ≥50 % | 1 % per year, min–max | Strict core at 1 % |
|---|---|---|---|---|---|---|---|---|---|---|
| France | 94.5 | 71.9 (Oct 2018) / 94.5 (Feb 2018) | 13.9 | 16.9 | 18.7 | 19.9 | 426 | 92 % | 13.0–22.3 | 0.0 |
| Germany (DE-LU) | 81.3 | 76.8 (Apr 2021) / 81.3 (Nov 2021) | 8.2 | 9.5 | 10.4 | 11.1 | 489 | 96 % | 7.1–14.5 | 5.8 |
| Great Britain | 49.8 | 45.8 (Oct 2018) / 49.8 (Jan 2017) | 6.9 | 8.7 | 9.8 | 10.8 | 365 | 89 % | 4.9–12.6 | 0.0 |
| Italy | 53.8 | 53.8 (Jul 2019) / 50.3 (Jan 2017) | 5.1 | 6.4 | 7.3 | 8.1 | 353 | 89 % | | 6.4 |
| Spain | 41.8 | 39.8 (Aug 2018) / 41.8 (Jan 2021) | 4.2 | 5.3 | 6.1 | 6.7 | 350 | 87 % | | 5.3 |
| Sweden | 26.6 | 21.3 (Apr 2018) / 26.6 (Jan 2017) | 3.5 | 4.2 | 4.7 | 5.1 | 403 | 91 % | | 0.0 |
| Norway | 25.2 | 20.7 (Oct 2019) / 25.2 (Feb 2021) | 2.8 | 3.6 | 4.1 | 4.4 | 363 | 88 % | | 0.0 |
| Poland | 28.3 | 24.9 (Apr 2022) / 28.3 (Jan 2024) | 2.1 | 2.6 | 2.9 | 3.1 | 410 | 93 % | | 1.9 |
| Finland | 15.0 | 11.9 (Oct 2018) / 15.0 (Jan 2024) | 1.5 | 1.9 | 2.1 | 2.3 | 394 | 91 % | | 0.0 |
| Portugal | 9.8 | 8.2 (Apr 2024) / 9.8 (Jan 2021) | 1.3 | 1.5 | 1.7 | 1.8 | 505 | 95 % | | 1.2 |

Findings:

- **Europe's ten largest markets can absorb about 61 GW of new load at 1 % curtailment, 14 % of their summed peak, against Duke's 17 % for the US.** Hours and retention are in the same range as the US (406 h vs 366; 91 % vs 88 %).
- **The nine-year window is the optimistic lever Duke's method hands us.** US load grew over 2016–2024, so Duke's thresholds came from recent years. European load fell: France's winter threshold is February 2018, GB's is January 2017, and the method measures headroom against those. The per-year column shows the consequence: GB's 1 % figure runs from 4.9 GW (2017) to 12.6 GW (2023). The panel shows both the pooled figure and every year.
- **Strict core-month thresholds give 0 GW for France, GB, Sweden, Norway and Finland**, which is why the full-window reading is required (§1).
- **Curtailment falls in the shoulder months.** France at 1 %: 33 % of curtailed energy in April and 15 % in October, against the 71.9 GW "summer" threshold; GB: 40 % in January, 18 % each in February and December, 10 % in November.
- **Cleaning touched five hours in nine years across the ten markets**: four Norwegian DST-change hours at midnight on the last Sunday of October (26–30 GW against 14–16 GW neighbours, a double-counted hour in the feed that would otherwise have set Norway's summer threshold above its real all-time peak) and one Swedish hour in February 2021. Nothing in GB, DE or FR.
- **Battery-firmed (beyond Duke)**: an 8 h battery at 99.9 % uptime makes about 86 GW effectively firm across the ten markets; 2 h gives 60 GW; 4 h gives 71 GW.


## 2b. Update 8 Oct 2026 evening: 18 markets; bidding zones withdrawn

Austria, Belgium, Croatia, Czechia, Denmark, Ireland, the Netherlands and Switzerland were added (OPSD bulk file for 2017–2020, energy-charts country series after). Totals across 18 markets, summed peak 508.2 GW: 47 / 59 / 71 / 86 / 112 GW at 0.25 / 0.5 / 1 / 2 / 5 %. An 8 h battery at 99.9 % uptime firms 99 GW.

**Bidding zones are withdrawn.** The energy-charts `bzn=` parameter returned Germany's load for every Nordic, Danish and Italian zone from Q4 2020 on (identical 81.3 GW peak for NO1, SE1, IT-North…). The earlier "SE1–4 confirmed" was based on a matching sample count, which was not a valid check. Those 357 cache files were deleted. Zones need the ENTSO-E Transparency API (token) or a per-zone source; OPSD covers them only to Sep 2020.

| Market | Peak GW | 0.5 % | 1 % | 2 % | 1 % as share of peak |
|---|---|---|---|---|---|
| France | 94.49 | 13.9 | 16.9 | 19.9 | 17.8 % |
| Germany (DE-LU) | 81.32 | 8.2 | 9.5 | 11.1 | 11.7 % |
| Great Britain | 49.75 | 6.9 | 8.7 | 10.8 | 17.4 % |
| Italy | 53.82 | 5.1 | 6.4 | 8.1 | 11.9 % |
| Spain | 41.75 | 4.2 | 5.3 | 6.7 | 12.8 % |
| Sweden | 26.62 | 3.5 | 4.2 | 5.1 | 15.8 % |
| Norway | 25.23 | 2.8 | 3.6 | 4.4 | 14.2 % |
| Netherlands | 19.56 | 2.3 | 2.8 | 3.4 | 14.3 % |
| Poland | 28.3 | 2.1 | 2.6 | 3.1 | 9.2 % |
| Finland | 14.99 | 1.5 | 1.9 | 2.3 | 12.5 % |
| Switzerland | 11.13 | 1.3 | 1.7 | 2.1 | 15.0 % |
| Portugal | 9.83 | 1.3 | 1.5 | 1.8 | 15.7 % |
| Belgium | 13.62 | 1.2 | 1.5 | 1.8 | 10.8 % |
| Czechia | 11.28 | 1.0 | 1.2 | 1.5 | 11.0 % |
| Denmark | 6.39 | 0.9 | 1.1 | 1.3 | 16.6 % |
| Austria | 10.8 | 0.8 | 1.0 | 1.3 | 9.6 % |
| Ireland | 5.95 | 0.9 | 1.0 | 1.2 | 17.3 % |
| Croatia | 3.34 | 0.4 | 0.5 | 0.6 | 15.3 % |

## 2a. Reproduction on Duke's own input (EIA-930, 22 balancing authorities, 2016–2024)

`pipelines/duke_reproduction.py` downloads the open EIA-930 six-month files, filters the 22 balancing authorities and runs the European engine unchanged. Report: `pipelines/reports/duke_reproduction.md`.

| Limit | GW, ours → published | Hours/yr | ≥50 % retained | ≥75 % | ≥90 % | Mean event h |
|---|---|---|---|---|---|---|
| 0.25 % | 77.4 → 76 | 87 → 85 | 85 → 88 % | 59 → 60 % | 28 → 29 % | 4.2 → 1.7 |
| 0.5 % | 99.5 → 98 | 183 → 177 | 87 → 88 % | 60 → 60 % | 29 → 29 % | 4.7 → 2.1 |
| 1 % | 126.8 → 126 | 378 → 366 | 88 → 88 % | 61 → 60 % | 30 → 29 % | 5.4 → 2.5 |
| 5 % | 214.9 → 215 | 1,903 → 1,848 | 89 → 88 % | 61 → 60 % | 29 → 29 % | 9.0 → 4.5 |

Per system at 0.5 %: PJM 17.82 vs 17.8, MISO 14.80 vs 14.8, ERCOT 9.98 vs 10.0, SPP 9.66 vs 9.7, Southern 7.70 vs 7.7, CAISO 4.99 vs 5.0, TVA 4.51 vs 4.5, NYISO 3.97 vs 4.0, ISO-NE 3.54 vs 3.5, and so on; 19 of 22 within 5 %. Three things this settles:

1. **The engine is Duke's.** Totals, hours and all three retention tiers reproduce within 2–3 %.
2. **The cleaning must include the report's erroneous-peak correction.** The raw PJM feed carries 192 GW on 28 July 2020 against a true peak near 153 GW; with only gap-filling and 1.5× spike removal PJM came out at 47.6 GW instead of 17.8 and the US total at 132 GW instead of 98. A generic rule (one- or two-hour excursions in the top 2 % of the series more than 10 % above both bounding hours, interpolated away) restores PJM to 17.82 and is now applied to the European data too; every hour it touches is logged per market-year.
3. **The threshold adaptation is validated.** For every summer-peaking US system the full-window threshold equals the core-month one (the adaptation is a no-op). For CAISO, the report's footnote-20 case, only the full-window threshold reproduces the published 5.0 GW (strict core gives 2.96).

What does not replicate: mean event duration, at roughly 2.2× the published figure at every limit. Median run, runs at ≥10 % or ≥25 % depth, and energy-equivalent hours per event were all tested and none matches. The report's definition cannot be recovered from its text, so the panel shows the US on this engine next to the published US figure and compares European durations to the former only.

## 3. Data: which geographies can carry which tier

Tier 1 is the Duke analysis itself (system or bidding-zone hourly load, no keys). Tier 2 is the same analysis on measured sub-zonal load (the nodal version Duke could not do). Tier 3 is network-aware (remaining margins or power flow). Coverage confirmed on 8 Oct 2026 unless marked.

| Geography | Tier 1 source (hourly load, open) | Zones | Tier 2 (sub-zonal measured, open) | Tier 3 | Status |
|---|---|---|---|---|---|
| GB | NESO historic demand (ND, half-hourly, 2009→), no key | National | DNO primary-substation half-hourly: UKPN, NPg, NGED, SPEN, ENWL (free portal keys) | ETYS App. B model, NESO boundary data | Tier 1 cached 2025; 2021–24 backfilling; Tier 2 blocked on keys |
| DE | energy-charts (ENTSO-E mirror), 15-min | DE-LU | none open at substation level (MaStR gives assets, not load) | JAO Core RAM, hourly, open | Tier 1 backfilling |
| FR | energy-charts, hourly | FR | RTE eCO2mix regional hourly load (open; 12 regions) | JAO Core RAM | Tier 1 backfilling; Tier 2 to add |
| ES | energy-charts, 15-min | ES | none open hourly at node; REE demand capacity maps are static snapshots (archiver running) | none | Tier 1 only |
| PT | energy-charts, hourly | PT | E-Redes per-substation 15-min load and headroom, open API (pilot shipped: `data/pt_profiles.json`) | none | Tier 1 + Tier 2 (fastest nodal pilot) |
| IT | energy-charts, hourly | IT zones via `bzn` (to confirm) | Terna zonal only | none | Tier 1 |
| PL | energy-charts, 15-min | PL | none | JAO Core RAM | Tier 1 |
| SE | energy-charts, hourly (country) | SE1–4 need ENTSO-E (see note) | eSett per metering-grid-area hourly, open | JAO Nordic RAM | Tier 1 zonal + Tier 2 |
| NO | energy-charts, hourly (country) | NO1–5 need ENTSO-E | Elhub municipal hourly, open | JAO Nordic RAM | Tier 1 zonal + Tier 2 |
| FI | energy-charts, 15-min | FI | eSett, open | JAO Nordic RAM | Tier 1 + Tier 2 |
| DK | energy-charts, hourly (confirmed) | DK1–2 (to confirm) | Energinet open data | Nordic RAM | add to Tier 1 |
| AT, HR, IE | energy-charts Load confirmed | national | — | — | add to Tier 1 |
| NL, BE, CH, CZ and others | energy-charts probe inconclusive (rate-limited); ENTSO-E Transparency token (free, ~3 days) covers all | per ENTSO-E | NL: Liander/Stedin open data (to check) | — | add once token or probe confirms |

Decision rule: run Tier 1 everywhere a free hourly series exists (target 14–18 zones plus GB), run Tier 2 only where it is open today (PT, SE, NO, FI, FR regions) or where keys are instant (GB DNOs), and present Tier 3 as the existing POWERFLOW_PLAN, not as part of this deliverable.

## 4. The interactive deliverable

Extend the existing sidebar pattern (the uptime slider already drives the KPI tiles) rather than rebuilding:

1. **Controls, one row**: curtailment limit (0.25 / 0.5 / 1 / 1.5 / 2 / 3 / 5 %, default 1 %); ceiling (Duke seasonal thresholds, default / all-time peak as an optimistic sensitivity); capability margin (×1.00 default, ×1.03, ×1.05 as sensitivities); the existing uptime slider drives the battery tiles.
2. **Headline tiles**: Europe total GW of new flexible load at the chosen tolerance; the battery-firmed total at the chosen duration and uptime; hours curtailed per year; share of curtailment hours retaining ≥50 % of the load.
3. **Choropleth by bidding zone**: GW unlocked (and % of zone peak) at the chosen settings, drawn as zone polygons under the node layer, with a per-zone popup.
4. **Per-zone chart**: load-duration curve with both thresholds and the new-load band shaded; curtailment-rate-vs-load curve (Duke Fig. 9); curtailed energy by month; event-duration histogram; year-by-year figures (2017–2025) so the reader sees how much the answer depends on the year. **Built and deployed 8 Oct 2026** (items 1, 2, 4, 6); the choropleth (3) and CSV export (5) remain.
5. **Table and export**: per-zone rows (peak, load factor, GW at 0.25 / 0.5 / 1 %, hours, events, battery-firmed GW), CSV download.
6. **Methodology panel**: the audit table from §1 in plain words, the Duke comparison, and the caveats (no transmission constraints, no reserve margin, observed peak as capability).

The nodal layer stays separate and non-additive, as today. Where Tier 2 exists, the popup for a measured substation shows its own Duke-style result.

## 5. Work packages

| WP | What | Needs | Gate |
|---|---|---|---|
| 1 | Backfill 2017–2024 for the 10 markets (energy-charts, NESO) so the window is nine years like Duke's; running since 8 Oct 2026 with 12–15 s request spacing | nothing | every market-year has ≥8,000 hours; peaks per year logged |
| 2 | Pipeline v2 `eu_load_growth.py`: Duke method exactly (two seasonal thresholds, App. C rate, year-by-year averaging, goal-seek, all statistics), optimistic sensitivities off by default, per-zone JSON for the UI — **done 8 Oct 2026** | WP1 | hour and retention statistics within 15 % of Duke's; strict-core variant stored |
| 3 | Bidding zones: SE1–4 (confirmed), NO1–5, IT zones, DK1–2 via `bzn`; AT, HR, IE, DK as new markets | probe result | zone sums reconcile to country series within 2 % |
| 4 | UI panel (§4) | WP2 | renders from JSON; controls change all tiles, choropleth and charts together |
| 5 | Tier 2 sub-zonal: FR eCO2mix regions; SE/NO/FI eSett and Elhub; PT already done | nothing | measured sub-zonal headroom vs zonal result published per zone |
| 6 | GB DNO primaries (UKPN, NPg first) | portal keys (Karim, instant) | per-node Duke result for ~4,000 GB nodes |
| 7 | ENTSO-E token to fill NL, BE, CH, CZ and cross-check energy-charts | token (Karim, ~3 days) | all zones on one source |
| 8 | Methodology write-up and the headline sentence for the deck | WP2–4 | reviewed against the Duke PDF (`NeuraMaterials/refs/duke_rethinking_load_growth_feb2025.pdf`) |
| 9 | Reproduction of Duke on EIA-930 with the same engine — **done 8 Oct 2026** (§2a) | nothing | totals, hours, retention within 3 % |

## 6. Risks and limits to state up front

- The energy-charts API rate-limits (429 at 7 s spacing). Backfills must be slow; a token for ENTSO-E removes the dependency.
- GB is absent from ENTSO-E; NESO ND excludes station load, pumping and exports, so GB headroom is slightly understated relative to transmission-level demand.
- 2022 demand was suppressed by the energy crisis; multi-year maxima lean on 2021 and 2025 peaks. Report the per-year result, not only the pooled one.
- The observed peak is a floor for capability, not a measure of it: reserve margins, interconnector support and seasonal plant deratings all move it. The capability-margin control is a sensitivity, never a claim.
- System-level headroom is not deliverable at any particular node. The map's nodal layer and Tiers 2–3 are what turn the Duke number into a siting answer.
