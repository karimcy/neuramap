# Ingestion (M0)

**Module:** `gridq.ingestion` · **Owner hint:** Oli · **Status:** v1 (real HH + market-forecast inputs)

Ingestion is the validated **perimeter** into the deterministic core. It turns the description
of a behind-the-meter site into a validated, *graded* `SiteModel`, and accepts the two real
time-series inputs GridQ underwrites from:

- **Real half-hourly (HH) commercial load data** — the site's own metered consumption (and
  export where present), as a CSV.
- **Real wholesale / Modo price-forecast files** — wholesale price curves, as CSV or JSON.
  Gated upside only (never the priced floor) until the route-to-market partner/API is known.

Exact accepted file formats are specified in **[docs/input_data_formats.md](input_data_formats.md)**.

---

## The one rule that matters: never invent *silently*

**Updated by D45 (Olly, 2026-08-12) — see [`docs/decisions.md`](decisions.md).** The rule was
"never invent missing data". It is now sharpened rather than repealed: **never invent silently,
always derive and declare.** Ingestion never *fabricates* a value, and never presents a derived
value as evidenced. But a missing input must **never block a run**: it becomes a labelled
`DerivedAssumption`, the run completes, and the report discloses it with its basis, its anchors and
the exact confirmation ask ([`validation_requirements.md` §8](validation_requirements.md)).

Two things remain absolutely forbidden: an **unrecorded** guess, and a **fabricated citation**. A
third is new: a `pending_permission` (revenue-stream consent, export MPAN, G99) must never be written
as though consent were granted. Permission cannot be derived; only facts can.

**And a null is never an acceptable input (D45's same-day amendment) — it is worse than a blocked
run.** Ingestion is where a null most often *originates*, so it is where it must be stopped: a
loader that hands a `None` tariff
rate, an empty levy dict or an unset connection field to the pipeline has exported a defect, because
downstream that `None` does not read "unknown", it reads **£0** — a confidently wrong number that
enters P50, P90 and the DSCR looking exactly like a measured one. So a null on the pricing path is a
**defect to fix here**, not a state to pass on and report later. "Nothing is priced on the absent
input" means the affected £ is **refused**, never silently zeroed. Deriving a *published* GB value (a
DNO CDCM tariff table, a NESO de-rating factor, an ENA settlement formula) is **sourcing, not
invention**; only where derivation genuinely fails does the field stay unpopulated, with the search
recorded and the £ refused.

This is still the binding data-integrity rule from
[`docs/parallel_development_plan.md` §5](parallel_development_plan.md) and the System Brief's
"deterministic core, agentic perimeter" — derivation happens at the **perimeter**, once, offline, and
the recorded value is what the deterministic pipeline reads. No LLM sits on the pricing path.

When an input is missing, incomplete, or implausible, ingestion has three honest responses. The first
two are unchanged:

1. **A typed validation warning** (`ValidationIssue` on a `ValidationReport`) — the gap is
   recorded, surfaced, and the affected output is marked accordingly. Nothing is priced on the
   absent input.
2. **A ranked data ask** (`DataAsk`) — a prioritised list of the inputs the user should supply
   to most improve the quote. The data ask is an *output artifact*, not a blocker: the pipeline
   still runs degraded on whatever is present, and says so.

3. **A recorded `DerivedAssumption`** (D45) — where the run needs the value to proceed, the Fable
   advisor derives it from the site's own evidence, its business type and the literature; the value is
   recorded with its `kind`, basis, sources, derivation method, confidence ceiling and confirmation ask;
   the band is capped; and the run **completes**. Derivation happens in a separate offline step that
   writes provenance, so CI stays deterministic and offline.

A missing input becomes **a warning, a data ask, or a declared derived assumption — never a silently
invented number.** Concretely:

- Missing HH intervals are **counted and reported, never back-filled**.
- A loaded frame always has **exactly the input row count** — no rows are added or dropped.
- Missing forecast prices stay missing (warned). Interpolation happens **only** when explicitly
  requested (`allow_interpolation=True`), is **labelled** (`interpolated=True` on the forecast
  and each filled point), and **never extrapolates** beyond the known range.
- The energy↔power conversion (kWh→kW) is a deterministic, labelled unit transform, not a
  fabricated value.

---

## What's in the package

| File | Responsibility |
|------|----------------|
| `loaders.py` | `load_site_model_json` — structured SiteModel JSON → validated `SiteModel`, with clean, field-level errors. |
| `hh_data.py` | Half-hourly CSV: `load_half_hourly_csv`, `validate_half_hourly_coverage`, `summarise_half_hourly_data`, `to_load_data_summary`. |
| `data_tier.py` | Deterministic T0/T1/T2 grading: `infer_data_tier_from_hh_summary` + the structural tier-boundary constants. |
| `market_forecasts.py` | Modo/wholesale forecast CSV/JSON: `load_market_forecast_csv`, `load_market_forecast_json`, `validate_market_forecast`, `hash_market_forecast`, `summarise_market_forecast`. |
| `missing_data.py` | `detect_missing_data` — find which underwriting inputs are absent/incomplete. |
| `improvement_asks.py` | `build_ranked_data_asks` — rank the missing inputs into the ranked data ask. |
| `validators.py` | Shared exceptions, the `ValidationIssue`/`ValidationReport` types, column helpers, and `validate_site_model` (cross-field coherence). |

Everything is re-exported from `gridq.ingestion` for convenience.

---

## 1. SiteModel JSON loader

```python
from gridq.ingestion import load_site_model_json, load_site_model_json_with_report

site = load_site_model_json("data/site_000123.json")          # raises on bad input
site, report = load_site_model_json_with_report("data/x.json")  # inspect coherence findings
```

- Validates against the versioned `SiteModel` schema (`extra='forbid'` + the tier/coverage
  invariants), then runs cross-field **coherence** checks (`validate_site_model`).
- Fails cleanly with a specific, actionable `SiteModelLoadError`: a missing file, invalid JSON
  (with line/column), a non-object top level, a schema failure (each error names the field
  path), or an *incoherent-but-schema-valid* site (e.g. it exports but declares export consent
  `not_required`). The incoherent case is rejected **distinctly** from the schema-invalid case.
- Coherence **warnings** do not raise; use `load_site_model_json_with_report` to read them.

## 2. Half-hourly load data

```python
from gridq.ingestion import (
    load_half_hourly_csv, summarise_half_hourly_data, infer_data_tier_from_hh_summary,
)

df = load_half_hourly_csv("data/site_000123/hh_2025.csv")   # parsed, normalised, no filling
summary = summarise_half_hourly_data(df, file_ref="data/site_000123/hh_2025.csv")
tier = infer_data_tier_from_hh_summary(summary)             # T0 / T1 / T2
```

- **Required columns:** `timestamp` + one of `import_kw` / `import_kwh`.
  **Optional:** `export_kw` / `export_kwh`, `site_load_kw`, `meter_id`, `quality_flag`.
- `validate_half_hourly_coverage(df)` computes: duplicate-timestamp count, missing-interval
  count (over the observed window at the inferred grid), coverage period, completeness %, and
  the inferred granularity. It **counts**; it does not fix.
- `summarise_half_hourly_data(df)` adds the load statistics (max/min/mean import kW, max export,
  negative/null counts) and a `ValidationReport`. Negative readings are flagged unless
  `allow_negative=True` (they are still counted).
- Unparseable timestamps raise `HalfHourlyDataError` naming the offenders — they are never
  coerced and guessed.

### Data tiers (deterministic)

| Tier | Definition (System Brief M0) | Rule in `data_tier.py` |
|------|------------------------------|------------------------|
| **T0** | annual kWh + tariff + postcode; no HH | no genuine HH series (`present_intervals < T1_MIN_PERIODS`) |
| **T1** | partial HH, below the validated-12-month bar | genuine HH present, below the T2 bar |
| **T2** | ≥ ~12 months validated HH | `present_intervals ≥ T2_MIN_PERIODS − tolerance` **and** `completeness_pct ≥ T2_MIN_COMPLETENESS_PCT` |

`T2_MIN_PERIODS` (17 520 = 48 × 365) is a structural calendar boundary and matches the
`SiteModel` schema's own T2 bar. `T2_MIN_COMPLETENESS_PCT` (90 %) is a v1 ingestion default.
Both are *structural* parameters, not pricing assumptions, so they live (labelled) in
`data_tier.py` rather than the assumption library — the same precedent as
`BASELINE_POWER_FRACTION`. Whether the completeness floor should move to the versioned
assumption library (X.1) is an open question — see `OPEN_COMMENTS`.

## 3. Market / wholesale forecast loader (Modo)

```python
from gridq.ingestion import (
    load_market_forecast_csv, load_market_forecast_json,
    validate_market_forecast, hash_market_forecast, summarise_market_forecast,
)

fc = load_market_forecast_csv("data/modo_2025-06.csv", source="Modo", forecast_version="2025-06-v1")
summary = summarise_market_forecast(fc)   # counts, price range, scenarios, provenance
report  = validate_market_forecast(fc)    # missing prices, duplicates, absent provenance
digest  = hash_market_forecast(fc)        # sha256: content hash — order-independent
```

- **Required columns/fields:** `timestamp` + `price_gbp_per_mwh`.
  **Optional:** `scenario`, `source`, `forecast_version`, `created_at`.
- **No generated prices.** A forecast is only ever loaded from a file the user supplies.
- **No interpolation by default.** Missing prices are warned, not filled. Interpolation requires
  `allow_interpolation=True`, fills interior gaps only, labels every filled point, and flags the
  forecast `interpolated=True`.
- **Provenance is recorded:** `source`, `forecast_version`, a `content_hash` (the timestamp/
  price/scenario content) and a `file_sha256` (the literal bytes) — so a quote can cite exactly
  which forecast it used. Negative prices are recorded as INFO (GB wholesale goes negative
  legitimately), never an error.

> ⚠️ The exact Modo export schema is **not yet confirmed**. The alias maps are a best-effort
> starting point; confirm against a real Modo file before relying on them — see `OPEN_COMMENTS`.

## 4. Missing-data → ranked data ask

```python
from gridq.ingestion import build_ranked_data_asks, format_data_asks, data_asks_to_missing_list

asks = build_ranked_data_asks(site, wholesale_requested=True)
print(format_data_asks(asks))                  # human-readable, for an installer email/memo
site_missing = data_asks_to_missing_list(asks) # short strings for SiteModel.missing_data
```

The ask catalogue, ranked by impact on bankability / quote fidelity:

1. **12 months of half-hourly meter data** — blocks T2 / an underwritten quote.
2. **Full tariff** — standing charge, unit rates, DUoS bands, levy lines (the priced floor).
3. **Connection capacities** — MIC / MEC / ASC (bounds dispatch; headroom value).
4. **DNO region** — DUoS band structure; local-flex (Piclo) area.
5. **Export status** — G99 / G100 consent state and export MPAN (gates export streams).
6. **Supplier consent** — host supply-contract interaction (an underwriting condition).
7. **Physical gates** — footprint / fire separation / access (underwriting conditions).
8. **Counterparty legal entity & company number** — the deterministic credit mapping (M9).
9. **Wholesale price forecast file** — *only when wholesale/P415 analysis is requested* (gated
   upside; priced from a supplied forecast, never generated).

Each ask carries *why it matters* and *what it blocks*. Ranking is deterministic (by priority,
then a stable id tie-breaker), so the same site always yields the same ask order.

---

## Determinism & provenance

- Tier grading and ask ranking are pure functions of their input — identical input → identical
  output.
- The forecast `content_hash` reuses `gridq.provenance.hashing.hash_json` (canonical,
  sorted-key JSON) so it is stable and order-independent.
- Ingestion holds **no pricing/financial constants** — the only numbers it carries are the
  structural tier boundaries (documented above) and unit-conversion factors.

## Scope & deferrals (v1)

- **In:** structured SiteModel JSON; real HH CSV; Modo/wholesale forecast CSV/JSON; coverage &
  integrity validation; deterministic tiering; the ranked data ask.
- **Out (Later):** LLM-assisted parsing of arbitrary installer formats (PDF/email/free CSV);
  the human-confirm loop; writing the intake tier into telemetry (X.3 owns that); deriving
  `dno_region` from postcode (the ask flags it instead).

See also: [docs/input_data_formats.md](input_data_formats.md),
[docs/03_data_contracts.md](03_data_contracts.md) (§1 SiteModel),
[docs/issues/008-ingestion-workflow.md](issues/008-ingestion-workflow.md).
