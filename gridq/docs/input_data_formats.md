# Input data formats — GridQ ingestion (M0)

The **exact** file formats `gridq.ingestion` accepts. Four inputs:

1. [SiteModel JSON](#1-sitemodel-json) — the structured site description.
2. [Half-hourly (HH) load CSV](#2-half-hourly-load-csv) — the site's real metered series.
3. [Modo / wholesale forecast CSV](#3-modo--wholesale-forecast-csv) — a wholesale price curve.
4. [Market forecast JSON](#4-market-forecast-json) — the same forecast as JSON.

Conventions across all four: power in **kW**, energy in **kWh**, money in **GBP** (£/MWh for
wholesale prices), time series are **half-hourly** (48 periods/day). Column names are matched
**case-insensitively** and trimmed; a small set of aliases is accepted (listed per format).
**No loader ever invents a missing value** — see [docs/ingestion.md](ingestion.md).

---

## 1. SiteModel JSON

A single JSON **object** matching the `SiteModel` schema (`gridq.schemas.site.SiteModel`). The
schema is strict: **unknown fields are rejected** (`extra='forbid'`). Loaded with
`load_site_model_json(path)`. Canonical examples: [`examples/site_t0.json`](../examples/site_t0.json)
(T0) and [`examples/site_t2.json`](../examples/site_t2.json) (T2).

### Required fields

| Field | Type | Notes |
|-------|------|-------|
| `site_id` | string | stable internal id |
| `postcode` | string | GB postcode (full or outward) |
| `data_tier` | enum | `T0_ANNUAL_KWH_TARIFF_POSTCODE` \| `T1_PARTIAL_HALF_HOURLY` \| `T2_VALIDATED_12_MONTH_HALF_HOURLY` |
| `load_data` | object | `LoadDataSummary` (see below) |
| `tariff_stack` | object | `TariffStack` |
| `connection` | object | `ConnectionDetails` — `mic_kw` (≥ 0) is required |
| `consents` | object | `ConsentState` (all fields default; the object itself is required) |
| `physical_gates` | object | `PhysicalGateStatus` (all fields optional) |
| `counterparty` | object | `Counterparty` — `legal_name` is required |

**Tier/coverage invariants enforced by the schema:**

- `T2_VALIDATED_12_MONTH_HALF_HOURLY` ⇒ `load_data` carries HH coverage metadata
  (`half_hourly_rows` + `start_date` + `end_date`).
- `T0_ANNUAL_KWH_TARIFF_POSTCODE` ⇒ `annual_consumption_kwh` (or `load_data.annual_kwh`) is set.

### Optional fields

`schema_version` (defaults to the package version), `site_name`, `country` (default `"GB"`),
`annual_consumption_kwh`, `source_references[]`, `missing_data[]`, `created_at`. Nested optional
fields follow each component model (e.g. `connection.mec_kw`/`asc_kw`/`dno_region`/`voltage_level`,
`consents.export_mpan`, `counterparty.company_number`/`sector`).

### `load_data` (`LoadDataSummary`)

```jsonc
{
  "annual_kwh": 1850000.0,                 // float ≥ 0, or null
  "half_hourly_file_ref": "data/site/hh_2025.parquet", // pointer to the canonical series
  "half_hourly_rows": 17520,               // present HH intervals (T1/T2)
  "start_date": "2025-01-01",              // ISO date
  "end_date": "2025-12-31",
  "missing_intervals": 42,                 // counted gaps (never back-filled)
  "max_import_kw": 420.0,                  // float ≥ 0
  "max_export_kw": 0.0,
  "validation_warnings": []
}
```

### Coherence checks (second gate, beyond the schema)

After schema validation, `validate_site_model` adds cross-field checks. An **error**-severity
finding makes `load_site_model_json` raise (distinct from a schema failure); warnings don't.
Examples: a site that exports (`connection.mec_kw > 0`) but declares `g99`/`g100` as
`not_required` (**error**); a T2 site with no `half_hourly_file_ref` (**warning**); a missing
`counterparty.company_number` (**warning**).

### Failure modes (all raise `SiteModelLoadError`)

missing file · invalid JSON (reports line/column) · non-object top level · schema validation
failure (each error names its field path) · incoherent-but-schema-valid site.

---

## 2. Half-hourly load CSV (or Excel)

A CSV **or Excel workbook (`.xlsx`/`.xls`)** of the site's real metered half-hourly readings.
Loaded with `load_half_hourly_csv(path)`, which dispatches on the file extension (same columns,
aliases and validation either way). An Excel file must have a **single sheet** — a multi-sheet
workbook is rejected with an error naming the sheets, never guessed (export the intended sheet to
CSV, or supply a one-sheet file).

### Columns

| Column | Required? | Aliases (case-insensitive) | Notes |
|--------|-----------|----------------------------|-------|
| `timestamp` | **required** | `datetime`, `date_time`, `settlement_datetime`, `time`, `ts` | any parseable date-time; must parse for **every** row |
| `import_kw` *or* `import_kwh` | **one required** | `import_power_kw`/`demand_kw`/`kw_import`/`import_avg_kw`; `import_energy_kwh`/`kwh_import`/`consumption_kwh`/`kwh`/`energy_kwh` | import power, or import energy per interval. A bare `kwh`/`energy_kwh` column (common installer/normalised export, e.g. `timestamp,kwh`) is read as import energy |
| `export_kw` *or* `export_kwh` | optional | `export_power_kw`/`generation_kw`/`kw_export`; `export_energy_kwh`/`kwh_export` | export side, if present |
| `site_load_kw` | optional | `load_kw`, `net_load_kw` | site load if supplied separately |
| `meter_id` | optional | `mpan`, `meter`, `mpan_core` | kept as text |
| `quality_flag` | optional | `quality`, `flag`, `status` | kept as text |

### Example

```csv
timestamp,import_kw,export_kw,meter_id,quality_flag
2025-01-01T00:00:00,118.4,0,1200001234567,A
2025-01-01T00:30:00,121.0,0,1200001234567,A
2025-01-01T01:00:00,117.6,0,1200001234567,A
```

### Rules

- **Energy → power:** when only `import_kwh` is given, average kW is derived as
  `kWh × (60 / interval_minutes)` (≡ `× 2` for 30-minute periods) — a labelled unit transform.
- **Missing intervals** are counted over the observed window (`expected − present`) and **never
  back-filled**. The returned frame has exactly the input row count.
- **Duplicate timestamps** are counted and reported (rows are **not** dropped).
- **Negative** import/export readings are flagged (counted) unless `allow_negative=True`.
- **Blank/non-numeric** load cells become null and are counted — not filled.
- **Unparseable timestamps** raise `HalfHourlyDataError` naming the offending values.

### Timestamps and timezones

- **Naive timestamps** (no UTC offset) are taken as the site's **local clock**, labelling the
  **start** of each half-hour period. They are never re-timed.
- **A uniform UTC offset** is kept: the parsed series is timezone-aware.
- **Mixed offsets** (`+00:00`/`+01:00`, the standard UK export style across a clock change) are
  converted to a single timezone-aware **UTC** axis. The physical instants are unchanged and no
  row is dropped; unparseable values still raise.
- **Daylight saving:** a timezone-aware series is physically continuous across a GB clock change,
  so dispatch sees no false gap there. A naive local series jumps by one hour at spring-forward,
  which dispatch treats as one conservative segment boundary per year rather than inventing the
  missing labels. Tariff banding (DUoS red 16:00-19:00 and the other time-of-use windows) always
  follows the GB local clock: timezone-aware timestamps are converted to Europe/London before
  banding; naive timestamps are read as the local clock directly.

### Tier grading

`summarise_half_hourly_data(df)` → a `HalfHourlySummary`; `infer_data_tier_from_hh_summary(summary)`
→ `T0`/`T1`/`T2`. T2 needs ≥ ~17 520 present intervals (within a ±2-day tolerance) **and**
completeness ≥ 90 %. See [docs/ingestion.md](ingestion.md#data-tiers-deterministic).

---

## 3. Modo / wholesale forecast CSV

A CSV of a wholesale price forecast (e.g. a Modo Energy export). Loaded with
`load_market_forecast_csv(path)`.

### Columns

| Column | Required? | Aliases (case-insensitive) | Notes |
|--------|-----------|----------------------------|-------|
| `timestamp` | **required** | `datetime`, `date_time`, `settlement_datetime`, `period_start`, `settlement_period_start`, `time`, `ts` | parseable date-time |
| `price_gbp_per_mwh` | **required** | `price_gbp_mwh`, `gbp_per_mwh`, `wholesale_price_gbp_per_mwh`, `price_gbp`, `price` | £/MWh; may be **negative** |
| `scenario` | optional | `case`, `scenario_name` | e.g. `central` / `low` / `high` |
| `source` | optional | `provider`, `data_source` | recorded as provenance |
| `forecast_version` | optional | `version`, `vintage` | recorded as provenance |
| `created_at` | optional | `created`, `generated_at`, `run_date`, `forecast_date` | recorded as provenance |

`source` / `forecast_version` / `created_at` may instead be passed as arguments to the loader,
which **override** any same-named column.

### Example

```csv
timestamp,price_gbp_per_mwh,scenario,source,forecast_version
2025-01-01T00:00:00,80.5,central,Modo,2025-06-v1
2025-01-01T00:30:00,72.0,central,Modo,2025-06-v1
2025-01-01T01:00:00,-15.0,central,Modo,2025-06-v1
```

### Rules

- **No generated prices** — only ever loaded from the supplied file.
- A **missing `price_gbp_per_mwh` column** raises `MarketForecastError`.
- **Missing prices** (blank cells) stay missing and raise a warning; they are **not** filled
  unless `allow_interpolation=True` (interior-only, labelled, never extrapolated).
- **Negative prices** are recorded as INFO (legitimate for GB wholesale), never an error.
- **Provenance** (`source`, `forecast_version`, `content_hash`, `file_sha256`) is recorded so a
  quote can cite the exact forecast.

> ⚠️ The real Modo column names/units/layout are **unconfirmed** — see `OPEN_COMMENTS`. The
> alias map is a starting point; confirm against a real Modo file.

---

## 4. Market forecast JSON

The same forecast as JSON. Loaded with `load_market_forecast_json(path)`. Two accepted shapes.

### a) A top-level array of point objects

```json
[
  {"timestamp": "2025-01-01T00:00:00", "price_gbp_per_mwh": 80.5, "scenario": "central"},
  {"timestamp": "2025-01-01T00:30:00", "price_gbp_per_mwh": 72.0, "scenario": "central"}
]
```

### b) An object with forecast-level metadata + a points array

```json
{
  "source": "Modo",
  "forecast_version": "2025-06-v1",
  "created_at": "2025-06-01T00:00:00",
  "points": [
    {"timestamp": "2025-01-01T00:00:00", "price_gbp_per_mwh": 80.5, "scenario": "central"},
    {"timestamp": "2025-01-01T00:30:00", "price_gbp_per_mwh": 72.0, "scenario": "central"}
  ]
}
```

The points array may live under any of: `points`, `data`, `series`, `forecast`, `prices`,
`rows`. Each point uses the **same field names and aliases** as the CSV (§3). `timestamp` and
`price_gbp_per_mwh` are required on the points; the same rules apply (no generated prices,
missing prices warned, provenance recorded). A missing price field, malformed JSON, or a
non-array/non-object payload raises `MarketForecastError`.

---

## Quick reference

| Input | Loader | Required | Hard failure |
|-------|--------|----------|--------------|
| SiteModel JSON | `load_site_model_json` | the SiteModel required fields | bad JSON / schema / coherence error |
| HH load CSV | `load_half_hourly_csv` | `timestamp` + `import_kw`\|`import_kwh` | unparseable timestamp / missing import column |
| Wholesale CSV | `load_market_forecast_csv` | `timestamp` + `price_gbp_per_mwh` | missing price column / unparseable timestamp |
| Forecast JSON | `load_market_forecast_json` | `timestamp` + `price_gbp_per_mwh` per point | malformed JSON / missing price field |
