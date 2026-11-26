"""Tests for `gridq.quotes.telemetry` — a TelemetryEvent emitted from the first quote (X.3)."""

from __future__ import annotations

from collections.abc import Callable

import gridq.schemas as s
from gridq.ingestion import populate_missing_data
from gridq.quotes import QUOTE_CREATED_EVENT_TYPE, emit_quote_telemetry


def _quote(site: s.SiteModel) -> s.QuoteRecord:
    return s.QuoteRecord(
        quote_id="quote_test",
        quote_mode=s.QuoteMode.DRAFT,
        site_id=site.site_id,
        data_tier=site.data_tier,
        warnings=["baseline: not bankable"],
        status_history=[],
    )


def test_event_maps_quote_and_counts_populated_asks(make_site: Callable[..., s.SiteModel]):
    site = populate_missing_data(make_site())
    quote = _quote(site)

    event = emit_quote_telemetry(quote, site, contributor="Olly")

    assert event.event_type == QUOTE_CREATED_EVENT_TYPE
    assert event.quote_id == quote.quote_id
    assert event.data_tier == site.data_tier
    assert event.contributor == "Olly"
    assert event.missing_data_count == len(site.missing_data) > 0
    assert event.event_id == f"{quote.quote_id}:{QUOTE_CREATED_EVENT_TYPE}"
    # Measured-only fields stay None until captured (never guessed).
    assert event.human_minutes is None and event.installer_iterations is None


def test_optional_measured_fields_passthrough(make_site: Callable[..., s.SiteModel]):
    site = make_site()
    event = emit_quote_telemetry(
        _quote(site),
        site,
        event_type="manual_followup",
        human_minutes=12.5,
        installer_iterations=3,
        notes="two rounds with the installer",
    )
    assert event.event_type == "manual_followup"
    assert event.human_minutes == 12.5
    assert event.installer_iterations == 3
    assert event.missing_data_count == 0  # raw site, no asks populated
