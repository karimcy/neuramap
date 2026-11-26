# GridQ

GridQ is the **Product-Engineer layer** of the GridQ / Neura behind-the-meter (BTM) battery
(BESS) **sizing and underwriting** engine. You give it the data for **one** commercial site;
it works out **how big a battery makes sense there, what that battery is worth, and whether the
numbers stack up** — and writes that up as three documents that always agree with each other.

> **Plain-English summary.** A factory, cold store or warehouse has a half-hourly electricity
> meter, an electricity tariff, a grid connection, and a landlord/offtaker. GridQ reads all of
> that, simulates running a battery on the site for a year (half-hour by half-hour), picks the
> battery size that earns the most while staying within the rules, runs the finances, and produces
> an underwriting pack, a customer proposal, and a web report — every number traceable back to its
> source.

> ### ⚠️ Honest status (read this first)
> The whole engine is **built, integrated and tested green** — the real half-hourly dispatch
> optimiser, the two-stage continuous sizing search, commitment windows, finance, scenarios,
> the full web product and outputs all run, and the demo site now prices an honestly **viable**
> project (positive levered returns, P90 DSCR covenant passing). An **unviable** site gets the
> other first-class answer: an explicit **no-viable-configuration verdict** with the evidence
> surface preserved — never a manufactured "recommendation" (D39; proven end-to-end on a real
> prospect's transcribed bills in `tests/test_e2e_examples_real.py`, red-team audit in
> [`docs/redteam/`](docs/redteam/AUDIT.md)). **But no quote it produces today is "bankable."**
> The assumption releases it prices against (`gb_v0_1` … `gb_v1_5`) still carry values labelled
> `placeholder` / `needs_review` — research-grounded shapes verified against literature in the
> [assumption register](docs/redteam/ASSUMPTION_REGISTER.md), but not yet sourced and signed
> off, with **nothing `confirmed`**. Every output is therefore stamped **`not_bankable`** on
> purpose, and anything the engine cannot honestly compute is shown **"unavailable"** rather
> than guessed. This is by design: GridQ never invents a number. See
> [Honest limits](#6-operationalising-for-installers--and-the-honest-limits). The web product is
> **UK-only**, and projects are **per-account** (Google sign-in when configured; a private
> per-browser workspace otherwise — D40).
>
> **New in the meeting-readiness drive (issue 018).** The engine now models **consent-gated
> wholesale export** as *gated upside*: when a site's export consents cohere (G99/G100 approved,
> export MPAN, MEC > 0) **and** a half-hourly wholesale price series is supplied — fetched free from
> **Elexon's Insights API** (`gridq.dev.fetch_prices`, no API key) — a separate overlay solve values
> exported energy at those prices as **CONDITIONAL** upside, the incremental total net of any
> behind-the-meter arbitrage it cannibalises. Wholesale £ **never** enters the priced floor, P50/P90
> or DSCR (D43). **Consents are a declared input**, captured in a dedicated "Consents & grid
> arrangements" step in both the guided wizard and the real-site form — **never a backend block**.
> The customer also declares **per-stream consent** there — a checkbox per revenue stream (plus an
> agreed-rate field for Capacity Market / Balancing Mechanism / Ancillary) that authorises us to
> pursue that stream and, with the rate it needs, flips its proposal row to "consent given"; consent
> authorises the pursuit, it is not a guarantee.
> Local flexibility now prices **net of the aggregator cut**. Half-hourly ingestion accepts a bare
> `kwh` column and Excel files. **Robert Thomas Farms** (Haywood Oaks) is the worked real-site
> example: on its own overnight-heavy load it is honestly **not viable as a standalone behind-the-meter
> battery** — only 6.4% of its consumption falls in the expensive red band, so the engine returns a
> `no-viable-configuration` verdict, plainly (F-013); its value case lives in the export / flexibility
> upside and the open data questions (real tariff bands; whether the near-zero midday load is existing
> PV). Assumption releases now run `gb_v0_1 … gb_v1_5`; still **nothing `confirmed`**, so every output
> remains **`not_bankable`** by design.

---

## Contents

1. [What GridQ is and what it does](#1-what-gridq-is-and-what-it-does)
2. [How the pipeline works — every step](#2-how-the-pipeline-works--every-step)
3. [How to use it — CLI, UI, outputs, the two modes](#3-how-to-use-it)
4. [Where data goes — inputs, formats, data tiers](#4-where-data-goes)
5. [How to make changes — assumptions, value streams, contracts](#5-how-to-make-changes)
6. [Operationalising for installers — and the honest limits](#6-operationalising-for-installers--and-the-honest-limits)
7. [Status, roadmap & documentation map](#7-status-roadmap--documentation-map)
8. [Setup, quality bar & team workflow](#8-setup-quality-bar--team-workflow)

The deep operator runbook is [`docs/operating_guide.md`](docs/operating_guide.md). Per-topic
detail lives in [`docs/`](docs/) (linked throughout).

---

## 1. What GridQ is and what it does

GridQ turns **one site's data → three consistent artifacts**:

| Artifact | File | Who it's for | What it is |
|----------|------|--------------|------------|
| **System specification** | inside `quote.json` | engineering | the recommended battery: power (kW), energy (kWh), duration (h) |
| **Underwriting pack** | `memo.md` | capital / lenders | cashflows, debt cover, returns, firm-vs-upside split, provenance |
| **Customer proposal** | `proposal.md` | the site / installer | plain-language offer: system, savings, what's secured vs upside |

(A self-contained `report.html` bundles the essentials of both into one web page.)

Three ideas explain the whole design:

- **One engine, called twice.** The same dispatch optimiser that *searches* for the best battery
  size is run **again** to *underwrite* the size it chose. Because the search and the underwrite use
  the identical engine, GridQ can never recommend a battery that its own underwriting would reject.
  (`src/gridq/sizing/sizer.py` + `src/gridq/sizing/underwrite.py`.)
- **Three artifacts from one record.** All three documents are rendered from a single stamped
  `QuoteRecord`. No document does any arithmetic of its own — they only *display* numbers the engine
  already computed — so they are **consistent by construction** and a re-run from the same record is
  byte-for-byte identical.
- **Deterministic core, agentic perimeter.** The pricing path is pure and reproducible: the same
  inputs + assumption version + code version + seed always give the same output. **No LLM or random
  AI ever touches a number on the pricing path.** AI is allowed only at the edges (helping ingest
  data, drafting prose around the numbers) — never to create or change a figure. See
  [`docs/02_architecture.md`](docs/02_architecture.md).

---

## 2. How the pipeline works — every step

```
  site data  ─►  SiteModel  ─►  value stack  ─►  dispatch MILP  ─►  sizing  ─►  finance  ─►  quote  ─►  memo
 (JSON + HH    (validated,    (which revenue   (half-hourly      (pick the   (cashflows,  (stamped,  proposal
  CSV +         graded         streams are      battery run for   best size)  DSCR, IRR,   hashed     report
  forecast)     T0/T1/T2)      open here)       a whole year)                 price)       record)
```

Each step is one module. Out of the box every step runs; the result is honest but **not bankable**.

1. **Site data in → `SiteModel` (T0 / T1 / T2).** Ingestion validates the site JSON, optionally
   folds in a real half-hourly meter file, and **grades the data**: **T0** = annual kWh + tariff +
   postcode only; **T1** = some half-hourly data; **T2** = a validated 12-month half-hourly series.
   It never invents missing data — gaps become a **ranked list of "data asks."**
   (`src/gridq/ingestion/` · [`docs/ingestion.md`](docs/ingestion.md))

2. **Value stack — which revenue streams are open.** For this site, which of the nine GB revenue
   streams can earn money? Each stream has a **gate** (e.g. wholesale needs export consent + a
   route-to-market partner) and a **firmness** (firm floor / contracted / conditional upside).
   (`src/gridq/value_stack/` · [`docs/value_stack.md`](docs/value_stack.md))

3. **Dispatch MILP — run the battery for a year.** A half-hourly linear optimisation (linopy /
   HiGHS) decides when to charge and discharge to **maximise the site's bill reduction**, while
   respecting the grid connection limit (MIC/ASC), round-trip losses, and the battery's warranty
   cycle cap. It reads the site's **real metered load** and **real tariff**.
   (`src/gridq/optimisation/` · [`docs/dispatch_engine.md`](docs/dispatch_engine.md))

4. **Sizing — pick the battery.** The default is a **two-stage continuous search** (D34): a coarse
   labelled sweep over power × duration scored on a typical-period proxy built from the site's own
   metered year, a seeded deterministic refinement, then **full-series confirmation** of a diverse
   top set plus a bounded compass polish — only confirmed, never-proxy evaluations can be selected,
   and the winner needs the best **levered return** that still clears the **P90 DSCR covenant**
   (falling back to the best *indicative* size when no debt case can be made). The whole evaluated
   cloud persists on the record and becomes the sizing surface the app renders; a dense exact grid
   remains the certification instrument, never the per-request path.
   (`src/gridq/optimisation/two_stage.py` · `src/gridq/sizing/`)

5. **Finance — do the numbers stack up?** Builds P50/P90 cashflows, costs (capex, opex, insurance,
   business rates, levies), debt-service cover (DSCR), project/equity IRR, payback, and a
   price-to-hit-target recommendation. Missing debt or dispatch inputs are reported **"unavailable,"
   never guessed.** (`src/gridq/finance/` · [`docs/financial_model.md`](docs/financial_model.md))

6. **Quote — stamp it.** The optimisation result + financial case are bound into a single immutable
   `QuoteRecord`, hashed and stamped with the input hash, assumption version, code version and seed,
   so it is fully reproducible and auditable. (`src/gridq/quotes/` · `src/gridq/provenance/`)

7. **Memo / proposal / report — write it up.** One record renders the three artifacts; prose is
   LLM-free by default and the HTML does no arithmetic.
   (`src/gridq/outputs/` · [`docs/outputs.md`](docs/outputs.md))

A scenario engine (Low/Central/High price cases, [`docs/scenario_engine.md`](docs/scenario_engine.md))
and a credit→rate/tenor mapping plug into finance; both are deterministic.

### Which revenue streams are backed by real data

Honest status of the data behind each stream. **Today only the wholesale / P415 export stream is
priced from real market data** (live GB half-hourly prices from Elexon). The firm-floor streams run
on the site's **real metered load** and **real GB regulatory time-windows**, but the **£ rates**
(tariff unit rates, DUoS band rates, the CM clearing price) are still **placeholders** — which is
exactly why every output stays `not_bankable` until those assumption values are confirmed with
sourced numbers.

Every stream is **consentable-and-priced**: the customer authorises it at intake (`stream_consents`,
with any rate it needs) and the "Consent given" label is stamped independently of whether the gate
opens. The gated streams (Capacity Market, local flex, wholesale/P415, BM, ancillary) price only as
**CONTRACTED** with consent **and** a rate — **never FIRM**; consent is authorisation, not value, and
volumes are always engine-measured (no rate or no volume ⇒ £0, still labelled "consent given").

| Revenue stream | Role | Consentable-and-priced | Real vs placeholder today | Real-data source · URL |
|----------------|------|------------------------|---------------------------|------------------------|
| **TARIFF_ARBITRAGE** | firm floor | Consent stamped; £ is load-derived (not consent-gated). | **Real:** metered load + GB tariff time-structure. **Placeholder:** import £/kWh unit rates. | Load = the site's meter; rates = the site's supplier contract/invoice (not yet extracted) |
| **DUOS_RED_BAND_AVOIDANCE** | firm floor | Consent stamped; £ is load-derived (not consent-gated). | **Real:** DUoS red/amber/green **band time-windows** (regulatory). **Placeholder:** the £/kVA band **rates**. | Windows = the DNO Use-of-System charging statement; rates = the site's DNO DUoS schedule |
| **MIC_ASC_HEADROOM** | firm floor | Consent stamped; £0 by construction (value realised via connection-bounded arbitrage). | **Real:** connection capacities when the agreement is supplied. | The site's DNO connection agreement (MIC / ASC) |
| **PPA_OR_BILL_SAVINGS** | firm floor | Consent stamped; aggregate £0 (itemised across arbitrage + DUoS). | Derived from load + tariff (inherits the placeholder tariff rates). | Same as TARIFF_ARBITRAGE |
| **CAPACITY_MARKET** | conditional | Consent + agreed £/kW/yr ⇒ priced at the agreed rate; stays **CONDITIONAL** (deliverability physics hold it out of the floor). | **Real:** NESO **de-rating factors** (by duration). **Placeholder:** the **clearing price** (£/kW/yr); a customer-agreed rate wins when consented. | De-rating = [EMR Delivery Body / NESO](https://www.emrdeliverybody.com/) Electricity Capacity Report; price = the T-1/T-4 auction result |
| **LOAD_SERVICE** | conditional | Consent stamped; priced from declared provisioning requirements only (£0 without one). | **Real:** derived from the metered load (firming / back-up value). | The site's meter |
| **LOCAL_FLEX_PICLO** | conditional | Consent + agreed £/MWh (on `ExportArrangement`) ⇒ priced net of the aggregator cut; £0 until a tender is open (engine-measured volume). | **Placeholder:** priced only on a **manually confirmed** tender; aggregator cut defaults to 20% (an input). | [Piclo Flex](https://picloflex.com/) — manual tender lookup; **no open API** |
| **WHOLESALE_P415** | **gated upside** (+ contracted floor) | Consent + declared export tariff (£/kWh) ⇒ a **CONTRACTED** floor entry (agreed tariff × overlay export MWh); the Elexon-MID perfect-foresight delta stays **separate indicative gated upside, never contracted, never FIRM**. | **Real: live GB half-hourly wholesale prices.** Export overlay gated on export consent (G99/G100) + MEC. | **[Elexon Insights — MID dataset](https://data.elexon.co.uk/bmrs/api/v1/datasets/MID)** (free, no API key) |
| **BALANCING_MECHANISM** | gated upside | Consent + agreed £/MW/yr ⇒ **CONTRACTED** (agreed rate × engine-measured de-rated deliverable MW); £0 without a rate; never bankable/FIRM. | **Placeholder / £0** without a customer-agreed rate. | Needs the RTM / aggregator partner feed (no public per-asset BM data) |
| **ANCILLARY_DC_DM_DR** | gated upside | Consent + agreed £/MW/yr ⇒ **CONTRACTED** (agreed rate × engine-measured de-rated deliverable MW); £0 without a rate; never bankable/FIRM. | **Placeholder / £0** without a customer-agreed rate. | [NESO ancillary auctions](https://www.neso.energy/) (DC / DM / DR) via an aggregator; not wired |

**Consent.** The conditional / gated streams (Capacity Market, local flex / Piclo, wholesale /
P415, balancing mechanism, ancillary) are now **consentable-and-priced**: when the customer
explicitly authorises a stream at intake **and** supplies the rate it needs, it is priced into the
floor as **CONTRACTED** (never FIRM) with its P90 haircut + contracted-basis DSCR, and the
customer-facing outputs say **"Consent given"** against it. Consent is authorisation, not a
revenue guarantee — a consented stream without its needed rate still prices £0 and stays gated,
volumes are always engine-measured, and consent never elevates a stream's firmness (a gated
stream can never be FIRM). The firm-floor streams above are load-derived and are not consent-gated.

**Input data, separately.** The **half-hourly load** is real for any supplied meter file (e.g. Robert
Thomas Farms, from its SmartestEnergy export); the only synthetic series in the product is the
bundled **demo cold-store** load. The **wholesale price series** is real GB data pulled from the
Elexon MID endpoint (`python -m gridq.dev.fetch_prices`, off the pricing path). When a supplied price
series does not fully cover the metered horizon the engine **refuses the overlay rather than
inventing prices** — there is **no synthetic-price fallback on the pricing path**.

---

## 3. How to use it

### Install

Requires **Python 3.11+**.

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"              # engine + dev tools (pytest, ruff, mypy)
pip install -e ".[app]"              # add this if you also want the demo web UI
```

### Run a quote from the command line

The end-to-end command is `gridq run` (or `python -m gridq.cli run`):

```bash
# 1. Run a bundled example site. NOTE: the examples carry T2 metadata but the
#    referenced meter file is NOT in the repo, so this honestly degrades to
#    "insufficient_data / not_bankable" and prints the ranked data asks.
gridq run examples/site_t2.json

# 2. A PRICED run: supply a real half-hourly meter CSV. The MILP now has a load
#    series to optimise and produces a real firm-revenue floor (still not_bankable
#    on placeholder assumptions, but with real numbers).
gridq run examples/site_t2.json --hh path/to/your_meter_12months.csv --out runs/acme

# 3. Price against the current head release (what the web app uses) instead of the default
gridq run examples/site_t2.json --hh your.csv --assumptions gb_v1_3

# 4. Request the wholesale/P415 upside (kept as gated upside; lists the forecast
#    file as a data-ask if you don't supply one)
gridq run examples/site_t2.json --hh your.csv --forecast modo_2025.csv --forecast-source Modo --wholesale

gridq render runs/acme/quote.json --site examples/site_t2.json   # re-make the 3 docs from a saved quote
gridq info        # list the modules and what they do
gridq version
```

Useful flags: `--hh` (real meter CSV), `--forecast`/`--forecast-source`/`--forecast-version`
(wholesale price file — gated upside only), `--assumptions` (default `gb_v0_1`), `--wholesale`,
`--out` (default `runs/<quote_id>/`), `--json`, `--contributor`. Full reference:
[`docs/operating_guide.md`](docs/operating_guide.md).

### Run the web UI

```bash
python -m gridq.app          # then open http://127.0.0.1:8000
```

The app is the **full product surface** — server-rendered FastAPI + Jinja2 with vanilla JS, no
build step, all assets vendored (works offline), on a dark "instrument panel" design system:

- **Landing** — a WebGL hero that draws the newest solved run's **real** sizing surface (vendored
  Three.js, beacon at the actual chosen optimum; falls back to a static SVG when there's no solved
  run, on narrow viewports, under software GL, or with reduced motion), live metrics that count up
  from real record values, how-it-works, markets.
- **New Quote wizard** (`/new-quote`) — five steps (site, consumption, load profile, equipment,
  review) with a dark-cartography map picker, drag-and-drop zones for the meter CSV / bill, live
  validation, and a gated run: nothing executes until the required inputs are valid.
- **Run console** (`/run/<quote_id>`) — the stamped record rendered as live console panels: the 3D
  sizing surface beside its heat-map (confirmed vs proxy points disclosed), dispatch, utilisation,
  finance (draw-on-scroll cashflow with the payback year marked; the firm-vs-gated value stack),
  provenance, every warning by name, spec / artifacts / telemetry tabs, and a "sharpen this
  quote" panel to re-run with better data.
- **Projects / Equipment / Review** — the run list; the equipment catalogue, which you can
  **extend with your own products** ("Upload spec sheet": type the specs and, optionally, attach
  the manufacturer datasheet as provenance — nothing is parsed off the file, so the catalogue
  stays honest and display-only); and a red-team review screen that ranks every outstanding issue
  (blockers first, with owners).
- **Two modes**, both live: **real-site** (primary — your data, ranked data-asks for whatever is
  missing) and **demo** (one click, a clearly-labelled illustrative dataset end to end).

Every figure on every screen renders from the stamped record via `fmt_*` helpers — **the
interface formats, it never computes or invents a number** (guard-tested). The design system is
codified in [`design-system/gridq/MASTER.md`](design-system/gridq/MASTER.md) (single token source
`src/gridq/app/static/tokens.css`, anti-pattern list, pre-delivery checklist; see also CLAUDE.md
§6a and [`docs/goal/redesign-report.md`](docs/goal/redesign-report.md)).

### Read the outputs

A run writes five files to the output directory:

| File | What's in it |
|------|--------------|
| `quote.json` | the full stamped `QuoteRecord` (the source of truth) |
| `telemetry.json` | one telemetry event (data tier, missing-data count, outcome) |
| `memo.md` | underwriting pack — spec, P50/P90 cashflows, DSCR, firm-vs-upside split, returns, provenance, **every warning by name** |
| `proposal.md` | customer proposal — system, offer, secured-vs-upside, next steps |
| `report.html` | self-contained web page combining the essentials |

Every document carries a **NOT BANKABLE** banner while it rests on placeholder assumptions, and
labels any figure it can't yet compute as **"unavailable"** (with the reason) rather than showing a
zero or a guess.

### Indicative vs underwritten (what those words mean here)

These are **lifecycle stages** of a quote, tracked on `quote_mode`:
`draft → indicative → underwritten → contracted → operating → lapsed`. A fresh `gridq run` produces
a **`draft`** quote; advancing it (e.g. to `underwritten`) is a deliberate, recorded transition
(`src/gridq/quotes/lifecycle.py`), not something the data quality flips automatically.

Crucially, **the lifecycle stage is *not* what makes a quote bankable.** Bankability is gated on the
**assumption status** — a quote can only become a `bankable_candidate` when the numbers behind it are
`confirmed` (and a real debt case exists). Today nothing is confirmed, so every quote — at any
lifecycle stage — is `not_bankable`. "Indicative" = an early, banded view; "underwritten" = the
firm, fully-modelled view you'd take to capital *once the assumptions are real*.

---

## 4. Where data goes

GridQ accepts **four kinds of input**, all documented field-by-field in
[`docs/input_data_formats.md`](docs/input_data_formats.md). Real third-party data (meter files,
forecasts) belongs under a **gitignored `data/`** path — never committed.

### a) The site description — `SiteModel` JSON
A single JSON object (schema `src/gridq/schemas/site.py`, strict — unknown fields rejected). It holds
the tariff stack, the connection (`mic_kw` / `mec_kw` / `asc_kw`, voltage, DNO region), consents
(G98/G99/G100, export MPAN), physical gates, and the counterparty (legal name, credit band).
Examples: [`examples/site_t0.json`](examples/site_t0.json) (annual-only) and
[`examples/site_t2.json`](examples/site_t2.json) (12-month). Load it as-is, or build it from your own
data following the example shape.

### b) The half-hourly meter file — CSV (via `--hh`)
The site's real metered series. Columns are matched case-insensitively: **`timestamp`** (required)
plus **`import_kw`** *or* **`import_kwh`** (one required); optional `export_kw`/`export_kwh`,
`site_load_kw`, `meter_id`, `quality_flag`. 30-minute periods (48/day; 17,520/year for T2).

```csv
timestamp,import_kw,export_kw,meter_id,quality_flag
2025-01-01T00:00:00,118.4,0,1200001234567,A
2025-01-01T00:30:00,121.0,0,1200001234567,A
```

> **How the meter file actually reaches the engine.** The dispatch MILP reads the series from the
> site's `load_data.half_hourly_file_ref`. Passing `--hh your.csv` points that reference at your
> file (and re-grades the tier). The bundled examples point at a file under `data/` that isn't in
> the repo — which is exactly why they degrade to *insufficient_data* until you supply a real series.
> Missing intervals are **counted, never back-filled**; negative readings flagged; nothing invented.

### c) The wholesale/Modo price forecast — CSV or JSON (via `--forecast`)
A third-party price curve used **only for the gated wholesale (P415) upside — never the priced
floor.** Required columns: `timestamp`, `price_gbp_per_mwh` (may be negative); optional `scenario`,
`source`, `forecast_version`, `created_at`. Forecasts are **referenced, not stored as assumptions**:
GridQ pins each one by source + version + a content hash so a quote can cite the exact curve.

### d) Tariff / connection / consents / counterparty
These live **inside the `SiteModel`** (not separate files) — see (a).

### What each data tier unlocks

| Tier | You provide | You get |
|------|-------------|---------|
| **T0** | annual kWh + tariff + postcode | a banded, **indicative** view; no half-hourly dispatch; ranked data-asks |
| **T1** | some half-hourly data | partial load shape; better than T0, still under-qualified |
| **T2** | validated ~12-month half-hourly (≥90% complete) | full dispatch MILP, all floor streams priced, the basis for an **underwritten** quote |

Even at T2 the quote stays `not_bankable` until the **assumptions** are confirmed — the tier governs
data quality; the assumption status governs bankability. Contracts & tiers:
[`docs/03_data_contracts.md`](docs/03_data_contracts.md).

---

## 5. How to make changes

### The assumption library (where the numbers live)
Every underwriting number lives in versioned YAML — never as a constant in code — and is read **only**
through `gridq.assumptions.load_assumptions(version)`. A release is one frozen file under
[`assumptions/`](assumptions/): `gb_v0_1` (the original placeholders) through `gb_v1_3` (the
current head: sourced-shape successor line, decomposed power + energy capex per C025, availability
/ penalty / TNuOS content from the north-star waves). **The web app prices on `gb_v1_3`; the CLI
defaults to `gb_v0_1`** (override with `--assumptions gb_v1_3`). Each release has sections (capex, opex,
efficiency, tariff arbitrage, DUoS, capacity market, …) and every entry carries **8 required fields**:

```yaml
battery_capex:
  installed_cost:
    value: 500000
    unit: GBP/MW
    source: "Neura business plan: 'roughly £500k per MW'."   # never blank
    status: needs_review        # placeholder → needs_review → confirmed
    owner_or_reviewer: Oli
    confidence: low
    last_reviewed: 2026-06-24
    notes: "Confirm against real installer quotes."
```

### To update a value and its provenance
**Never edit a released file in place** — releases are frozen for reproducibility (existing quotes
stay pinned to the version that made them). Instead:
1. Copy the latest release to a **new versioned file** (e.g. `assumptions/gb_v1_1.yaml`) and bump
   `metadata.version`.
2. Change the `value`, cite a real `source`, set `status`/`confidence`, update `last_reviewed`.
3. A value only becomes bankable when its `status` is `confirmed`. Run against the new version with
   `--assumptions gb_v1_1`.

Gaps and the public source to confirm each one are ranked in
[`docs/assumptions_to_source.md`](docs/assumptions_to_source.md); the rules are in
[`docs/assumption_library.md`](docs/assumption_library.md).

### To add a value stream
1. Add the stream to the `MarketStream` enum (`src/gridq/schemas/enums.py`); if it's upside that must
   never enter the floor, add it to `GATED_UPSIDE_STREAMS` (`src/gridq/schemas/market.py`).
2. Implement its gate + value function in `src/gridq/value_stack/gb_streams.py` and register it.
3. Add the matching assumption section(s) to the library YAML.
4. Bump `SCHEMA_VERSION` and update the conformance tests
   (`tests/test_optimisation_contract_conformance.py`).

### Where contracts are frozen
`SiteModel` (input) and `OptimisationResult` (output) are the **frozen contracts** that decouple the
two sides of the codebase (the underwriting platform and the optimisation engine). Changing either is
a **deliberate, versioned event** that must trip the conformance tests — not a silent edit. The
market-agnostic core never branches on `market == "GB"`; GB specifics are content in the assumption
library only.

---

## 6. Operationalising for installers — and the honest limits

### The intended installer workflow

1. **Submit a site.** An installer provides what they have — often just annual kWh, tariff, postcode,
   connection and counterparty (a **T0** site).
2. **Get an indicative quote + ranked data-asks.** GridQ returns an honest banded view *and* a
   prioritised list of what to add to sharpen it (`SiteModel.missing_data`, built by
   `src/gridq/ingestion/improvement_asks.py`). Typical order: **12 months of half-hourly meter data**
   (rank 1), then connection capacities, export consents, supplier consent, counterparty credit.
3. **Improve the data.** The installer supplies the asks — above all a real 12-month meter file —
   which upgrades the site to **T2** and lets the dispatch MILP price the firm floor.
4. **Re-run → an underwritten-quality quote.** With real load + tariff the engine prices a real
   firm-revenue floor and full finance; the quote can be advanced to `underwritten`.
5. **Bankable only when assumptions are confirmed** — see below.

### Honest limits (what is *not* real yet — never hidden)

GridQ is deliberately conservative; it labels everything that isn't firm:

- **Nothing is bankable yet.** No assumption release has a single `confirmed` value. Every quote is
  `not_bankable` and says so, with each placeholder named in the warnings. A quote becomes bankable
  only when the assumptions it used are `confirmed`.
- **Gated upside is never in the floor (blocker B002).** Wholesale/P415, the Balancing Mechanism, and
  ancillary services (DC/DM/DR) stay **conditional, valued £0**, until a route-to-market partner/API
  and export consents are known. They show as upside with their unlock conditions — never as secured
  revenue. Availability-style commitments that *are* modelled are enforced against the per-instant
  deliverable envelope (a kW is never double-sold). (`docs/shared_work/BLOCKERS.md`.)
- **Debt cover / DSCR runs on labelled placeholder gearing (blocker B001 half-open).** From
  `gb_v1_2` the credit→rate/tenor mapping plus a library gearing placeholder assemble real
  `DebtTerms`, so DSCR, the P90 covenant gate and debt sculpting genuinely fire — but the gearing is
  an illustrative placeholder pending Sim's portfolio-finance workstream, so every debt case stays
  `not_bankable`. On older releases DSCR is still reported *unavailable*, never guessed.
- **`needs_review` / `placeholder` assumptions** are usable so the pipeline runs end-to-end, but are
  always labelled and never presented as confirmed.
- **The customer bill is computed only when its inputs are real.** The merged bill
  (before/after/savings) renders from the persisted record where the tariff + dispatch inputs
  support it, and is shown as honestly unavailable otherwise (e.g. under MPC dispatch).

---

## 7. Status, roadmap & documentation map

**Status (2026-07-04): engine + product complete through the merged north-star waves.** The real
MILP is the default everywhere (gap-segmented state-of-energy, timezone-correct banding); the
**two-stage continuous sizing search is live as the default** (D34 — bake-off-verified against the
dense grid at ~4× less wall time); capex is decomposed into power + energy components (C025 /
`gb_v1_3`) so duration finally costs real capital; each physical cost is charged **exactly once**
(D36 — the levy and degradation double-counts are gone, which is what turned the demo honestly
viable: positive levered returns with the P90 DSCR covenant passing, still `not_bankable` on
labelled placeholders); W7 availability commitments are enforced against the per-instant
deliverable envelope; and the web app is the full two-mode product on a codified dark design
system with a WebGL hero of the real sizing surface (Lighthouse 99 desktop / 96 mobile, WCAG AA,
zero template arithmetic). The whole tree is green (835+ tests, deterministic outputs).
Live milestone table: [`docs/project_status.md`](docs/project_status.md).

| Doc | What it is |
|-----|------------|
| [`docs/operating_guide.md`](docs/operating_guide.md) | **the operator runbook** — deep how-to for everything in §3–§6 |
| [`CLAUDE.md`](CLAUDE.md) | operating rules for working in this repo |
| [`SKILLS.md`](SKILLS.md) | procedural "how to build here" memory |
| [`docs/00_project_boundary.md`](docs/00_project_boundary.md) | scope & ownership (what this is / is not) |
| [`docs/01_project_spec.md`](docs/01_project_spec.md) · [`docs/02_architecture.md`](docs/02_architecture.md) | spec, module map, milestones · stack, contracts, determinism |
| [`docs/03_data_contracts.md`](docs/03_data_contracts.md) · [`docs/input_data_formats.md`](docs/input_data_formats.md) | field-level contracts · exact input formats |
| [`docs/ingestion.md`](docs/ingestion.md) · [`docs/value_stack.md`](docs/value_stack.md) · [`docs/dispatch_engine.md`](docs/dispatch_engine.md) | per-module depth |
| [`docs/financial_model.md`](docs/financial_model.md) · [`docs/scenario_engine.md`](docs/scenario_engine.md) · [`docs/outputs.md`](docs/outputs.md) | per-module depth |
| [`docs/assumption_library.md`](docs/assumption_library.md) · [`docs/assumptions_to_source.md`](docs/assumptions_to_source.md) | the number library · the gaps to fill |
| [`docs/project_status.md`](docs/project_status.md) · [`docs/decisions.md`](docs/decisions.md) · [`docs/changelog.md`](docs/changelog.md) | status · decisions · history |
| [`docs/manual/`](docs/manual/) | **the 144-page Platform Manual** (17 chapters + glossary + library reference; LaTeX, with per-chapter "how to rebuild this" recipes) |
| [`design-system/gridq/MASTER.md`](design-system/gridq/MASTER.md) · [`docs/goal/redesign-report.md`](docs/goal/redesign-report.md) | the frontend design system (tokens, anti-patterns, checklist) · the redesign report with before/after screenshots |

---

## 8. Setup, quality bar & team workflow

```bash
pytest                 # tests (MILP suites: run with TZ=UTC OMP_NUM_THREADS=1, never in parallel)
ruff check .           # lint
ruff format --check .  # format
mypy                   # typecheck
gridq                  # run (or: python -m gridq.cli)
```

Everything must pass, and outputs must stay deterministic, before a change is "done." UI changes
additionally run the pre-delivery checklist in
[`design-system/gridq/MASTER.md`](design-system/gridq/MASTER.md) (the design-system guard tests
enforce the load-bearing parts: single token source, self-hosted fonts, no template arithmetic,
no em dashes in UI copy).

**Olly** maintains this codebase; the platform/engine split is a structural hint, not
a wall. Session continuity runs through [`docs/shared_work/`](docs/shared_work/) and the dashboard:

```bash
python -m gridq.dev.team_sync     # active plan, handoffs, open comments, blockers
```

Before starting, read [`docs/shared_work/ACTIVE_PLAN.md`](docs/shared_work/ACTIVE_PLAN.md) and
[`docs/shared_work/BLOCKERS.md`](docs/shared_work/BLOCKERS.md); when you stop, log a handoff. How to
work here is in [`SKILLS.md`](SKILLS.md).
