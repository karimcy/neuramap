"""TelemetryEvent — one logged event in a quote's life (the cost-compression dataset)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from gridq.schemas.enums import DataTier, QuoteOutcome
from gridq.schemas.provenance import GridQModel


class TelemetryEvent(GridQModel):
    """A single telemetry event. Append-only; time-to-quote and human-minutes derive
    from a quote's events (the gap between an intake event and the quote-created event)."""

    event_id: str
    quote_id: str
    event_type: str
    data_tier: DataTier | None = None
    human_minutes: float | None = Field(default=None, ge=0)
    contributor: str | None = None
    installer_iterations: int | None = Field(default=None, ge=0)
    missing_data_count: int | None = Field(default=None, ge=0)
    #: Commercial outcome of the quote — the hit-rate axis. ``PENDING`` until the deal resolves.
    outcome: QuoteOutcome = QuoteOutcome.PENDING
    #: Free-text reason for a win/loss (the Brief's "win/loss with reason"). ``None`` until known.
    outcome_reason: str | None = None
    #: Realised performance as a % of the underwritten figure (the underwriting-error axis).
    #: ``None`` until measurement & verification (M&V) supplies a realised number — never guessed.
    realised_vs_underwritten_pct: float | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
