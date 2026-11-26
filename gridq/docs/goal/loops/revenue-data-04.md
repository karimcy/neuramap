# Revenue-Data Drive — Loop 04 (perimeter fetchers + the gb_v1_6 fetcher-backed release)

**Branch:** `agent/revenue-data-drive`. Slices 1–3 committed (consent spine, gate/firmness + pricing/placement, intake). This loop is **slice 4** — the data-fetcher + versioned-release leg of the build sequence (loop-01 step 3 / build-sequence item 4).

**Goal:** stand up off-the-pricing-path perimeter fetchers for the sourced revenue leaves, RUN THEM LIVE, and land a `gb_v1_6` release transcribing the real fetched numbers with committed provenance — no invented values; the golden untouched (app stays `gb_v1_4`).

## 4a — fetchers (`gridq.dev`, mirror the 018 `fetch_prices` pattern)
- `_fetch_common.py` — shared `sha256_hex`, `ProvenanceSidecar` (`{source_url, dataset_id, fetched_utc, licence, sha256, size_bytes, fetcher, fetcher_args, record_count}`), a deterministic JSON writer, and the single `http_get_bytes` network seam (bytes, for JSON + XLSX/CSV). `fetch_prices.py` untouched.
- `fetch_neso_ckan.py` — keyless NESO Data Portal CKAN. `--resource cm-derating|cm-clearing|eac-results`. Pure transforms: latest-T-4 storage de-rating (1h/2h/4h), £/kW/yr clearing = ForecastCost/(CapacityAwarded×1000) (latest T-4 value + latest T-1 context), DC/DM/DR mean £/MW/h × 8760 (register §10). Writes gitignored artifact + sidecar; prints suggested YAML.
- `fetch_piclo.py` — public Piclo Data Hub S3. `--dataset competitions|bids|lcm`. Competitions parsed via pandas → GB-wide utilisation median + availability median; bids/lcm download+hash+row-count only.
- Tests: captured fixtures under `tests/fixtures/` (NESO JSON + tiny Piclo XLSX/CSV), the network seam monkeypatched, ZERO live HTTP; transform determinism, malformed→skipped-never-filled, failure⇒no artifact, sidecar byte-determinism under a fixed `fetched_utc`.

## 4b — the gb_v1_6 release (fetched LIVE 2026-07-20)
Copy of `gb_v1_5`, priors carried forward UNCHANGED, header/metadata rewritten. Four leaves re-sourced from live fetches, each `needs_review`, source citing URL + sha256(12) + fetched-date + the committed sidecar (`assumptions/provenance/gb_v1_6/`):

| Leaf | gb_v1_5 → gb_v1_6 | Derivation |
|------|-------------------|------------|
| `capacity_market.clearing_price_gbp_per_kw_yr` | 30.0 → **27.1** | latest T-4 (DY 2029/30); T-1 = 5.0 in notes |
| `capacity_market.de_rating_factor_by_duration_h` | {1:0.1047,…} → **{1:0.1099, 2:0.2198, 4:0.4396}** | latest T-4 primary NESO de-rating; replaces modoenergy secondary |
| `ancillary_services.dc_dm_dr_value_gbp_per_mw_yr` | 0 → **28296.9** | mean 3.2302 £/MW/h across DC/DM/DR (4032 EFA blocks, 2026-03-31..07-21) × 8760; gated/indicative |
| `local_flex_piclo.utilisation_price_gbp_per_mwh` | 150.0 → **135.67** | GB-wide median of 224,535 utilisation guide prices |
| `wholesale_p415.gated_upside_value_gbp_per_mw_yr` | 0 → **0** | source/notes only (real £ = MID overlay × consented export tariff, §7) |
| `balancing_mechanism.gated_upside_value_gbp_per_mw_yr` | 0 → **0** | note-only (needs paid Modo index / full BOALF×BOD, §9) |

**Provenance sidecars committed** (raw-byte sha256, size, record count): cm_clearing (192 recs), cm_derating (801), eac_results (36,288), piclo_competitions (290,321 rows). Elexon MID fetched for wholesale context (2026-07-13 mean ~109.6 £/MWh).

## Invariants held
- **0 confirmed repo-wide** — every gb_v1_6 entry stays `needs_review`/`placeholder`; not-bankable posture untouched.
- **Golden UNCHANGED** — `app.py` `ASSUMPTION_VERSION` stays `gb_v1_4`; `GOLDEN_QUOTE_HASH` + demo pins unchanged; gb_v1_6 invisible to the golden.
- **No invented numbers** — every changed value cites a hashed fetched source; the two gated zeros stay 0 by design.
- **Ancillary nonzero but still gated** — behavioural test proves it stays CONDITIONAL/never-FIRM/out-of-floor absent consent+rate.
- Tests do ZERO live HTTP; ruff/format/mypy/review_gates/pytest green (except the known C034 macOS golden skip).

## Next
- Confirm the fetched leaves with a per-delivery-year cadence (CM tables re-published each auction); widen the EAC window to a full rolling 12 months once the archive resources are wired.
- The remaining sourced-but-not-yet-fetcher-backed leaves (DUoS SLC14, TNUoS/BSUoS, CCL/RO) per the register.
