"""Telemetry dataset aggregations (X.3) — the falsifiable cost-compression thesis.

The System Brief: "The telemetry dataset must answer, with numbers: time-to-quote vs the manual
benchmark · marginal cost per quote · hit rate by data tier · underwriting error by stream." This
aggregates a collection of `TelemetryEvent`s into those metrics.

Two of the four are computable from the data captured today (time-to-quote-proxy via human-minutes,
and hit-rate by data tier from win/loss outcomes); two require inputs that do not exist yet
(per-quote cost, and realised-vs-underwritten *by stream*), so they are returned as honest
``unavailable`` notes rather than guessed. Pure aggregation — no clock, deterministic.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from gridq.quotes.telemetry import QUOTE_CREATED_EVENT_TYPE
from gridq.schemas import QuoteOutcome, TelemetryEvent
from gridq.telemetry.manual_benchmark import MANUAL_BENCHMARK_EVENT_TYPE

#: Outcomes that resolve a quote for hit-rate purposes (others are still in flight).
_WON = QuoteOutcome.WON
_RESOLVED = frozenset({QuoteOutcome.WON, QuoteOutcome.LOST})


@dataclass(frozen=True)
class TelemetrySummary:
    """The aggregated cost-compression dataset metrics over a set of telemetry events."""

    quote_count: int
    benchmark_count: int
    mean_system_human_minutes: float | None
    mean_benchmark_human_minutes: float | None
    #: Manual-benchmark minutes ÷ system minutes — the cost-compression multiple (>1 = faster).
    time_compression_ratio: float | None
    #: ``data_tier`` → win rate (won ÷ resolved), only over events with a resolved outcome.
    hit_rate_by_data_tier: dict[str, float] = field(default_factory=dict)
    #: Metrics whose inputs do not exist yet — named honestly, never guessed.
    unavailable: list[str] = field(default_factory=list)


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def summarise_telemetry(events: Iterable[TelemetryEvent]) -> TelemetrySummary:
    """Aggregate telemetry events into the Brief's dataset metrics (computable ones only)."""
    events = list(events)
    quotes = [e for e in events if e.event_type == QUOTE_CREATED_EVENT_TYPE]
    benchmarks = [e for e in events if e.event_type == MANUAL_BENCHMARK_EVENT_TYPE]

    system_minutes = [e.human_minutes for e in quotes if e.human_minutes is not None]
    benchmark_minutes = [e.human_minutes for e in benchmarks if e.human_minutes is not None]
    mean_system = _mean(system_minutes)
    mean_benchmark = _mean(benchmark_minutes)
    compression = (
        round(mean_benchmark / mean_system, 4)
        if mean_system is not None and mean_benchmark is not None and mean_system > 0
        else None
    )

    # Hit rate by data tier: won ÷ resolved (won+lost), over every event with a resolved outcome.
    won: dict[str, int] = {}
    resolved: dict[str, int] = {}
    for e in events:
        if e.data_tier is None or e.outcome not in _RESOLVED:
            continue
        tier = e.data_tier.value
        resolved[tier] = resolved.get(tier, 0) + 1
        if e.outcome == _WON:
            won[tier] = won.get(tier, 0) + 1
    hit_rate = {tier: round(won.get(tier, 0) / n, 4) for tier, n in sorted(resolved.items())}

    return TelemetrySummary(
        quote_count=len(quotes),
        benchmark_count=len(benchmarks),
        mean_system_human_minutes=mean_system,
        mean_benchmark_human_minutes=mean_benchmark,
        time_compression_ratio=compression,
        hit_rate_by_data_tier=hit_rate,
        unavailable=[
            "marginal cost per quote — needs a per-quote cost input (not captured yet).",
            "underwriting error by stream — needs realised-vs-underwritten by stream from M&V "
            "(only a whole-quote realised_vs_underwritten_pct field exists, populated post-COD).",
        ],
    )
