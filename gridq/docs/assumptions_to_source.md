# Assumptions to source — ranked gap list (GB)

The work-list to turn `gb_v1_0` from a mostly-placeholder release into a confirmable one.
Created with the `gb_v1_0` provenance pass (W4-3, 2026-06-24, Olly). Ranked by **impact on the
bankable floor and DSCR** — i.e. what most moves the priced, contracted numbers a lender relies
on — *not* by how easy each is to source.

**How to use:** pick the highest-rank open gap, find its value with the named **authoritative
source**, set it in a *new* assumption release (never edit `gb_v1_0` in place — bump the version),
set `status: confirmed`, cite the source inline in the `source` field, raise `confidence`, and
update `last_reviewed`. Confirming any value bumps the library version (see
[`assumption_library.md`](assumption_library.md)).

**What this list is, under D45 (Olly, 2026-08-12 — [`decisions.md`](decisions.md)): a defect backlog,
not a resting state.** Every open gap below is a key the pipeline may read, and an unsourced key that
reads as `0.0` on the pricing path is not an "unknown" — it is a confidently wrong number, and it
enters P50, P90 and the DSCR looking exactly like a measured one. Where the **authoritative source**
column names a *published* GB figure (a DNO CDCM charging statement, NESO de-rating factors, an ENA
settlement formula, an OBR index), going and transcribing it is **sourcing, not invention** — do that
rather than leave the key null. Where the search genuinely fails, the key stays `needs_review` with
the search recorded and the affected £ is **refused, never silently zeroed**.

## Where `gb_v1_0` stands

- **25 entries, 0 confirmed.** Nothing here is bankable.
- **2 carry a real in-repo citation** (the Neura business plan) and are promoted to `needs_review`:
  `battery_capex.installed_cost` (£500k/MW) and `financing.target_equity_irr_pct` (12%, low end of
  the cited 12–13% band). Both are *indicative* plan figures — they still need confirming.
- **10 `needs_review`, 15 `placeholder`.** Every un-cited number was **carried forward unchanged**
  from `gb_v0_1` and re-labelled honestly — none was invented.
- **Provenance fix:** `round_trip_efficiency.ac_round_trip` (0.88) was cited in `gb_v0_1` as
  `system_requirements (≈12% round-trip loss)`, but the source document states **no** such figure.
  In `gb_v1_0` it is re-labelled **NOT sourced** (industry-typical placeholder) and listed below.

## A rule that bounds this list

Per [`SKILLS.md`](../SKILLS.md) §6, **external market forecasts are *referenced, not stored* as
assumptions** — pinned by `source` + `forecast_version` + content hash under gitignored `data/`,
never baked into the library as a single invented constant. So the volatile, per-cycle market
series below (CM clearing price, CM de-rating factors, DUoS bands, DC/DM/DR, wholesale/BM) are
listed here with their authoritative source **to confirm**, but are expected to be *referenced* by
the engine rather than frozen as library scalars. That is why none was web-scraped into `gb_v1_0`.

**Referenced-not-stored governs *where* a value lives, not *whether* it exists at run time (D45).** It
is never a licence for a null. A reference that no run resolves leaves the key absent, and an absent
DUoS band prices `DUOS_RED_BAND_AVOIDANCE` — a **priced-floor** stream — at £0. So either the reference
resolves to a real value for the site being underwritten (its own DNO CDCM band rates, transcribed onto
its `TariffStack`) or the £ is refused. `gb_v1_6` is the pattern for the library side: fetched values
transcribed with a hashed provenance sidecar, never invented constants.

## Ranked gaps

### Tier 1 — moves the floor and DSCR the most

| # | Assumption (section.key) | `gb_v1_0` now | Why it matters | Authoritative source to confirm | Owner | Ref |
|---|--------------------------|---------------|----------------|----------------------------------|-------|-----|
| 1 | `battery_capex.installed_cost` | £500k/MW, needs_review (Neura plan) | Drives capex, project IRR and every DSCR — the single biggest lever | Real installer/EPC quotes; a capex benchmark library (e.g. BNEF BESS cost survey) | Oli | — |
| 2 | `financing.debt_gearing_pct`, `debt_interest_rate_pct`, `debt_tenor_years` | **keys absent** | DSCR is **unavailable** with no debt structure — the bankable floor cannot exist | Sim (portfolio finance) for gearing; M9 credit-score→rate/tenor mapping for per-site rate/tenor | Oli ↔ Sim | C008, B001 |
| 3 | `dscr_covenants.min_p90_dscr` | 1.30, placeholder (illustrative) | Sets the lender pass/fail line the optimiser cannot violate | A real term sheet / lender covenant | Oli ↔ Sim | C008 |
| 4 | `financing.target_equity_irr_pct` | 12%, needs_review (Neura plan band 12–13%) | The hurdle the price must clear | Sim's confirmed portfolio cost of capital (this is a target, not Sim's WACC) | Oli ↔ Sim | — |
| 5 | `p50_p90_haircuts.*` (p90 revenue, perfect-foresight) | 15% / 20% | Define the P90 case debt is sized on | M7 calibration / backtests against realised dispatch | Olly (perfect-foresight), Oli (p90) | — |

### Tier 2 — the import-side firm floor (BTM)

| # | Assumption | `gb_v1_0` now | Why it matters | Authoritative source | Owner | Ref |
|---|------------|---------------|----------------|----------------------|-------|-----|
| 6 | `tariff_arbitrage.indicative_value_gbp_per_mw_yr` | £30k/MW/yr | Import-side bankable floor; site-derived in practice | Site half-hourly load + full tariff stack (M0 ingestion); not a fixed library number | Olly | — |
| 7 | `duos.red_band_avoidance_gbp_per_mw_yr` | £18k/MW/yr | DUoS red-band avoidance, post-TCR | DNO Use-of-System charging statements / Ofgem CDCM (per-DNO, per-year — referenced not stored) | Olly | C007 |
| 8 | `mic_asc_headroom.value_gbp_per_mw_yr` | £12k/MW/yr | Value where the connection binds | Site connection data (MIC/ASC) — site-derived | Olly | — |

### Tier 3 — the opex floor (silently flatters every quote if wrong)

| # | Assumption | `gb_v1_0` now | Why it matters | Authoritative source | Owner | Ref |
|---|------------|---------------|----------------|----------------------|-------|-----|
| 9 | `business_rates.annual_gbp_per_mw` | £8k/MW/yr | Recurring cost; must be confirmed before it flatters every quote | VOA rateable value for BESS × the gov.uk non-domestic multiplier (RV is a specialist valuation; the multiplier is public) | Oli | — |
| 10 | `battery_opex.annual_fixed_opex` | £12k/MW/yr | Fixed O&M floor | Real O&M contracts / benchmark library | Oli | — |
| 11 | `insurance.annual_pct_of_capex` | 0.5% | Recurring cost | Insurance broker quote | Oli | — |
| 12 | `metering_idno.annual_gbp_per_site` | £1.2k/site/yr | Standing cost | Meter operator (MOP) / IDNO standing-charge schedule | Oli | — |
| 13 | `dno_costs.annual_gbp_per_site` | £1.5k/site/yr | Standing cost | DNO operating-cost schedule | Oli | — |

### Tier 4 — engine physics inputs (Olly-owned; from OEM datasheets)

| # | Assumption | `gb_v1_0` now | Why it matters | Authoritative source | Owner | Ref |
|---|------------|---------------|----------------|----------------------|-------|-----|
| 14 | `round_trip_efficiency.ac_round_trip` | 0.88, **NOT sourced** | Losses bought at the faced price; affects every dispatch | Cell/inverter OEM datasheet (provenance correction — see top) | Olly | — |
| 15 | `degradation.annual_capacity_fade_pct` | 2.5%/yr | End-of-life sizing (no augmentation) | OEM datasheet + warranty fade curve | Olly | — |
| 16 | `warranty_cycle_limits.max_cycles_per_year_by_duration_h` | {1h:548,2h:365,4h:250} | Hard throughput caps | OEM warranty schedule | Olly | — |
| 17 | `parasitic_load.standby_kw_per_mw` | 5 kW/MW | HVAC/BMS draw billed at host tariff | OEM / HVAC-BMS datasheet | Olly | — |

### Tier 5 — contracted upside (de-rated, deliverability-gated; NOT the floor)

| # | Assumption | `gb_v1_0` now | Why it matters | Authoritative source | Owner | Ref |
|---|------------|---------------|----------------|----------------------|-------|-----|
| 18 | `capacity_market.clearing_price_gbp_per_kw_yr` | £30/kW/yr, needs_review | Contracted upside; only priced if deliverability holds | NESO / EMR Delivery Body auction results (per-auction forecast/series — referenced not stored) | Oli | C007 |
| 19 | `capacity_market.de_rating_factor_by_duration_h` | {1h:0.40,2h:0.65,4h:0.95}, needs_review | De-rates the CM contribution by duration | NESO / EMR Delivery Body published de-rating factors (re-issued per auction cycle) | Oli | C007 |
| 20 | `local_flex_piclo.utilisation_price_gbp_per_mwh` | £150/MWh, needs_review | Priced only when a tender is open in the site's DNO area | Piclo / DNO flexibility tender results (per-area, time-varying) | Oli | C007 |

### Tier 6 — gated upside (£0 until an RTM partner; never in the floor)

| # | Assumption | `gb_v1_0` now | Why it matters | Authoritative source | Owner | Ref |
|---|------------|---------------|----------------|----------------------|-------|-----|
| 21 | `wholesale_p415.gated_upside_value_gbp_per_mw_yr` | £0, gated | Conditional upside only | Route-to-market partner / API; never populate from a forecast | Olly (with RTM partner) | C007, B002 |
| 22 | `balancing_mechanism.gated_upside_value_gbp_per_mw_yr` | £0, gated | Conditional upside; needs export consents (G99/G100) | RTM partner + export consents | Olly (with RTM partner) | C007, B002 |
| 23 | `ancillary_services.dc_dm_dr_value_gbp_per_mw_yr` | £0, gated | DC/DM/DR with decay; needs aggregated portfolio MW | NESO ancillary auction reports + min portfolio MW | Olly (with RTM partner) | C007, B002, C012 |

### Tier 7 — structural / commercial-policy defaults (labelled, not market figures)

| # | Assumption | `gb_v1_0` now | Why it matters | Authoritative source | Owner | Ref |
|---|------------|---------------|----------------|----------------------|-------|-----|
| 24 | `quote_validity.indicative_validity_days` | 30 days | How long an indicative quote stays valid | Internal commercial policy | Oli | — |
| 25 | `scenario_weights.central_low_high` | {0.25/0.50/0.25} | Low/Central/High probability weights (sum to 1.0) | M7 calibration / internal modelling policy | Oli | — |

## Coordination notes (do not lose these at merge)

- **Debt structure is being added in parallel.** The `agent/counterparty` lane has an *uncommitted*
  `financing.credit_rate_tenor_policy` entry (a deterministic credit-band → rate/tenor mapping, M9)
  in its working-tree copy of `gb_v0_1.yaml`. `gb_v1_0` was built from the **committed, frozen**
  `gb_v0_1` and deliberately does **not** absorb that in-flight work. Whoever cuts the next release
  must reconcile: fold the confirmed M9 mapping + the Tier-1 #2 debt keys into the `financing`
  section of that release (a versioned change), resolving **C008**.
- **Frontend traceability (Olly's W4-3 directive).** Every value used in a calculation must show
  *where it came from* in any output/frontend. The `source` field now carries a display-ready
  citation for every entry, but the outputs layer currently threads only run-level
  `assumption_version`, **not** per-`Assumption.source`. Filed as **C020** for the outputs +
  frontend lanes.
- **Forecast sources** (Modo wholesale format, etc.) are tracked separately in **C012**.
