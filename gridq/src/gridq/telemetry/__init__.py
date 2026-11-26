"""Telemetry — the cost-compression dataset (X.3).

The system exists to compress quote cost; that claim is only falsifiable if it is
*measured*. This package holds the tooling that captures the measurement. Today that is
the **manual quote benchmark**: the human-minutes and back-and-forth a hand-built quote
costs *today*, captured before the system erases it (the first hand-built quotes are the
control group for the whole cost-compression thesis — see ``docs/manual_benchmark.md``
and the System Brief, Acceptance test 2).

A manual benchmark converts into the same :class:`gridq.schemas.TelemetryEvent` shape as
a system-generated quote (marked ``event_type="manual_benchmark"``), so the manual
baseline lives in one dataset with system quotes for an apples-to-apples comparison.

No pricing or optimisation logic lives here.

This is quote telemetry, not site telemetry. Measured site series (meter, SCADA and device
points, raw and curated, with quality flags and gaps) are the IC-14 contract,
`gridq.contracts.telemetry_series` (WP-TEL-01).
"""

from __future__ import annotations

from gridq.telemetry.manual_benchmark import (
    ManualBenchmarkRecord,
    ManualQuoteOutcome,
    ManualQuoteType,
    convert_benchmark_to_telemetry_event,
    create_manual_benchmark_record,
    render_manual_benchmark_markdown,
)

__all__ = [
    "ManualBenchmarkRecord",
    "ManualQuoteType",
    "ManualQuoteOutcome",
    "create_manual_benchmark_record",
    "render_manual_benchmark_markdown",
    "convert_benchmark_to_telemetry_event",
]
