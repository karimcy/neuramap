# Atlas bridge: open GB constraint layers from the Neura Constraint Atlas

NeuraMap shows a matrix row, **GB constraints — Atlas**, fed from the Neura Constraint Atlas (Neura's GB constraint
and curtailment map). The row is GB only; every other market shows the gap "Neura Atlas covers GB only".

Everything under `data/atlas/` is written by the Atlas's exporter (`tools/neuramap_export.py` in the Atlas repository)
from open sources only. Nothing in `js/app.js`, `js/data.js` or `data/GB.json` is changed, and no Atlas figure enters
the node layers, the deal screen, the fit score or the node popups.

## What comes from the Atlas

| Sub-layer | What it shows | Source keys | Publisher and licence | Atlas methods | Basis |
|---|---|---|---|---|---|
| Wind turn-down by BM unit | Net SO-flagged accepted turn-down against the unit's own FPN, MWh, per wind BM unit that the Atlas draws (class A or B), over the snapshot window | `elexon_boalf`, `elexon_pn`, `elexon_bmunits` (figures); `osm`, `repd` (locations) | Elexon, BMRS Data Licence; OpenStreetMap contributors, ODbL; DESNZ REPD, OGL v3.0 | `TURNDOWN_NET_v1`, `SOFLAG_CLASS_v1`, `SCOPE_WIND_v1`, `LOC_A_v1`, `LOC_B_v1`, `LOC_AGG_v1` | Atlas nowcast; not Elexon indicative, not settlement |
| Boundary forecasts | NESO's day-ahead forecast of flow against limit per constraint group, drawn on NESO's ETYS boundary lines where the Atlas maps the group (unverified), and NESO's OTF weekly limits on the boundaries NESO names | `neso_da_constraints`, `neso_etys_boundaries`, `neso_otf_congestion`; mapping evidence `neso_24m_limits`, `pypsa_gb_boundary_map`, `neso` | NESO, NESO Open Data Licence (Data Portal only); PyPSA-GB, MIT | `DA_STRESS_v1`, `BOUNDARY_GEOM_v1`, `OTF_LIMIT_v1` | NESO day-ahead forecast, made before NESO's actions |
| Interconnectors | Half-hourly outturn per interconnector from NESO's Demand Data Update (actual rows), on OpenStreetMap cable geometry | `neso_demand_update`; `osm` (geometry) | NESO, NESO Open Data Licence; OpenStreetMap contributors, ODbL | `IC_FLOW_v1`, `IC_GEOM_v1` | NESO outturn, published with a lag |
| Curtailment index | GB wind BM units, monthly: net turn-down, gross bid volume and Elexon-indicative cashflow; the validation result is kept only where its reference is open | `elexon_boav`, `elexon_ispstack`, `elexon_bmunits` | Elexon, BMRS Data Licence | `CIX_GB_v1` and the measures' own methods | Elexon indicative, not settlement |

Every block and every feature in the files carries `src` (its source keys) and the method ids it rests on.
`data/atlas/GB.json` holds, per sub-layer, a `srcs` list in NeuraMap's own shape (`op`, `u`, `d`) extended with the
retrieval time, licence, licence quote and attribution of each source. The freshness strip reads the snapshot's date
under the same 60-day rule as the other feeds.

### Files

| File | Contents | Licence |
|---|---|---|
| `data/atlas/GB.json` | Manifest: snapshots (newest first), geometry files, `srcs`, method labels, attribution, the list of what is absent and why | as each entry states |
| `data/atlas/snapshots/<stamp>.json` | One snapshot's figures: turn-down per unit with the conservation identity, boundary forecast strips, OTF limits, interconnector strips, the index | BMRS Data Licence and NESO Open Data Licence, per block |
| `data/atlas/gb_units_osm.geojson` | Unit locations taken from OpenStreetMap objects (with the OSM object reference) | **ODbL 1.0** |
| `data/atlas/gb_units_repd.geojson` | Unit locations taken from REPD | OGL v3.0 |
| `data/atlas/gb_ic_osm.geojson` | Interconnector cable geometry from OpenStreetMap | **ODbL 1.0** |
| `data/atlas/gb_etys_neso.geojson` | NESO ETYS boundary lines | NESO Open Data Licence |
| `js/atlas.js` | The matrix row, the panel under the coverage matrix and the three sub-layers | this repository's terms |

The files join in the browser by id (BM unit id, interconnector key, ETYS boundary name), so the OpenStreetMap-derived
geometry stays in files of its own.

## Licences and attribution

Quoted from the Atlas's licence register (`research/LICENCES.md`).

- **Elexon, BMRS Data Licence.** "You are free to: copy, publish, distribute and transmit the BMRS Data; adapt the
  BMRS Data; exploit the BMRS Data, including commercially, or by including it in your own product or application."
  Attribution: "Contains BMRS data © Elexon Limited copyright and database right {year}".
- **NESO, NESO Open Data Licence.** It "grants you a worldwide, royalty-free, perpetual, non-exclusive licence to use
  the Information": "copy, publish, distribute and transmit the Information; adapt the Information; exploit the
  Information commercially and non-commercially …". Attribution: "Supported by National Energy SO Open Data"; no
  suggestion of official status or endorsement. It covers the NESO Data Portal only. Files on the neso.energy website
  are under its website terms and are not used here.
- **DESNZ REPD, Open Government Licence v3.0.** "Contains public sector information licensed under the Open
  Government Licence v3.0".
- **OpenStreetMap, ODbL 1.0.** "© OpenStreetMap contributors". The register records that "Our BMU → OSM-object table
  is a derived database and must be offered under ODbL if published." The two OSM-derived files are therefore offered
  under the Open Database Licence 1.0, each carries that notice in its own `notice` field, and any database derived
  from them must also be offered under ODbL. Keep them separate from files under other licences.
- **PyPSA-GB, MIT.** ETYS names stated in its boundary mapping are one evidence type for the boundary mapping.
- **OSUKED Power Station Dictionary.** "Power Station Dictionary (OSUKED / Ayrton Bourn), CC BY 4.0".

The panel shows these attributions, and the map's attribution control adds them while the layer is on.

## How to refresh

The owner runs it, on the machine where the Atlas server runs on loopback:

```
cd atlas
.venv/bin/python tools/neuramap_export.py --check            # build and test everything, write nothing
.venv/bin/python tools/neuramap_export.py --out ../neuramap  # write a new snapshot into data/atlas/
.venv/bin/python tools/neuramap_export.py --verify --out ../neuramap   # re-test what is on disk against today's gate
```

The exporter reads the Atlas server's HTTP API on 127.0.0.1 only. Each run adds a stamped snapshot and makes it the
default; earlier snapshots stay listed and can be picked in the panel. It refuses, and writes nothing, if any of
these fails:

- every source key behind every feature is open (open by its licence, or recorded as open in the Atlas's
  authorisation register) and not gated today;
- every method id is one the Atlas ships;
- no figure of a source held for the owner appears in any text written (the Atlas's owner-figure rule);
- no customer, site or person name from Neura's other projects or the Atlas's curated files appears, whole word;
- the Atlas's wording rules hold (below);
- drawn units plus the not-drawn total equal the GB wind total.

The exporter never commits or pushes. Committing the new files is the owner's step after `--check` passes.

## What is deliberately not published, and why

| Not here | Why |
|---|---|
| Distribution network data (the DNOs' own published layers, retrieved through their portals) | Held for the owner only until each publisher's terms for republication are recorded. This covers the Huwise-hosted portals (UKPN, SPEN, NPg, ENWL, SSEN Transmission), NGED live data, SSEN NeRDA, the DNO service areas and the DNOs' LTDS CIM models |
| Circuits crossing each boundary | The set combines open data with a source whose terms are not recorded |
| TEC register sites as queued nodes | No approved method places a TEC site; a name match would be an undeclared stand-in. NeuraMap already carries its own TEC layers |
| Solar, storage and other non-wind units | No approved location method outside wind |
| Unit names | Free-text site names are not exported; each marker carries its BM unit id, REPD id and OSM object reference, and the public records give the name |
| Per-unit figures on the Elexon indicative basis | Not held per unit |
| Constraint cost | The Atlas reports cashflow to units, not a cost |
| Turn-down assigned to a boundary | Not computed; no method links a unit's turn-down to a boundary |
| Validation results that stand on a reference whose terms are not recorded | Withheld with that reason; only checks against open references are shown |
| The Atlas's transmission model | It stands on NESO website files (the ETYS appendices), which are not under the open licence |
| Weather (ERA5), REF farm figures, wastedwind | Held for the owner only until their terms are recorded |

NeuraMap's own GB layers (`gb_dist`, `gb_tec_gen`, `gb_tec_queue`), `data/GB.json` and `js/data.js` are Karim's data
under his terms; the bridge does not touch, restyle, describe or endorse them.

## Wording

- Curtailment here is net SO-flagged accepted turn-down against the unit's own FPN, which is the unit's own
  declaration of output, not a measure of what it could have produced. The window can end after the snapshot time,
  because NESO accepts some instructions ahead of the period they apply to; such periods hold only the acceptances
  issued by then, and the panel says so.
- Boundary figures are NESO's day-ahead forecast, made before NESO acts. A group is "forecast binding" when its
  forecast stress is at least 1. Nothing is described as a measured, live or actual flow, and turn-down is never
  attributed to a boundary.
- Every group-to-line mapping is a Neura mapping and unverified: mapped (solid) when two or more evidence types agree,
  candidate (dashed) with one; other groups are listed in the panel without a line.
- Elexon BOAV and ISPSTACK figures are "Elexon indicative", never final settlement.
- Atlas model outputs are "modelled"; none is exported today.

## Decisions (Fable 5.2, 2026-11-26)

The design choices were delegated by the owner to Fable 5.2, the Atlas's design advisor, choosing the most
innovative option inside the rules. The full record is `research/37_neuramap_bridge.md` in the Atlas repository.

- **Layers.** Wind turn-down by BM unit (class A and B, wind only), all ETYS lines with day-ahead stress for mapped
  and candidate groups, interconnector outturn and the GB wind index are published. Boundary circuits and TEC
  nodes are not. A candidate group whose registry entry carries an unresolved note (ESTEX) is listed only: the export
  may be stricter than the Atlas, never looser.
- **Schema.** No Atlas feature enters `COUNTRIES.GB.nodes`: NeuraMap's node popups would call it available
  capacity and it would reach the fit score. The bridge has its own row, files, panel and popups.
- **Stress on lines.** Colour and width show the share of settlement periods forecast binding in the window; the popup
  gives the highest forecast stress with its time, a per-period strip, and the counts of periods with no limit
  modelled and with no forecast. Stress at one instant is not shown on the map, because it is stale once committed.
- **Refresh.** Stamped snapshots, owner-run, with a picker; the forecast layers grey out once a snapshot is older
  than its forecast window.
- **Turn-down window.** Seven days on the Atlas nowcast basis, with the held and expected settlement periods, and the
  drawn, not-drawn and GB totals always on screen.
- **Names.** BM unit id only (see the table above).
- **Interconnectors.** The raw half-hourly rows of the latest fully published settlement day and any later
  published periods, as a strip with gaps; the latest published period, the highest import and the highest export
  are selections of published values. No mean and no GB net sum.
- **Deal screen.** Not connected. A proximity join between turn-down or stress and a demand node would be a new
  derived number and a location proxy with no registered method.
- **Other layers.** Karim's layers are not touched; other markets get the gap text.

Two implementation choices differ in form from the advice and keep its intent: the wiring needs no edit to
`js/app.js` at all (`js/atlas.js` adds its row and wraps `rebuildMatrix` and `renderFreshness` at load, so the only
edit to a generated file is one script tag in `index.html`, kept as a pair in `deploy/atlas_patches.py`); and each
snapshot is one file under `data/atlas/snapshots/` rather than a folder.

`deploy/sync.sh` already ships `js/` and `data/`, so the layer deploys with the rest of the site; `docs/` is not
deployed, which is why every data file carries its own licence and attribution.
