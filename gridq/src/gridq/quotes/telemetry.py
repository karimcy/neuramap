"""Quote telemetry emission (X.3) — ship a `TelemetryEvent` from the first quote.

Every run produces a telemetry event from day one (not retrofitted): data tier at intake,
the count of outstanding data asks, the contributor, and — when measured — human-minutes and
installer iterations. These rows are the falsifiable cost-compression dataset, and live in the
same `gridq.schemas.TelemetryEvent` shape as the manual-benchmark control group
(`gridq.telemetry.manual_benchmark`), so system quotes and hand-built baselines compare
apples-to-apples.

No number here ever feeds a price; this is perimeter measurement only.
"""

from __future__ import annotations

from gridq.schemas import QuoteOutcome, QuoteRecord, SiteModel, TelemetryEvent

#: Default marker distinguishing a system-generated quote event from a manual benchmark.
QUOTE_CREATED_EVENT_TYPE = "quote_created"


def emit_quote_telemetry(
    quote: QuoteRecord,
    site: SiteModel,
    *,
    event_type: str = QUOTE_CREATED_EVENT_TYPE,
    event_id: str | None = None,
    contributor: str | None = None,
    human_minutes: float | None = None,
    installer_iterations: int | None = None,
    outcome: QuoteOutcome = QuoteOutcome.PENDING,
    outcome_reason: str | None = None,
    realised_vs_underwritten_pct: float | None = None,
    notes: str | None = None,
) -> TelemetryEvent:
    """Emit a `TelemetryEvent` for a freshly assembled quote.

    ``missing_data_count`` is read off the **populated** ``site`` (the ranked data asks already
    wired into ``site.missing_data``), so the event records how much was outstanding at quote
    time. ``human_minutes`` / ``installer_iterations`` are optional measured inputs (left
    ``None`` until captured — never guessed). ``outcome`` defaults to ``PENDING`` at creation (a
    fresh quote has no commercial result yet); ``realised_vs_underwritten_pct`` stays ``None``
    until M&V supplies a realised figure.
    """
    return TelemetryEvent(
        event_id=event_id or f"{quote.quote_id}:{event_type}",
        quote_id=quote.quote_id,
        event_type=event_type,
        data_tier=quote.data_tier,
        human_minutes=human_minutes,
        contributor=contributor,
        installer_iterations=installer_iterations,
        missing_data_count=len(site.missing_data),
        outcome=outcome,
        outcome_reason=outcome_reason,
        realised_vs_underwritten_pct=realised_vs_underwritten_pct,
        notes=notes,
    )
