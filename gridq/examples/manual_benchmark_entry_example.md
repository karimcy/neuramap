# Manual benchmark — worked example entry

> ⚠️ **EXAMPLE / ILLUSTRATIVE ONLY.** This is a synthetic, hand-authored row that shows
> the *shape* of a manual quote benchmark. It is **not** a real site and **not** a real
> input — never load it into a quote or treat its numbers as measured. Real benchmarks are
> recorded honestly from actual hand-built quotes (see
> [`docs/manual_benchmark.md`](../docs/manual_benchmark.md)).

A manual benchmark captures how long a quote costs **by hand, today** — the control group
for the cost-compression thesis, before the system erases it.

## The record

```python
from gridq.telemetry.manual_benchmark import create_manual_benchmark_record

record = create_manual_benchmark_record(
    benchmark_id="MB-EXAMPLE-001",
    site_name="Acme Cold Store (LE17)",   # example site — not real
    contributor="Oli",
    date="2026-06-20",
    data_tier="T1_PARTIAL_HALF_HOURLY",
    human_minutes=180,                     # 3 hours wall-clock, all people
    number_of_back_and_forth_iterations=2, # two rounds chasing the installer for data
    missing_data_categories=[
        "12-month half-hourly load",
        "export capacity / consent",
        "counterparty company number",
    ],
    source="manual_quote",
    quote_type="indicative",
    quote_outcome="sent",
    notes="Tariff stack reconstructed by hand from a single PDF bill; "
          "no HH file, so import shape estimated from annual kWh — capped at T1.",
)
```

## Rendered as a Markdown row

`render_manual_benchmark_markdown(record)`:

| benchmark_id | date | site | source | contributor | data tier | human minutes | iterations | missing data | quote type | outcome | linked quote | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MB-EXAMPLE-001 | 2026-06-20 | Acme Cold Store (LE17) | manual_quote | Oli | T1_PARTIAL_HALF_HOURLY | 180 | 2 | 12-month half-hourly load; export capacity / consent; counterparty company number | indicative | sent | — | Tariff stack reconstructed by hand from a single PDF bill; no HH file, so import shape estimated from annual kWh — capped at T1. |

## As a telemetry event

`convert_benchmark_to_telemetry_event(record)` yields a `TelemetryEvent` with
`event_type="manual_benchmark"`, `human_minutes=180`, `installer_iterations=2`,
`missing_data_count=3`, `data_tier=T1_PARTIAL_HALF_HOURLY`, and
`quote_id="manual:MB-EXAMPLE-001"` (until a real `QuoteRecord` is linked). It lands in the
same dataset as system quotes — that is how the manual baseline becomes the comparison
point once automation kicks in.
