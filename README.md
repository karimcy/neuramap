# OpportunityMap — European Grid Connection Atlas

**Neura Energy** · Interactive map of available grid connection headroom per node across
10 European markets. Answers the siting question for datacenters and other large loads:
*where can this actually plug in?*

Dark industrial UI over the CARTO dark-matter basemap. No build step, no framework —
static HTML/CSS/JS + Leaflet 1.9 with a single shared canvas renderer (13,441 nodes
draw in ~60 ms).

## Run

```sh
python3 serve.py          # → http://127.0.0.1:8734
```

`serve.py` gzips the ~45 MB data payload (~7× smaller) and answers 304s so reloads
are instant. Any static server works, but plain `python -m http.server` sends
everything raw and uncompressed.

## Structure

```
index.html        app shell (sidebar, map, legend)
css/app.css       design system (dark, IBM Plex, Neura orange accent)
js/data.js        data bundle: FREE / MANIFEST / MATRIX
js/app.js         all app logic
assets/           Neura brand assets
data/XX.json      lazy-loaded market bundles (GB FR PL IT DE SE NO PT)
lines/XX.geojson  grid line geometries per market
sites/XX.geojson  OSM industrial-land candidate sites per market
```

## Data model

- **FREE** — markets inlined in `js/data.js` (ES, FI) with nodes, layers, stats, a
  per-layer source registry (`srcs`), and Spain regional aggregates (`regs`).
- **MANIFEST** — lazy markets with teaser stats; fetched from `data/XX.json` on demand
  (tap the market code in the coverage matrix, or its dashed rectangle on the map).
- **MATRIX** — layer-row × market grid; `gap` entries document why a combination has no
  public source.
- Node kinds: `demand` (red ramp — connection headroom), `generation` (grid-strength
  proxy), `queued` (purple — pipeline requests, not headroom), `indicative` (teal —
  operator-flagged viable, no figure).

There is no pan-European source for this data. Coverage is assembled market by market:
national energy regulator → licensed-operator registry → each operator's capacity
publication (ArcGIS backends, Power BI dashboards, quarterly PDFs), normalised into a
single schema. Current snapshot: July 2026.

## Deal screen — the firming source funnel

The **Deal screen** mode turns the atlas into a sourcing funnel for the firming
business model (lease flexible import capacity, firm it with behind-the-meter BESS
sized to the curtailment duty):

- Headline KPIs: **GW of firm power accessible** with a 2 h and an 8 h battery
  (published headroom × firm-uplift: 2 h ×2.6 from the overview's 30%→78% day profile;
  8 h ×3.4 indicative), plus raw published headroom — screening totals, non-additive.
- Pick a **target load** (20/50/100/250 MW) and **battery duration** (2/8 h); every
  demand node ≥5 MW in loaded markets is scored 0–100 and coloured by tier
  (prime / strong / possible / weak).
- **Fit score** blends: firming-gap economics (45% — sweet spot where headroom covers
  most but not all of the target, so a right-sized battery bridges the rest), voltage
  class (20%), industrial land within 3 km (15%), and queued-demand pressure within
  30 km (20%, where the market publishes a queue).
- Node popups gain a **firming screen**: headroom vs target, gap, indicative BESS
  MW/MWh and capex (€145/kWh), sites and queue context, and the 12–18-months-to-power
  framing (vs 5–7 y conventional).
- **Pipeline**: star nodes into a persistent shortlist with stages
  (Screened → Contacted → Verifying → Term sheet) and CSV export; a second export
  dumps the full screened ranking (top 500) for desk review.
- Ranked **top opportunities** list, filterable per market; one-tap **load all markets**.

Scoring anchors from the company overview: standard 2 h utility-scale battery,
~1 cycle/day; battery import ≈ 8% of hours; firm potential ~30% unfirmed → ~78% firmed;
accelerated route ≈ 12–18 months vs 5–7 years for a conventional firm connection.

## Company overview document

`docs/neura-overview.html` — the complete overview source package consolidated into a
single self-contained file: the canonical A4 document (WeasyPrint it for the PDF; the
appendix is print-hidden), plus the landscape variant, earlier draft, chart generator
scripts, and build notes.

## Features

- Coverage matrix (7 layer types × 10 markets) with lazy market loading
- Headroom threshold filter; line colouring by voltage class or nearby headroom
- Typeahead search over substations + lines (keyboard nav, `/` to focus) with
  `#cc=…&n=…` permalinks
- Spain regional breakdown: ranked list + map bubbles
- Site finder: industrial land within 3 km of ≥100 MW transmission nodes, with
  cadastre / listings / OSM links per site
- Data-freshness strip (flags source editions older than 60 days)
- Resizable + collapsible sidebar, mobile layout, reset-view control

All figures are indicative and **non-additive** — headroom is shared between
neighbouring nodes. Screening data, not confirmed capacity: validate any shortlist
with a formal access request to the operator.
