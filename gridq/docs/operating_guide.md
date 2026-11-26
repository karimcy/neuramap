# GridQ operating guide

The hands-on runbook for **running and operating** GridQ — the deep version of the README's
"how to use it / where data goes / how to change it / honest limits" sections. If you only read one
thing first, read the [README](../README.md); this is the reference you come back to.

Everything here describes **what is actually built today** (Wave 4). Where something is planned but
not built, it says so. GridQ never invents a number, and every quote today is **`not_bankable`** on
purpose — see [§7 Honest limits](#7-honest-limits--the-do-not-present-as-bankable-checklist).

---

## 1. Install & environment

Python **3.11+**.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"     # engine + pytest/ruff/mypy
pip install -e ".[app]"     # + FastAPI/uvicorn/jinja2 for the demo UI
```

The dispatch optimiser depends on a pinned solver stack (`linopy`, `highspy`, `xarray`) — these come
with the base install and are part of the **reproduction contract** (a quote reproduces byte-for-byte
only on a matching platform/solver build; the run records its `reproduction_env`). A full-year
17,520-period MILP solves in ≈2.5 s single-threaded.

Quality bar (must be green before any change is "done"):

```bash
pytest ; ruff check . ; ruff format --check . ; mypy
python -m gridq.dev.review_gates      # placeholder / banned-string gate
```

---

## 2. CLI reference

Entry point: `gridq` (installed console script) or `python -m gridq.cli`.

### `gridq run <site.json> [options]`
Takes a site end-to-end: ingest → grade → dispatch MILP → finance → stamped `QuoteRecord` →
telemetry → the three artifacts. Uses the **real `MilpEngine`** by default.

| Option | Default | Meaning |
|--------|---------|---------|
| `site_json` (positional) | — | path to a `SiteModel` JSON (must exist) |
| `--hh PATH` | none | real half-hourly meter CSV; folds in and **re-grades** the tier. Points `half_hourly_file_ref` at this file so the MILP can read the series. If the file would invalidate the site, the run continues on the original site and warns. |
| `--forecast PATH` | none | wholesale/Modo price file (CSV/JSON). **Gated upside only** — pinned for provenance, never priced into the floor. |
| `--forecast-source STR` | none | provider name, if the file doesn't record one (e.g. `Modo`) |
| `--forecast-version STR` | none | forecast vintage label, if the file doesn't record one |
| `--assumptions VERSION` | `gb_v0_1` | assumption release to price against (`gb_v0_1` or the sourced `gb_v1_0`) |
| `--wholesale / --no-wholesale` | `--no-wholesale` | treat wholesale/P415 as requested; lists the forecast as a data-ask if absent |
| `--contributor NAME` | none | who ran the quote (recorded on telemetry) |
| `--out DIR` | `runs/<quote_id>/` | where to write the artifacts |
| `--json` | off | print the `QuoteRecord` as JSON instead of the summary table |

**Exit codes:** `0` success · `2` fatal input error (site won't load, unknown assumption version).

### `gridq render <quote.json> [--site <site.json>] [--out DIR]`
Regenerates `memo.md` / `proposal.md` / `report.html` from a saved `QuoteRecord` — **byte-identical**
to the original run. Pass `--site` to fill the counterparty / consents / gate sections (without it,
those render "site context not supplied," never assumed).

### `gridq info` / `gridq version`
List the modules and their responsibilities / print the package version.

### Worked examples

```bash
# Out of the box — honest degrade (the example's meter file isn't in the repo)
gridq run examples/site_t0.json          # T0: prints ranked data asks; not_bankable
gridq run examples/site_t2.json          # T2 metadata, no series → insufficient_data; not_bankable

# Priced run — supply a real 12-month meter CSV
gridq run examples/site_t2.json --hh data/acme/hh_2025.csv --out runs/acme

# Sourced assumptions + wholesale upside requested
gridq run examples/site_t2.json --hh data/acme/hh_2025.csv \
  --assumptions gb_v1_0 --forecast data/modo_2025.csv --forecast-source Modo --wholesale

# Re-render docs from a saved quote
gridq render runs/acme/quote.json --site examples/site_t2.json --out runs/acme/docs
```

> **Why the bundled examples don't price out of the box.** The dispatch MILP reads the half-hourly
> series from `load_data.half_hourly_file_ref`. The examples reference a file under the gitignored
> `data/` path that isn't committed, so the solver honestly returns `insufficient_data` (no spec, no
> invented load). Supply a real series via `--hh` (or place a file at the referenced path) to get a
> priced result.

---

## 3. The web UI

```bash
python -m gridq.app           # serves http://127.0.0.1:8000
```

**Built today (`src/gridq/app/app.py`, issue 013):** a thin FastAPI + Jinja2 **read-only demo shell**.
Routes: `GET /` (pick a bundled example site or paste a `SiteModel` JSON) and `POST /run` (runs the
real pipeline and shows the three artifacts in one view, with the provenance stamp and warnings).
It computes and formats **nothing** itself — every figure comes from the deterministic outputs layer.
It prices against `gb_v0_1`. Override the example folder with `GRIDQ_SITE_FIXTURES_DIR`.

**Planned for Wave 5 (not built):** an explicit **two-mode chooser** (real-site primary / demo
opt-in), the navigable Inputs→Dispatch→Sizing→Finance flow, the 3D sizing surface, and public
hosting. Requirements and acceptance criteria: [`wave5_scope.md`](wave5_scope.md).

---

## 4. Reading the outputs

`gridq run` writes to the output directory:

| File | Format | Contents |
|------|--------|----------|
| `quote.json` | JSON | the stamped `QuoteRecord` — the single source of truth all docs render from |
| `telemetry.json` | JSON | one `TelemetryEvent` (data tier, missing-data count, outcome) |
| `memo.md` | Markdown | **underwriting pack** (capital-facing) |
| `proposal.md` | Markdown | **customer proposal** (plain language) |
| `report.html` | HTML | self-contained web report (no external assets, no arithmetic) |

The **underwriting pack** contains: a NOT-BANKABLE banner (while on placeholders); the system spec
(power/energy/duration); P50/P90 year-by-year cashflows; DSCR & covenant; the **contracted floor vs
merchant upside** split; price-to-target & returns (project/equity IRR, payback); debt assumptions;
counterparty credit; consents & physical gates; the full provenance block (quote id, input hash,
assumption/code/schema versions, expiry); and **every warning, by name**.

### Where figures are deliberately "unavailable" (and why)

GridQ shows a labelled gap rather than a guess. The common ones:

| Figure | Shows as | Why (until…) |
|--------|----------|--------------|
| DSCR / debt terms | *unavailable*, with the reason | no lender terms supplied and no usable credit band. Supply the lender's terms with `gridq run SITE --lender-terms terms.json` or the app's lender-terms block (WP-UW-03, D75); never guessed |
| Equity (levered) IRR | *unavailable* | no debt principal to split against — project IRR is the return until debt terms exist |
| Price-to-target / payback | *unavailable* | needs a real dispatch energy volume **and** a confirmed target return |
| Customer bill before/after | *pending* | there is no customer-bill model in v1 — surfaced as a seam, not estimated |
| Wholesale / BM / ancillary £ | £0, *conditional* | gated upside (**B002**) — no route-to-market partner yet; never in the floor |
| Counterparty / consents / gates (in `render`) | *site context not supplied* | only when no `--site` was passed to `gridq render` |

Out of the box every quote is `bankable_status = not_bankable` and `quote_mode = draft`.

---

## 5. Data: placement, formats, tiers, privacy

Four inputs, all specified field-by-field in [`input_data_formats.md`](input_data_formats.md):

1. **`SiteModel` JSON** — the structured site (tariff, connection, consents, gates, counterparty).
   Strict schema (`extra='forbid'`). Examples: [`../examples/site_t0.json`](../examples/site_t0.json),
   [`../examples/site_t2.json`](../examples/site_t2.json).
2. **Half-hourly meter CSV** (`--hh`) — `timestamp` + (`import_kw` | `import_kwh`); 30-minute
   periods. Missing intervals counted (never filled); negatives flagged; nothing invented.
3. **Wholesale/Modo forecast** (`--forecast`, CSV or JSON) — `timestamp` + `price_gbp_per_mwh`
   (may be negative). **Referenced, not stored**: pinned by source + version + content hash. Gated
   upside only.
4. **Tariff / connection / consents / counterparty** — fields **inside** the `SiteModel`.

**Data tiers** (graded deterministically by ingestion):

| Tier | Input | Unlocks |
|------|-------|---------|
| **T0** | annual kWh + tariff + postcode | indicative banded view; ranked data-asks; no dispatch |
| **T1** | partial half-hourly | some load shape; still under-qualified |
| **T2** | validated ~12-month half-hourly, ≥90% complete (≈17,520 intervals ±2 days) | full dispatch MILP; firm floor priced; basis for an underwritten quote |

**Privacy (hard rules).** Real third-party meter data and counterparty credit are sensitive: keep
them under a **gitignored `data/`** path, never committed, never preloaded as a public default. A
user's own entered data is theirs to process for their run. If real submissions are ever stored,
record where and how they're protected (access control, no public indexing, a deletion path). The
public demo ships only the clearly-labelled example dataset.

---

## 6. Changing the model

### Update an assumption value (+ provenance)
Releases are **frozen** — never edit `assumptions/gb_v0_1.yaml` or `gb_v1_0.yaml` in place (existing
quotes stay pinned to the version that produced them). To change a number:

1. Copy the latest release to a **new file** (e.g. `assumptions/gb_v1_1.yaml`); bump
   `metadata.version` to match.
2. Edit the entry — set a real `source`, move `status` along `placeholder → needs_review →
   confirmed`, raise `confidence`, update `last_reviewed`. All **8 fields** stay required; a blank
   `source`/`status` is a validation error.
3. Run with `--assumptions gb_v1_1`. A figure is only bankable once its status is `confirmed`.

Gap list + the public source to confirm each: [`assumptions_to_source.md`](assumptions_to_source.md).
Rules: [`assumption_library.md`](assumption_library.md).

### Add a value stream
1. `MarketStream` enum in `src/gridq/schemas/enums.py`; if it's upside that must never enter the
   floor, add it to `GATED_UPSIDE_STREAMS` in `src/gridq/schemas/market.py`.
2. Implement gate + value function in `src/gridq/value_stack/gb_streams.py`; register it.
3. Add the assumption section(s) to the library YAML (a gated stream stays placeholder **zero** until
   its backing assumption is `confirmed` and positive — promoting it to the floor is a *version bump*,
   not a code edit).
4. Bump `SCHEMA_VERSION`; update `tests/test_optimisation_contract_conformance.py`.

### Frozen contracts
`SiteModel` (input) and `OptimisationResult` (output) decouple the underwriting platform from the
optimisation engine. Changing either is a deliberate, versioned event that must trip the conformance
tests — coordinate it (flag in `docs/shared_work/OPEN_COMMENTS.md`), don't edit silently. The core
stays market-agnostic: no `market == "GB"` branch; GB specifics are content in the assumption library.

---

## 7. Honest limits — the "do not present as bankable" checklist

Before anyone treats a GridQ output as a real commercial offer, confirm:

- [ ] **`bankable_status` is `bankable_candidate`** — today it is always `not_bankable` (both releases
      are **0 confirmed**). Nothing is bankable until the assumptions it used are `confirmed`.
- [ ] **No `placeholder` / `needs_review` assumption is load-bearing** — each is named in the quote's
      warnings; check them.
- [ ] **DSCR is present, not "unavailable"**, and measured on the lender's own terms from a term sheet
      (`--lender-terms`, D75), not synthetic or indicative terms. The record says which (`lender_terms`
      on the financial case).
- [ ] **Any wholesale/BM/ancillary £ is shown as conditional upside, not floor** — gated on **B002**
      (route-to-market partner/API + export consents); valued £0 until then.
- [ ] **The customer-bill figures are understood as *pending*** — there is no bill model in v1.
- [ ] **The data tier supports the claim** — an "underwritten" view needs **T2** (validated 12-month
      half-hourly); T0/T1 are indicative only.

Blockers in full: [`shared_work/BLOCKERS.md`](shared_work/BLOCKERS.md). Live status:
[`project_status.md`](project_status.md). Architecture & determinism rules:
[`02_architecture.md`](02_architecture.md).
