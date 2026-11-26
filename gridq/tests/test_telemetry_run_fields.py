"""Every run logs the full telemetry field set (L11 · X.3 telemetry).

System Brief: "Every run logged: data tier, human-minutes, iterations, outcome, realised vs
underwritten" — the falsifiable cost-compression dataset. These tests pin that a system quote run
and a manual benchmark both emit a `TelemetryEvent` carrying each of those fields, that the fields
are declared on the schema (so one can't be silently dropped), and that time-to-quote is
measurable from event timestamps. Optional measured fields stay honest (`None`/`PENDING` until
captured) — never guessed.
"""

from __future__ import annotations

from datetime import date

import gridq.schemas as s
from gridq.quotes.telemetry import emit_quote_telemetry
from gridq.telemetry.manual_benchmark import (
    convert_benchmark_to_telemetry_event,
    create_manual_benchmark_record,
)

# The fields the System Brief requires on every logged run.
_REQUIRED_TELEMETRY_FIELDS = frozenset(
    {
        "data_tier",  # data tier
        "human_minutes",  # human-minutes
        "installer_iterations",  # iterations
        "outcome",  # outcome
        "realised_vs_underwritten_pct",  # realised vs underwritten
        "created_at",  # for time-to-quote (event-timestamp delta)
    }
)


def test_telemetry_schema_declares_every_required_field() -> None:
    assert _REQUIRED_TELEMETRY_FIELDS <= set(s.TelemetryEvent.model_fields)


def test_system_quote_run_logs_every_field(make_site) -> None:
    site = make_site()
    quote = s.QuoteRecord(
        quote_id="q1",
        quote_mode=s.QuoteMode.INDICATIVE,
        site_id=site.site_id,
        data_tier=site.data_tier,
    )
    event = emit_quote_telemetry(site=site, quote=quote, contributor="Olly", human_minutes=20.0)

    assert event.data_tier == site.data_tier
    assert event.human_minutes == 20.0
    assert event.installer_iterations is None  # not yet measured — honest, not guessed
    assert event.outcome == s.QuoteOutcome.PENDING  # fresh quote has no commercial result yet
    assert event.realised_vs_underwritten_pct is None  # None until M&V
    assert event.created_at is not None  # time-to-quote derives from event timestamps


def test_win_loss_reason_is_recordable(make_site) -> None:
    # The Brief logs "win/loss with reason" — a lost quote records why.
    site = make_site()
    quote = s.QuoteRecord(
        quote_id="qr",
        quote_mode=s.QuoteMode.INDICATIVE,
        site_id=site.site_id,
        data_tier=site.data_tier,
    )
    event = emit_quote_telemetry(
        site=site,
        quote=quote,
        outcome=s.QuoteOutcome.LOST,
        outcome_reason="installer chose a cheaper competitor quote",
    )
    assert event.outcome == s.QuoteOutcome.LOST
    assert event.outcome_reason == "installer chose a cheaper competitor quote"
    assert "outcome_reason" in s.TelemetryEvent.model_fields


def test_outcome_and_realised_are_recordable_when_known(make_site) -> None:
    site = make_site()
    quote = s.QuoteRecord(
        quote_id="q2",
        quote_mode=s.QuoteMode.UNDERWRITTEN,
        site_id=site.site_id,
        data_tier=site.data_tier,
        warnings=["t2 ok"],
    )
    event = emit_quote_telemetry(
        site=site,
        quote=quote,
        outcome=s.QuoteOutcome.WON,
        realised_vs_underwritten_pct=96.5,
    )
    assert event.outcome == s.QuoteOutcome.WON
    assert event.realised_vs_underwritten_pct == 96.5


def test_manual_benchmark_event_carries_the_same_fields() -> None:
    record = create_manual_benchmark_record(
        benchmark_id="b1",
        site_name="Acme",
        contributor="Olly",
        date=date(2026, 6, 1),
        data_tier=s.DataTier.T1_PARTIAL_HALF_HOURLY,
        human_minutes=180.0,
        number_of_back_and_forth_iterations=3,
        quote_outcome=s.QuoteOutcome.SENT,
    )
    event = convert_benchmark_to_telemetry_event(record)
    assert event.data_tier == s.DataTier.T1_PARTIAL_HALF_HOURLY
    assert event.human_minutes == 180.0
    assert event.installer_iterations == 3
    assert event.outcome == s.QuoteOutcome.SENT  # shared outcome vocabulary with system quotes
    assert event.created_at.date() == date(2026, 6, 1)
