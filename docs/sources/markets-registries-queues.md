# Source inventory — Markets · Registries · Queues · Siting intel
*Research agent output, verified 2026-07-22. Feeds docs/data-sources.md.*

## Top 5 must-haves
1. **ENTSO-E TP** — DA prices, imbalance/balancing, load, per-unit gen, all 10 markets, one API (token).
2. **NESO GB suite** — TEC Register (per-project MW/substation/stage, monthly CSV; new Data Portal URL), Capacity Market Register (unit-level, de-facto asset register), EAC auction results (DC/DM/DR prices). All open.
3. **MaStR (DE)** — injection mapping solved for DE (daily bulk, unit lat/lon + grid operator).
4. **National balancing portals stack** — OMIE (open flat files, ES/PT since 1998) · ESIOS (token by email) · Elexon Insights (open, per-BMU) · Regelleistung.net (open, DE bid lists) · Netztransparenz (Client-ID) · GME (accept terms) · dati.terna.it (key) · PSE API (open) · Fingrid (key) · Svk Mimer (open) · RTE (OAuth2). ≈90% of what BNEF sells, free.
5. **EUTL + EEA Industrial Emissions Portal** — geocoded large industrial sites + verified emissions = ranked list of large loads WITH existing firm connections; declining-emissions sites = stranded connection capacity = prime lease-a-connection prospects. Open CSV.

## Registrations
ENTSO-E TP · ESIOS (consultasios@ree.es) · RTE OAuth2 · Fingrid key · dati.terna.it · Netztransparenz Client-ID · Nord Pool account (view free; API paid) · Modo Energy free tier (work email; GB+DE BESS indices) · GEM downloads · GME terms · Cloudscene freemium. Paid-only: EEX DataSource, Nord Pool API, LCP Delta, DC Byte.

## Queue transparency ranking (per market)
- **GB best-in-class**: TEC register + ECR accepted-not-connected (per-DNO, incl. queue) + Gate 2 outcomes + REPD.
- **PL**: PSE + all DSOs publish MONTHLY applicant lists (name, substation, MW, sources AND demand!) + refusals + zero-capacity nodes (art. 7(8l)). Closest to Irish-style register on continent.
- **NO**: Statnett dashboard: reserved/queued by company, county, industry — DATACENTERS visible (3.5 GW reserved / 5.4 GW queue). + Nkom mandatory DC register since 2025 (public access TBC manually).
- **ES**: REE demand access maps NEW 2026 (~25% of transmission nodes have free demand capacity; 74 demand-tender nodes: Andalucía 18, CyL 12, Aragón 9); CNMC combined maps Apr 2026 (distribution 83.4% saturated); granted totals: 129 GW RES, 16 GW storage, 19 GW demand.
- **PT**: DGEG quarterly injection capacity per network point + demand allocation notices.
- **FI**: Fingrid Grid Scope (capacity, not applicants). **IT**: Terna Econnextion (generation only). **FR**: Caparéseau queue MW (generation only). **SE**: new Svk map (county, no applicants). **DE: nothing** — worst; MaStR planned units + NEP as proxies.

## Injection registries per market
DE MaStR · GB ECR+REPD+BMU registry (Elexon, maps units to GSPs) · FR ODRÉ national register + Enedis · ES PRETOR · IT GAUDÌ NOT public (use Econnextion + dati.terna + GSE Atlaimpianti) · PL URE registers · SE Vindbrukskollen (wind only, gap) · NO NVE API · FI Energiavirasto XLSX · PT E-Redes open data (consumption per substation!). Cross-checks: powerplantmatching, GEM trackers (status incl. pre-construction), WRI (stale 2021), OpenInfraMap.

## Capacity markets (revenue leg)
GB CM register + EMR auction results · IT capacity market (2027 main: 43 GW @ €47k/MW/yr; storage ~60% of new-build 2026) · PL Rynek Mocy · FR (EPEX capacity + RTE registry; reform pending) · ES/PT: none yet (monitor MITECO state-aid) · Nordics: strategic reserves only. No CM revenue line in Nordics/Iberia today.

## BESS benchmarks
Modo Energy free tier: GB index (~£41–73k/MW/yr through 2026) + DE index (May 2026). LCP Delta paid. Else build from open stack above.

## DC/large-load siting
DataCenterMap (free browse) · Baxtel (best free MW estimates) · Cloudscene freemium · national DC associations (DE/FR/IT; SpainDC domain dead — manual) · Statnett DC stats (EirGrid-grade) · Nkom register (manual follow-up) · Uptime survey.

## Industrial load targeting
EUTL (~10k installations, emissions ∝ load) + EEA IEP (~60k geocoded facilities — the geo-join layer) + UK ETS for GB · Datadis ES (municipal consumption) · ODRÉ/Enedis FR · E-Redes PT (load per substation) · Eurostat (sector sizing).

## Gaps & watch
1. DE queue opacity = structural; 2. demand-side queues public only in PL/NO/ES/PT/GB; 3. ES demand tenders = queue intel WITH DEADLINE (action market); 4. ENTSO-E balancing coverage inconsistent → national portals from day one; 5. manual follow-ups: Nkom register, SpainDC, ENA queue pages, SPEN/SSEN ECRs, i-DE map (bot-blocked, human-accessible).
