# Manual Quote Benchmark

**Why this exists.** The whole cost-compression thesis is *falsifiable only if we measure
how long a quote takes by hand, before the system erases it.* The first hand-built quotes
are the control group for time-to-quote and human-minutes — and the system is built
precisely to make hand-building a quote a thing of the past. Capture the baseline **now**:
retrofitted, this data is worthless as evidence. The telemetry dataset must one day
answer, with numbers, *time-to-quote vs the manual benchmark · marginal cost per quote ·
hit rate by data tier · underwriting error by stream* — and that comparison is impossible
if the "before" was never recorded. (System Brief: Acceptance test 2; X.3 telemetry;
[`docs/01_project_spec.md`](01_project_spec.md), telemetry issue [010](issues/010-telemetry.md).)

> **Measure the manual benchmark before the system makes it unmeasurable.** This is a
> primary fundraising artifact, not housekeeping.

**Honest, not invented.** A benchmark records *measured* human effort on a **real**
hand-built quote. Over-counting effort is fine; guessing is not. A row with no recorded
effort is not a benchmark — `human_minutes` must be a real, positive number. Illustrative
rows must be clearly labelled and never treated as a real input (the
[shared input-data rule](parallel_development_plan.md#5-shared-input-data-rule-data-integrity--the-most-important-rule)
applies: no fabricated data passed off as real).

---

## How to record one

Each hand-built quote is one `ManualBenchmarkRecord`
([`gridq.telemetry.manual_benchmark`](../src/gridq/telemetry/manual_benchmark.py)). Build
it in code (validated, strict) or log a row by hand in the table below — keep the two in
step.

```python
from gridq.telemetry.manual_benchmark import (
    create_manual_benchmark_record,
    render_manual_benchmark_markdown,
    convert_benchmark_to_telemetry_event,
)

record = create_manual_benchmark_record(
    benchmark_id="MB-001",
    site_name="Acme Cold Store (LE17)",
    contributor="Oli",
    date="2026-06-20",
    data_tier="T1_PARTIAL_HALF_HOURLY",
    human_minutes=180,                       # total wall-clock effort, all people (required, > 0)
    number_of_back_and_forth_iterations=2,   # rounds of installer/customer back-and-forth
    missing_data_categories=["12-mo HH", "export capacity", "counterparty no."],
    quote_type="indicative",                 # indicative | underwritten | proposal | other
    quote_outcome="sent",                    # sent | won | lost | abandoned | pending
    notes="tariff stack reconstructed by hand from a bill",
)

print(render_manual_benchmark_markdown(record))      # → a Markdown table row
event = convert_benchmark_to_telemetry_event(record)  # → a TelemetryEvent (event_type="manual_benchmark")
```

A worked, clearly-labelled example: [`examples/manual_benchmark_entry_example.md`](../examples/manual_benchmark_entry_example.md).

### Fields

| field | meaning |
|-------|---------|
| `benchmark_id` | stable id for this benchmark row (e.g. `MB-001`) |
| `site_name` | site label (+ postcode/outward code); set `linked_quote_id` once a `SiteModel`/quote exists |
| `source` | provenance of the benchmark (default `manual_quote`) |
| `contributor` | who did the work (sum effort across people in `human_minutes`) |
| `date` | when the hand-built quote was produced |
| `data_tier` | `DataTier` — T0 (annual kWh + tariff + postcode), T1 (partial HH), T2 (≥12 months validated HH) |
| `human_minutes` | **required, > 0** — total wall-clock effort, all people |
| `number_of_back_and_forth_iterations` | rounds of back-and-forth with the installer/customer to get usable data |
| `missing_data_categories` | what had to be chased/assumed (feeds the ranked "missing-data" ask later) |
| `quote_type` | the artifact produced: `indicative` / `underwritten` / `proposal` / `other` |
| `quote_outcome` | commercial outcome: `sent` / `won` / `lost` / `abandoned` / `pending` |
| `notes` | anything that explains the time: messy data, manual tariff reconstruction, tooling used |
| `linked_quote_id` | the system `QuoteRecord.quote_id` once one exists (links manual ↔ system) |

## Log (hand-edited mirror of the records)

| benchmark_id | date | site | data tier | contributor | human minutes | iterations | missing data | quote type | outcome | notes |
|------|------|------|-----------|-------------|---------------|------------|--------------|-----------|---------|-------|
| _example_ MB-001 | 2026-06-20 | Acme Cold Store (LE17) | T1 | Oli | 180 | 2 | 12-mo HH; export capacity; counterparty no. | indicative | sent | tariff stack reconstructed by hand from a bill |
|  |  |  |  |  |  |  |  |  |  |  |

---

## How it lands in telemetry

`convert_benchmark_to_telemetry_event(record)` maps a benchmark onto the shared
[`TelemetryEvent`](../src/gridq/schemas/telemetry.py) shape, marked
**`event_type="manual_benchmark"`** — the marker that distinguishes the manual baseline
from system-generated quotes while keeping both in **one dataset** for an apples-to-apples
comparison. `human_minutes`, `installer_iterations` (= back-and-forth count),
`missing_data_count`, `data_tier` and `contributor` carry across; `created_at` is derived
deterministically from the benchmark `date` (metadata only — it never enters a number).

This is a perimeter / telemetry record: it ships from the first quote (X.3), is never
retrofitted, and **never feeds a price**.
