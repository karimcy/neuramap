# Source inventory — DE / SE / NO / FI / PL
*Research agent output, verified 2026-07-22. Feeds docs/data-sources.md.*

## Top 5 must-haves
1. **MaStR bulk download (DE)** — complete German unit register w/ grid connections, daily XML dump, no registration. Best injection-mapping dataset in Europe. `open-mastr` package loads to Postgres.
2. **eSett Open Data (SE+NO+FI)** — settlement-grade hourly/15-min consumption+production per Metering Grid Area (≈DSO area). Open REST, NO KEY (api.opendata.esett.com — verified live).
3. **German TSO static grid models** — 50Hertz XLSX (380/220kV circuits+transformers+params), Amprion→JAO Core static grid model (jao.eu/static-grid-model), TenneT/TransnetBW equivalents. Only market publishing element-level network models.
4. **PSE API v2 (PL)** — api.raporty.pse.pl, open, no key, 15-min load/gen/balancing, OData paging (verified live). + quarterly per-substation connection-capacity PDF (>110kV, 5y outlook).
5. **Fingrid Open Data + Grid Scope** — data.fingrid.fi (free API key, 10k req/day) + Grid Scope map: per-substation connection capacity for production AND consumption, current + future years.

## Registrations needed (free)
- data.fingrid.fi → x-api-key (register multiple for bulk backfill)
- api-portal.netztransparenz.de → OAuth2 (four-TSO redispatch/EEG/balancing WebAPI)
- MaStR webservice (only for incremental API; bulk needs nothing)
- NVE account (some api.nve.no endpoints; most open)
- NOT needed: SMARD, eSett, Elhub, PSE, Statnett, Svk/Mimer, all capacity maps

## Germany
- **MaStR**: marktstammdatenregister.de/MaStR/Datendownload — daily full XML, unit-level lat/long+voltage+DSO. Injections HIGH.
- **TSO static grid models**: 50hertz.com .../Staticgridmodel (XLSX), amprion → JAO, netztransparenz.tennet.eu, transnetbw.de/en/transparency. Network model HIGH. Caveat: no 110kV distribution level.
- **SMARD**: open JSON API (bundesAPI/smard-api pattern), 15-min, per control area + DE/LU zone, history to 2015. Injections HIGH (zonal).
- **Netztransparenz**: redispatch per-measure records (plant/grid element, MW, duration) history to 2013, CSV; Redispatch 2.0 incl. DSO curtailments since Oct 2021. **Headroom validation HIGH — redispatch = revealed congestion.**
- **TSO vertical grid load** (per control area, 15-min): 50Hertz CSV, Amprion undocumented CSV API (amprion.net/api/grid-data/items/csv/NETZLAST/{from}/{to}; site blocks generic UA), TransnetBW open CSV. ≈TSO→DSO offtake — key demand series.
- **DSO hosting capacity**: fragmented, application-driven. Netzampel.energy (Bayernwerk/Avacon/E.DIS/SH Netz feed-in+curtailment to municipality), Westnetz SNAP, Netze BW MV lookup. No national map.
- **BNetzA Kraftwerksliste**: all plants ≥10MW w/ grid operator + voltage level, XLSX. Injections HIGH (easier than raw MaStR for large units).

## Sweden
- **Svk Kapacitetskarta** (NEW June 2026): county-level offtake AND injection capacity + QUEUE data, station-level planned. svk.se/aktorsportalen/anslut-till-transmissionsnatet/kapacitetskarta-transmissionsnatet/. Re-scrape — our extract may predate relaunch.
- **eSett**: MGA-level hourly (see top 5).
- **Svk Mimer** (mimer.svk.se) + Elstatistik: hourly zonal production/consumption, CSV/Excel, open.
- **Vattenfall Eldistribution Kapacitetskarta**: per-node regionnät indicative capacity (consumption 1–50MW, production 1–120MW, storage). vattenfalleldistribution.se/kapacitetskarta
- **E.ON Kapacitetskompassen**: map of Nätutvecklingsplan 2025–34.
- **Ellevio: NO public capacity map** (gap — use Svk map + their PDF plan).
- **Ei (regulator)**: per-DSO annual technical/reliability data, Excel.

## Norway
- **Statnett queue dashboard**: reserved/queued/withdrawn/connected WITH company names, MW, region, industry — Power BI (scrape backend). Best queue publication in the five markets. statnett.no/for-aktorer-i-kraftbransjen/nettkapasitet-til-produksjon-og-forbruk/foresporsler-og-reservasjon-i-nettet/
- **WattApp** (wattapp.no): national DSO capacity map, 1–20MW distribution level (Elvia, Lede, Lnett, Glitre +). Headroom HIGH.
- **Elhub** (elhub.no/data): hourly consumption+production per municipality/MGA/group, 2021+, CC BY 4.0, no reg, REST/CSV. Injections HIGH.
- **Statnett driftsdata**: zonal flows/production, open. Områdeplaner PDFs 2025 (per-region capacity status).
- **NVE**: grid GIS (temakart.nve.no, nedlasting.nve.no/gis, ArcGIS REST Nettanlegg2) — line routes+substations, NO impedances. api.nve.no (reservoirs, plants). NLOD licence.
- **DSOs**: Elvia Tilko (login queue), Lnett indicative map, Glitre map; BKK gap. Since 2025-01-01 all >1MW connections have formal maturity/queue process.

## Poland
- **PSE API v2**: see top 5. Unit-level generation = quasi-nodal.
- **PSE connection capacity**: per-substation >110kV PDF, quarterly, 5y outlook (statutory art. 7 ust. 8l). Generation-oriented.
- **Tauron dostepnemoce.tauron-dystrybucja.pl**: interactive maps, station-group capacity 2025–30, no login, scrape.
- **Enea Operator**: 4 Power BI dashboards updated DAILY (installed RES, issued conditions, available MV capacity). Best-automated PL DSO.
- **PGE Dystrybucja / Energa / Stoen**: quarterly per-110kV-node PDFs (Energa: 0 MW network-wide — saturated).
- **URE**: connection-refusal statistics (record volumes) — demand-pressure signal.

## Cross-cutting
- Sub-zonal hourly load: SOLVED for NO/SE/FI (Elhub+eSett). DE: TSO vertical load only. PL: system+unit only.
- Network models: DE only market with element-level params. SE/FI/PL: OSM-derived + calibrate vs capacity maps. NO: NVE geometry, no impedances.
- Queues: NO (Statnett, company-level) > PL (operator filings + URE refusals) > SE (new Svk map) >> DE, FI (nothing open).
- Capacity maps are indicative snapshots with disclaimers — treat as claims to validate, not ground truth.
