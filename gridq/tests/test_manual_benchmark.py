"""Tests for the manual quote benchmark (``gridq.telemetry.manual_benchmark``).

The manual benchmark is the human-time control group for the cost-compression thesis;
it is a telemetry/perimeter record and never feeds a price. These tests check the model
validates, requires a real ``human_minutes`` measurement, renders to Markdown, and
converts cleanly into the shared :class:`TelemetryEvent` shape.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from gridq.schemas import DataTier, TelemetryEvent
from gridq.telemetry import manual_benchmark as mb


def _record(**overrides: object) -> mb.ManualBenchmarkRecord:
    kwargs: dict[str, object] = dict(
        benchmark_id="MB-001",
        site_name="Acme Cold Store (LE17)",
        contributor="Oli",
        date="2026-06-20",
        data_tier="T1_PARTIAL_HALF_HOURLY",
        human_minutes=180,
        number_of_back_and_forth_iterations=2,
        missing_data_categories=["12-mo HH", "export capacity", "counterparty no."],
        quote_type="indicative",
        quote_outcome="sent",
        notes="tariff stack reconstructed by hand from a bill",
    )
    kwargs.update(overrides)
    return mb.create_manual_benchmark_record(**kwargs)  # type: ignore[arg-type]


# --- validation ------------------------------------------------------------


def test_manual_benchmark_validates() -> None:
    rec = _record()
    assert rec.benchmark_id == "MB-001"
    assert rec.human_minutes == 180.0
    assert rec.data_tier is DataTier.T1_PARTIAL_HALF_HOURLY
    assert rec.quote_type is mb.ManualQuoteType.INDICATIVE
    assert rec.quote_outcome is mb.ManualQuoteOutcome.SENT
    assert rec.number_of_back_and_forth_iterations == 2
    assert rec.missing_data_categories == ["12-mo HH", "export capacity", "counterparty no."]
    # Strict base model: unknown fields are rejected.
    assert rec.model_config["extra"] == "forbid"


def test_missing_human_minutes_fails() -> None:
    with pytest.raises(ValidationError):
        mb.ManualBenchmarkRecord(
            benchmark_id="MB-002",
            site_name="X",
            contributor="Oli",
            date=date(2026, 6, 20),
            data_tier=DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        )  # type: ignore[call-arg]


def test_zero_or_negative_human_minutes_fails() -> None:
    with pytest.raises(ValidationError):
        _record(human_minutes=0)
    with pytest.raises(ValidationError):
        _record(human_minutes=-5)


def test_unknown_data_tier_fails() -> None:
    with pytest.raises(ValidationError):
        _record(data_tier="T9_MADE_UP")


def test_defaults_are_sensible() -> None:
    rec = mb.create_manual_benchmark_record(
        benchmark_id="MB-003",
        site_name="Y",
        contributor="Olly",
        date="2026-06-21",
        data_tier=DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        human_minutes=45,
    )
    assert rec.number_of_back_and_forth_iterations == 0
    assert rec.missing_data_categories == []
    assert rec.source == "manual_quote"
    assert rec.quote_type is mb.ManualQuoteType.INDICATIVE
    assert rec.quote_outcome is mb.ManualQuoteOutcome.PENDING
    assert rec.linked_quote_id is None


# --- markdown rendering ----------------------------------------------------


def test_markdown_benchmark_renders() -> None:
    md = mb.render_manual_benchmark_markdown(_record())
    lines = md.splitlines()
    assert lines[0].startswith("| benchmark_id |")  # header
    assert set(lines[1]) <= set("|-")  # separator row
    assert "Acme Cold Store (LE17)" in md
    assert "180" in md
    assert "MB-001" in md
    assert "indicative" in md and "sent" in md


def test_markdown_renders_many_with_single_header() -> None:
    md = mb.render_manual_benchmark_markdown([_record(), _record(benchmark_id="MB-009")])
    assert md.count("| benchmark_id |") == 1  # header once
    assert "MB-001" in md and "MB-009" in md


def test_markdown_row_only_omits_header() -> None:
    row = mb.render_manual_benchmark_markdown(_record(), include_header=False)
    assert "benchmark_id |" not in row
    assert row.startswith("| MB-001 |")


def test_markdown_escapes_pipes() -> None:
    rec = _record(notes="cost a|b breakdown")
    md = mb.render_manual_benchmark_markdown(rec, include_header=False)
    assert "a\\|b" in md  # pipe escaped so the table can't break


# --- telemetry conversion --------------------------------------------------


def test_convert_benchmark_to_telemetry_event() -> None:
    rec = _record()
    ev = mb.convert_benchmark_to_telemetry_event(rec)
    assert isinstance(ev, TelemetryEvent)
    assert ev.event_type == "manual_benchmark"
    assert ev.human_minutes == 180.0
    assert ev.installer_iterations == 2
    assert ev.missing_data_count == 3
    assert ev.data_tier is DataTier.T1_PARTIAL_HALF_HOURLY
    assert ev.contributor == "Oli"
    # No linked system quote yet -> stable manual id; created_at derived from the date.
    assert ev.quote_id == "manual:MB-001"
    assert ev.created_at.date() == date(2026, 6, 20)
    assert "outcome=sent" in (ev.notes or "")


def test_convert_uses_linked_quote_id_when_present() -> None:
    ev = mb.convert_benchmark_to_telemetry_event(_record(linked_quote_id="Q-2026-001"))
    assert ev.quote_id == "Q-2026-001"


def test_convert_is_deterministic() -> None:
    rec = _record()
    assert mb.convert_benchmark_to_telemetry_event(rec) == mb.convert_benchmark_to_telemetry_event(
        rec
    )
