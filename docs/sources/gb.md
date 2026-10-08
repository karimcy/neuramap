# Source inventory — Great Britain
*Research agent output, verified 2026-07-22. Feeds docs/data-sources.md.*

## Top 5 must-haves (with verified direct URLs)
1. **ETYS 2025 Appendix B** — neso.energy/document/383936/download (xlsx, anonymous): circuits R/X/B + ratings per TO (NGET/SPT/SHET), transformers, reactive. THE transmission network model. Also: Appendix G nodal demand (document/379136/download), boundary chart data (383896), fault levels (383951/61/66).
2. **NGED LTDS Tabular Model** — connecteddata.nationalgrid.co.uk/dataset/ltds-tabular-model (anonymous CSV): R1,X1,B1,R0,X0 + 4 seasonal ratings per circuit. Best DNO network model. CIM XML too.
3. **UKPN HH archive** (~155M records, key-gated): primary-transformer-power-flow-historic-half-hourly-epn (45.8M) / -spn (25.5M), grid-transformer HH (10.6M), 132kV (21.5M), 33kV EPN/SPN (29M+23M), super-grid (11.2M), PLUS ukpn-data-centre-demand-profiles (5.4M!).
4. **Elexon P114/S0142** (free portal account + scripting key) — the ONLY HH metered demand per GSP Group. Insights API (data.elexon.co.uk/bmrs/api/v1, NO KEY, 318 endpoints) has national demand + per-BMU generation but NO GSP-group demand.
5. **NESO TEC Register** — direct CSV, twice-weekly, now Gate 1/Gate 2 tagged post-Connections Reform.

## Registrations needed (all free)
NGED Connected Data (unlocks HH transformer flows + Network Opportunity Map headroom) · SPEN ODS key · ENWL ODS key · Elexon Portal scripting key · ENWL website (LTDS) · [have: UKPN, NPg]. NOT needed: NESO (all anonymous), Elexon Insights, NGED LTDS+queue, SSEN CKAN (needs browser UA header only).

## NESO portal highlights (all anonymous)
Historic Demand 2001–2025 per-year CSVs · GSP GIS polygons + GSP–Gnode–DirectConnect lookup CSV · FES building blocks + regional FES per GSP · tRESP demand/generation pathways per GSP (Jan 2026) · 24-months-ahead constraint limits per boundary + day-ahead flows vs limits · thermal constraint costs · ETYS boundary GIS.

## Per-DNO snapshot
- **NGED** (CKAN, 91 ds): LTDS anon; HH flows per region ×{primary,BSP,SGT} (~375 site CSVs/region, 2yr, login); network-opportunity-map-headroom (login); connection-queue 45 per-GSP CSVs ANON + Clearview + reform outcomes; ECR anon; smart-meter LV (3,871 files).
- **UKPN** (ODS, 136 ds): HH archive above; LTDS tables 1–8 + CIM; capacity-heatmap; dfes-network-headroom (177k = our current source); queue insights; ECR 1+2; large-demand-list (496!); flexibility dispatches/tenders; GIS sites.
- **NPg** (ODS, 99 ds): live rolling windows ONLY (14d primary/30d BSP/33-66-132kV circuits) — no multi-year HH; site-utilisation 28k + historical peaks; LTDS App 3–11 (R/X/B on 100MVA); NDP demand/gen headroom (our source); connection-queue-information; NATIONAL combined ECR (ecr_manual_combine_test, 20,075 — all-DNO in one!); DFES 2026 draft.
- **SPEN** (ODS, 150 ds): network-flow-dataset (17M) + historic_substation_demand_curve (405k); SPD/SPM LTDS + CIM EQ profiles; NSHR workbooks (our source); gsp-queue-position "Single Digital View"; ECR; DFES per substation.
- **ENWL** (ODS, 146 ds): weakest HH (live + LV only); LTDS on website only (registration); GSP/BSP/primary heatmaps + ndp-pry-bsp-headroom (38.9k); GSP connection queue; ECR.
- **SSEN** (CKAN behind Cloudflare — browser UA): smart_meter_prod_lv_feeder (84k LV feeders / 36k substations HH, daily, bulk via Datopian); SEPD/SHEPD LTDS XLSX; GSP Technical Limits CSV; headroom dashboard CSV; ECR monthly; NeRDA near-real-time.

## Cross-DNO / other
- **Weave (weave.energy)**: harmonized cross-DNO LV-feeder HH smart-meter GeoParquet on S3 (s3://weave.energy/smart-meter.parquet), free, no key, Feb 2024→. Saves 4-portal ETL for LV.
- ENA Open Networks flexibility figures (annual workbooks); Piclo Flex + Flexible Power (flexibility marketplaces); UKERC (P114 archive backup); carbon intensity API.

## Verification notes
- ODS anonymous: records → 403; exports/csv → 200 but header-only. Keys instant+free.
- LTDS machine-readable for 5/6 groups (ENWL = website reg). CIM exports: UKPN/NPg/SPEN/NGED — common ingestion path.
- HH history ranking: UKPN >> SPEN > NGED (2yr, login) > NPg (rolling only) > SSEN/ENWL (LV only). For NPg/SSEN/ENWL primaries: LTDS peaks + smart-meter agg + Elexon GSP scaling.
- Queues: NESO registers (Gate-tagged) + NGED/SPEN/NPg/ENWL/UKPN queue datasets; SSEN thinnest.
