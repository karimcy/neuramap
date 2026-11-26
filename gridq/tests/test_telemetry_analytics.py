"""Telemetry dataset aggregations (L17 · X.3).

The Brief: "The telemetry dataset must answer, with numbers: time-to-quote vs the manual
benchmark · marginal cost per quote · hit rate by data tier · underwriting error by stream."
`summarise_telemetry` answers the computable two (time/effort compression vs the benchmark, and
hit rate by data tier) and honestly names the two whose inputs do not exist yet — never guessed.
"""

from __future__ import annotations

import gridq.schemas as s
from gridq.telemetry.analytics import summarise_telemetry

T2 = s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY
T0 = s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE


def _event(
    event_type: str,
    *,
    data_tier: s.DataTier | None = None,
    human_minutes: float | None = None,
    outcome: s.QuoteOutcome = s.QuoteOutcome.PENDING,
    eid: str = "e",
) -> s.TelemetryEvent:
    return s.TelemetryEvent(
        event_id=eid,
        quote_id=eid,
        event_type=event_type,
        data_tier=data_tier,
        human_minutes=human_minutes,
        outcome=outcome,
    )


def test_time_compression_vs_benchmark() -> None:
    events = [
        _event("quote_created", human_minutes=15.0, eid="q1"),
        _event("quote_created", human_minutes=25.0, eid="q2"),
        _event("manual_benchmark", human_minutes=180.0, eid="b1"),
    ]
    summary = summarise_telemetry(events)
    assert summary.quote_count == 2
    assert summary.benchmark_count == 1
    assert summary.mean_system_human_minutes == 20.0
    assert summary.mean_benchmark_human_minutes == 180.0
    assert summary.time_compression_ratio == 9.0  # 180 / 20


def test_hit_rate_by_data_tier() -> None:
    events = [
        _event("quote_created", data_tier=T2, outcome=s.QuoteOutcome.WON, eid="a"),
        _event("quote_created", data_tier=T2, outcome=s.QuoteOutcome.LOST, eid="b"),
        _event("quote_created", data_tier=T2, outcome=s.QuoteOutcome.WON, eid="c"),
        _event("quote_created", data_tier=T0, outcome=s.QuoteOutcome.LOST, eid="d"),
        # Unresolved / no tier are excluded from the hit rate.
        _event("quote_created", data_tier=T2, outcome=s.QuoteOutcome.PENDING, eid="e"),
        _event("quote_created", data_tier=None, outcome=s.QuoteOutcome.WON, eid="f"),
    ]
    summary = summarise_telemetry(events)
    assert summary.hit_rate_by_data_tier == {T0.value: 0.0, T2.value: round(2 / 3, 4)}


def test_data_gated_metrics_are_named_unavailable_not_guessed() -> None:
    summary = summarise_telemetry([_event("quote_created", human_minutes=10.0)])
    joined = " ".join(summary.unavailable).lower()
    assert "marginal cost per quote" in joined
    assert "underwriting error by stream" in joined


def test_empty_dataset_is_safe() -> None:
    summary = summarise_telemetry([])
    assert summary.quote_count == 0
    assert summary.mean_system_human_minutes is None
    assert summary.time_compression_ratio is None
    assert summary.hit_rate_by_data_tier == {}
