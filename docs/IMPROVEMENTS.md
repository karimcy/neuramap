# Improvements backlog — parked for later consideration

## Overview deck
- **Integrate the EU headroom figures into the company overview** (the European
  companion to the Norris/Nicholas Institute US numbers already cited on p.1).
  Battery-verified (chronological SOC sim, measured 2025 load, 10 markets):
  **42 GW fully firm with 8 h battery · 10 GW with 2 h · 59 GW at 99.9% served**;
  flexible-load variant (no battery): 51 GW @ ≤1% curtailment.
  Files: data/eu_flexible_headroom.json, pipelines/reports/eu_flexible_headroom.md.
  Chart idea: per-market bars firm-2h/firm-8h/flex-1% next to the existing LDC chart.
  Deliberately NOT in the deck yet per Karim.

## Headroom figure refinements
- Capability-margin variant: observed-peak ceiling is the strict floor; ×1.03 → 31/64 GW
  (2h/8h firm), ×1.05 → 42/74 GW. Defensible middle: derive per-market capability from
  capacity-mechanism procurement targets / adequacy reports instead of a flat multiplier.
- Bidding-zone granularity for SE (SE1–4) and NO (NO1–5) instead of country level —
  zonal peaks bind differently; eSett/Elhub have the data.
- Multi-year SEASONAL peaks (Norris uses multi-year seasonal maxima; we use 2025's —
  single-year seasonal floors are stricter). Backfill 2022–2024 and take per-season
  maxima across years; consider PECD climate-stressed variants.
- Derive the 8 h firm-uplift factor from measured LDCs per market (replaces the ×3.4
  indicative assumption in the funnel; the 2 h ×2.6 can be validated the same way).
- PT country series currently ENTSO-E-mirror based; swap to REN/E-Redes native when
  the ENTSO-E token arrives for cross-checking.

## Funnel / app
- ~~Wire data/pt_profiles.json into popups + score~~ **DONE 2026-07-23**: measured
  block + badge in popups, funnel accepts measured nodes (firmable MW at selected
  battery duration replaces published headroom; accent ring on map; `evidence`
  column in both CSVs). 14 of 20 pilot substations join map nodes by E-Redes code;
  6 have no map node (E-Redes publishes no coordinates).
- ~~Published-vs-measured divergence report~~ **DONE 2026-07-23**: "Measured vs
  published — PT pilot" sidebar accordion; per-substation divergence pills sorted
  worst-first, summary stats (14/20 within ±15%, 6 overstated >15%); rows fly to
  the mapped node.
- Upgrade the per-node firmable math (PT pilot now, GB when keys arrive) from LDC
  screening to the same chronological battery SOC simulation used system-level —
  node-level "can a battery go THERE" requires it (event clustering at node scale).
- JAO RAM overlay for Core/Nordic markets: per-CNEC remaining margin as a map layer.
- GB nodal profiles at ~4,000 nodes once UKPN/NPg keys arrive (pipeline ready).
- Spain snapshot time-series view once ≥3 monthly editions archived (saturation trend
  per node; 176→145 ≥100 MW nodes in one edition already).
- Deploy to the DigitalOcean droplet when Karim wants it public.

## Solar & BESS asset layer (GB + FR shipped; extend)
- ~~FR asset register~~ **DONE 2026-07-26**: `pipelines/fr_assets.py` →
  `data/fr_assets.geojson`, 4,488 installations ≥1 MW / 18.6 GW (865 battery,
  3,623 solar) from the ODRE national production+storage register. Carries what
  GB lacks: poste source, connection voltage (3,483 rows at HTA = MV
  distribution), DSO, and stored energy → **real duration on 830 batteries**
  (median 0.8 h — the French fleet is frequency-response, not energy-shifting).
  2,945 rows get a named substation join via the `postes-electriques-rte`
  code→name dictionary; the rest are labelled proximity-only in the UI and CSV.
  Register is the *connected* fleet, not a queue — the FR queue is already the
  Caparéseau S3REnR layer.
- FR position quality: the register publishes no coordinates, so dots sit on the
  commune centroid (flagged `geo: "commune"` everywhere it surfaces). Upgrade
  path: Enedis's own open-data portal moved to opendata.enedis.fr and its v2.1
  catalog endpoint now 410s — find the new dataset ids and check for a
  geolocated poste-source layer.
- **DE next (biggest remaining prize)**: MaStR bulk export, rebuilt daily, no key.
  Server supports byte ranges, so the 27 `EinheitenStromSpeicher_*.xml` members
  (~430 MB of a 3.1 GB zip) can be pulled without the rest. `Netzanschlusspunkte`
  gives connection point + voltage level + network operator; `Lokationen` gives
  coordinates. Would deliver duration at scale plus a per-unit grid reference —
  and DE currently shows gaps on three matrix rows.
- Duration (MWh) join for GB: REPD/TEC publish none. ENA-standard DNO Embedded
  Capacity Registers carry storage MWh — NGED's is anonymous (join now possible),
  the other five DNOs need the portal keys. TEC name-match is conservative
  (79 hits) — improve with connection-site + coordinate matching via gb_node_map.
- ES/IT/PL/SE/FI/NO/PT asset registers: surveyed 2026-07-26, see
  docs/data-sources.md. None publishes a substation reference; ES has no open
  national plant register at all. Low value per unit of effort — do DE first.
- Startup race: a matrix rebuild during the ~12 s market-load window can eat a
  checkbox click; preserve/replay pending toggles across rebuilds.

## Results workspace (shipped; extend)
- ~~Assets never reached the results table~~ **FIXED 2026-07-26**: `loadAll()`
  awaited a 4-element array but destructured index 2 for the asset geojson, which
  resolved to the pt_profiles fetch (undefined), so `buildRecords` got nothing and
  every asset row was silently missing. Record count went 3,612 → 12,038. Asset
  CRM state is now keyed via `crmKey()` (GB keeps its REPD id so saved pipeline
  entries survive) instead of `repdId`, which was undefined for every non-GB row
  and would have collapsed them onto one shared CRM entry.
- Saved views / shareable filter permalinks; bulk stage-change; kanban by stage.
- Notes/stages are localStorage (single browser) — server-side store when the team
  needs shared CRM state (droplet deploy moment).
- Asset pins live only on the results page; decide whether the map pipeline card
  should show them too.
- Map deep-links for assets (#asset=repd_id) and PT measured substations.

## Connectivity layer (shipped; harden)
- Fibre backbone is an ITU transmission-map extract currently mirrored via enersite's
  public file — re-source directly from ITU bbmaps (ArcGIS backend) and confirm the
  licence before any public deploy. PeeringDB + TeleGeography are canonical/open.
- Enrich: IXP objects (PeeringDB /ix + netixlan participant counts), latency-relevant
  metro tags, "distance to nearest facility/landing/fibre" as a funnel score component
  and results-page column (connectivity-aware siting).
- Skipped deliberately per Karim: enersite-style country electricity-cost factors.

## Data / pipelines
- ENTSO-E token → uniform hourly backbone + per-unit generation for injection mapping.
- Phase 2 DC power flow on gb_etys.json, calibrated against JAO PTDFs (plan 2.3b);
  collapse ETYS busbars to (site, voltage) supernodes per WP-GBNET's finding.
- Manual follow-ups: i-DE + UFD capacity maps (bot-walled), Nkom NO datacenter
  register, TE.R.R.A. access, SpainDC domain.
- Watch: ENTSO-E/EU DSO Entity pan-EU hosting-capacity portal (due ~mid-2026).
