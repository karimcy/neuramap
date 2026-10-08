# Source inventory — FR / ES / PT / IT
*Research agent output, verified 2026-07-22. Feeds docs/data-sources.md.*

## Top 5
1. **E-Redes PT (e-redes.opendatasoft.com)** — best DSO portal of the four: per-substation reception capacity w/ saturation justifications naming constraining REN lines, per-substation **15-min load diagrams** (2.8M rows per district group), per-substation short-circuit power + transformer data, hourly consumption by postal code. Open, no key, verified live. NODAL.
2. **RTE Data Portal** — Consumption/per-unit Generation/Physical Flow/NTC APIs, free OAuth2 account. Partner-only: **Big-Substations** (poste-source data — chase commercially later).
3. **Terna developer portal + Econnextion** — free reg APIs (load 15-min, zonal transit limits/margins); Econnextion = monthly geolocated register of ALL RTN connection requests **including demand/datacenters** (rare!), open download. TE.R.R.A. capacity platform — manual check via MyTerna.
4. **REE nodal capacity** — demand maps NEW (Dec 2025/Feb 2026, ~75% of nodes saturated), PDF/CSV/XLSX monthly; generation equivalent. **Capture snapshots NOW — the time dimension doesn't exist yet.**
5. **ODRE (FR)** — 187 open datasets: éCO2mix 15/30-min national/regional, RTE line/substation GeoJSON, **contraintes-region** (per-asset residual constraints w/ MW+duration), RES queue by region. No key.

## Registrations
RTE (immediate OAuth2) · Terna developer (immediate) · ESIOS token (email consultasios@ree.es — days, start now) · GME (ToS accept) · i-DE + UFD capacity maps bot-walled — manual browser check · GAUDÌ operator-only (skip; use Terna APIs).

## France
ODRE (above) · RTE APIs · Caparéseau (generation-side nodal capacity + queue MW per poste source; no demand; scrape map backend) · **Enedis MIGRATED to opendata.enedis.fr** (ODS-compat API works today, "compatibility layer" — breakage risk; HH conso/prod curves by profile band; **quarterly production connection-queue history**; poste locations; full MV/LV geometry; IRIS annual consumption) · Agence ORE (all-DSO 15-min power + postes-sources incl. non-Enedis + queue by département).

## Spain
REE nodal (above) + ESIOS (~1,900 indicators, 5-min) + REData (open, no token, verified) · e-Distribución: per-node demand capacity **monthly XLSX/CSV + archive since Sept 2025** (available/occupied/admitted-pending) + generation · i-DE/UFD/Viesgo: same obligations, bot-walled/manual · OMIE flat files (open, since 1998, scriptable patterns) · No open GIS from REE (GeoRed private) — IGN BTN layers + OSM for geometry · MITECO monthly balances.

## Portugal
E-Redes (above; also completed-connections datasets) · REN Data Hub (15-min dashboard, API daily/monthly aggregates only — use ENTSO-E for hourly PT history) · DGEG (quarterly injection capacity RNT+RND since Feb 2025; demand capacity-allocation procedures — Sines etc.).

## Italy
Terna developer APIs (above) · Econnextion (above) · dati.terna.it Download Center (provincial consumption, zonal series) · GME XML archives (PUN + 7 zonal prices, deep history, ToS-free) · GAUDÌ closed · ARERA context.

## Cross-cutting
- ENTSO-E TP = uniform hourly backbone for all four while national APIs come online.
- **None of the four publishes impedances/thermal ratings openly.** Closest: E-Redes short-circuit (PT), ODRE geometry+voltage (FR), contraintes-region MW limits (FR). DC screening → per-km impedance libraries by voltage class + JAO SGM (FR only) + OSM model.
- FR platform migration risk (Enedis/ORE compat API); ES demand-map snapshots are short-history — archive from day one.
