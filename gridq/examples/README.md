# examples/

Runnable example inputs and reference records. Use these to try the pipeline and to see the exact
shape of each contract. See the [README](../README.md) and the
[operating guide](../docs/operating_guide.md) for the full how-to.

## Files

| File | What it is |
|------|------------|
| [`site_t0.json`](site_t0.json) | a **T0** `SiteModel` — annual kWh + tariff + postcode, no half-hourly data |
| [`site_t2.json`](site_t2.json) | a **T2** `SiteModel` — carries validated 12-month half-hourly *metadata* (the series file itself is not committed; see note below) |
| [`optimisation_request.json`](optimisation_request.json) | an example `OptimisationRequest` (the sealed input to the engine) |
| [`optimisation_result_baseline.json`](optimisation_result_baseline.json) | an example `OptimisationResult` from the integration `BaselineEngine` |
| [`quote_record_example.json`](quote_record_example.json) | an example stamped `QuoteRecord` |
| [`manual_benchmark_entry_example.md`](manual_benchmark_entry_example.md) | template for a manual telemetry/benchmark entry |
| [`demo_site/`](demo_site/) | a clearly-labelled, self-contained **demo dataset** — a representative site **with** a committed synthetic-but-labelled 12-month meter series, so the full flow runs unaided (used by the UI's Demo mode). See [`demo_site/README.md`](demo_site/README.md). |

## Run them

```bash
# T0 site — prints the ranked data-asks and an honest not_bankable result
gridq run examples/site_t0.json

# T2 site — see the note below: it degrades to insufficient_data until you supply a real meter file
gridq run examples/site_t2.json

# A PRICED run — supply a real 12-month half-hourly meter CSV
gridq run examples/site_t2.json --hh path/to/your_meter_12months.csv --out runs/demo
```

Each run writes `quote.json`, `telemetry.json`, `memo.md`, `proposal.md` and `report.html` to the
output directory (default `runs/<quote_id>/`). Re-render the documents from a saved quote with
`gridq render runs/demo/quote.json --site examples/site_t2.json`.

> **Note — the example sites don't price out of the box, on purpose.** The dispatch optimiser reads
> the half-hourly series from `load_data.half_hourly_file_ref`. `site_t2.json` points at a file under
> the gitignored `data/` path that isn't committed (real meter data is never committed), so the run
> honestly returns `insufficient_data` rather than inventing a load shape. Supply a real series with
> `--hh` (which re-points the reference at your file) to get a priced result. Every run stays
> **`not_bankable`** while it rests on the placeholder assumption library — by design.

A clearly-labelled, self-contained **demo dataset** now lives in [`demo_site/`](demo_site/) — a
representative site **with** a committed, synthetic-but-labelled 12-month meter series so the full
flow runs unaided. The Wave-5 web UI runs it end-to-end (real engine + illustrative debt) under its
**Demo** mode:

```bash
python -m gridq.app   # → http://127.0.0.1:8000 — choose "Real site" or "Demo"
```

It stays **illustrative / not bankable** throughout (synthetic load + illustrative debt); see
[`demo_site/README.md`](demo_site/README.md) and [`../docs/wave5_scope.md`](../docs/wave5_scope.md) §5.
