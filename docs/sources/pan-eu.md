# Source inventory — Pan-European
*Research agent output, verified 2026-07-22. Feeds docs/data-sources.md.*

## Top 5 must-haves
1. **JAO Publication Tools (Core + Nordic)** — OPEN no-auth JSON APIs with real operator hourly CNEC-level data: zone-to-slack PTDFs per network element, Fmax/Imax, FRM, RAM (=TSO-computed transmission headroom!), substation names, contingencies. Core=DE/FR/PL (since Jun 2022), Nordic=FI/SE/NO (since Oct 2024). Verified live: publicationtool.jao.eu/core/api/data/finalComputation etc.
2. **JAO Core Static Grid Model** — free Excel, Core-region elements with R,X,B,Imax,voltage, 6-monthly (8th release Oct 2025). pandapower `from_jao` importer exists. Real (non-OSM) network model for DE/FR/PL+neighbours. jao.eu/static-grid-model
3. **ENTSO-E Transparency Platform** — hourly zonal load, per-unit generation ≥100MW (EIC-coded), cross-border flows, outages, redispatch. FREE but token via email (~3 working days) — REGISTER NOW. GB data STOPPED June 2021 (Brexit) — GB needs Elexon/NESO. entsoe-py client; SFTP bulk.
4. **PyPSA-Eur prebuilt OSM network v0.7 (Zenodo 18619025, Feb 2026)** — 220–750kV, all 35 ENTSO-E countries incl. all our 10, buses/lines/transformers CSVs with impedances + s_nom ratings, ODbL (share-alike!), Nature Sci Data 2025 peer-reviewed. Only full-footprint open network model. PyPSA gives DC-PF/PTDF/LODF out of the box.
5. **ENTSO-E ERAA 2024 + TYNDP 2024** — hourly zonal demand ZIP (701MB, multi-climate-year), PEMMDB capacities, NTCs, FB domains, reference grid XLSX, CC-BY-4.0.

## Registrations (start now)
- ENTSO-E TP: account + email transparency@entsoe.eu "RESTful API access" (~3 days lead) → token + SFTP bulk
- Global Energy Monitor downloads (form-gated)
- (JAO needs NO registration for publication tools/SGM)

## Other verified
- **JAO SWE + Italy North CCRs**: NTC-based only (no PTDFs) — border ATCs for ES/PT/IT/FR-south.
- **powerplantmatching (PyPSA)**: merged deduped EU plant list w/ coords, CC-BY-4.0 — THE coordinate join for TP per-unit generation. v0.8.1 Feb 2026.
- **JRC-PPDB-OPEN** (Zenodo 3574566): EIC↔location bridge, ageing (2019) but useful.
- **GEM Global Integrated Power Tracker**: unit-level with announced/pre-construction status = best open generation-pipeline proxy. Form signup.
- **ENTSO-E/EU DSO Entity pan-EU Hosting Capacity Portal**: launch targeted ~mid-2026 — WATCH (validation index + competitive marker).
- **Ember**: hourly EU wholesale prices CC-BY-4.0 (GCS CSVs); hosting-capacity-maps country review (Jul 2024, updated Jun 2026) = ready-made audit framework.
- **Beyond Fossil Fuels/AFRY queue reports**: 1,700 GW stuck in TSO queues (16 countries, 2024); 375 GW RES + 455 GW storage in DSO queues (Jun 2026). Country aggregates only.
- **Copernicus PECD v4.2**: hourly zone demand + RES capacity factors 1950+, climate-stress profiles.
- **energy-charts.info** (Fraunhofer): free JSON hourly mirror. **OPSD**: frozen 2020, backhistory only. **Electricity Maps contrib repo**: catalogue of every national real-time source (sourcing map).
- Legacy models (SciGRID/GridKit/osmTGmod/Bialek): superseded — skip.

## Confirmed gaps (stop looking pan-EU)
1. No pan-EU nodal load data — TP zonal + unit-gen ≥100MW disaggregated on PyPSA-Eur is the ceiling; DSO-level demand = national sources.
2. No open operator nodal model beyond JAO Core SGM; TYNDP CGMES restricted (can request; don't plan on it).
3. No pan-EU queue register — national only. This is a data moat to BUILD, not buy.
4. GB absent from ENTSO-E TP + all JAO CCRs → all-national (NESO/Elexon).
5. ES/PT/IT: no published PTDFs (NTC regions) → own DC-PF + TP flows/redispatch for validation there.
