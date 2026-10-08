# Data Source Master Inventory — consolidated 2026-07-22

Five deep-research sweeps, every source live-verified. Full detail per slice in `docs/sources/`:
`pan-eu.md` · `gb.md` · `fr-es-pt-it.md` · `de-nordics-pl.md` · `markets-registries-queues.md`

## The 10 findings that shape the plan

1. **JAO publishes real operator PTDFs, element ratings and remaining margins (RAM), hourly, per CNEC — open, no auth** — Core (DE/FR/PL, since 2022) + Nordic (FI/SE/NO, since Oct 2024). Our DC-PF gets calibrated against TSO truth, not assumptions.
2. **Two real network models exist free**: JAO Core Static Grid Model (R/X/B/Imax, pandapower importer) and GB ETYS Appendix B (direct anonymous xlsx). Plus machine-readable LTDS impedances for 5 of 6 GB DNO groups. Everything else → PyPSA-Eur OSM model (ODbL) calibrated against JAO/capacity maps.
3. **GB half-hourly nodal load is abundant** (UKPN ~155M records; SPEN 17M; NGED per-site; SSEN/Weave LV parquet) but **key-gated per portal** (free, instant). Elexon P114 = GSP-group settlement demand (portal scripting key).
4. **Nordics hourly sub-zonal load is solved, open, no keys**: eSett (SE/FI/NO per metering-grid-area) + Elhub (NO municipal). 
5. **Portugal is nodal out of the box**: E-Redes per-substation 15-min load + headroom + short-circuit data, open API. Fastest full pilot of the "measured profile" tier outside GB.
6. **Spain is the action market**: first-ever demand capacity maps (Dec 2025+, ~75% of transmission nodes saturated), 74 demand-tender nodes, monthly DSO XLSX archives — and nobody has the time dimension yet. Archive snapshots from today.
7. **Demand-side queues are public in GB, PL, NO, ES, PT** (+ IT via Econnextion incl. datacenters). DE publishes nothing (structural gap → product argument). Norway names companies + industry (datacenters visible: 3.5 GW reserved / 5.4 GW queued).
8. **Injection mapping**: MaStR (DE, daily bulk) + ENTSO-E per-unit ≥100MW + powerplantmatching coordinates + national registers per market.
9. **The BESS revenue stack is fully buildable from open sources** (OMIE/ESIOS/Elexon/Regelleistung/GME/PSE/Fingrid/Mimer/RTE + capacity-market registers) — no BNEF dependency. Modo free tier as GB/DE benchmark.
10. **Offtaker targeting**: EUTL + EEA Industrial Emissions Portal = geocoded industrial loads with existing firm connections; declining emitters = stranded-connection lease prospects.

## Distribution-grid solar & battery registers (survey 2026-07-26)

Which markets publish *where the solar and batteries already are*, at distribution
level, with enough grid context to be useful for siting. Verified live on the date
above.

| Market | Source | Grid context | Storage MWh | State |
|---|---|---|---|---|
| **FR** | ODRE `registre-national-installation-production-stockage-electricite-agrege`, 137k rows, one export call, no key | **poste source + BT/HTA/kV + DSO name** | **yes** (`energiestockable`) | **shipped** — `pipelines/fr_assets.py` |
| **GB** | REPD (DESNZ) × NESO TEC | TEC connection site | no | shipped — `pipelines/gb_assets.py` |
| **DE** | MaStR `Gesamtdatenexport` (3.1 GB zip, rebuilt daily, no key) | `Netzanschlusspunkte` = connection point, voltage level, network operator; `Lokationen` = coords | yes | **best next build**. Server sends `Accept-Ranges: bytes`, so the 27 `EinheitenStromSpeicher_*.xml` members (~430 MB) can be range-fetched without the other 2.6 GB. Solar is 64 members / ~1 GB and mostly rooftop. |
| ES | No open national plant register with coordinates. e-Distribución / i-DE capacity maps are per-node but demand-side; PRETOR access permits are not public. | — | — | gap — the ES snapshot archiver is the substitute |
| IT | Terna GAUDÌ / GSE Atlaimpianti are portal-only, no bulk API found | — | — | needs manual work |
| PL / SE / FI / NO / PT | National registers exist (URE, Energimyndigheten, Energiavirasto, NVE, DGEG) but are address- or municipality-level, no substation reference | weak | mostly no | low value per unit of effort |

Two things fall out of this that matter beyond the map:
- **FR is the only market so far that publishes battery duration.** 830 French
  batteries have real MWh. Median duration is 0.8 h — the French fleet is
  frequency-response, not energy-shifting. That is a directly usable prior for
  what a firming battery has to be, and it is a fact about the market that the
  GB data cannot tell us.
- **DE would give duration at scale plus the connection voltage and the network
  operator per unit**, which is closer to a hosting-capacity signal than anything
  else free in Europe.

## Registration checklist (user actions, all free)
**Lead-time first:** ENTSO-E TP token (email, ~3 days) · ESIOS token (email) · Elexon Portal scripting key.
**Instant:** UKPN · Northern Powergrid · NGED · SPEN · ENWL (portal keys) · RTE OAuth2 · Terna developer · Fingrid · Netztransparenz Client-ID · GEM downloads · Modo free tier (work email) · ENWL website (LTDS).
**No registration needed:** JAO (all tools) · NESO · Elexon Insights · eSett · Elhub · SMARD · PSE · OMIE · ODRE · Enedis/ORE · E-Redes · REE/REData · e-Distribución · GME (ToS) · Statnett · Svk/Mimer · MaStR bulk · EUTL/EEA · SSEN (browser UA).
**Manual follow-ups:** i-DE + UFD capacity maps (bot-walled) · Nkom NO datacenter register · TE.R.R.A. access · SpainDC domain.

## Confirmed gaps (stop searching; build or accept)
No pan-EU nodal load · no open operator network model outside JAO Core/ETYS · no pan-EU queue register (data moat to build) · GB absent from ENTSO-E TP · ES/PT/IT have no published PTDFs · DE queue opacity · SE unit register (wind only) · impedances unpublished in FR/ES/PT/IT.
