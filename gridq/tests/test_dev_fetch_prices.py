"""Tests for the Elexon price-fetch dev tool (:mod:`gridq.dev.fetch_prices`).

The tool is off the pricing path and these tests never touch the network: the single HTTP
seam (:func:`fetch_prices.fetch`) is monkeypatched to return a small, hand-written payload
shaped like a real Elexon Insights MID response. Coverage:

- the pure transform maps settlement period → correct half-hour timestamp, prices → floats,
  and stamps the source/version/created_at labels;
- gaps (missing prices) are preserved as empty, never fabricated or filled;
- the output round-trips through the real ``load_market_forecast_csv`` loader;
- ``main()`` writes a parseable CSV with the HTTP seam stubbed;
- identical records + identical ``created_at`` produce byte-identical CSV;
- a network failure is reported and returns non-zero without writing a file.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from gridq.dev import fetch_prices
from gridq.dev.fetch_prices import (
    FetchError,
    build_dataset_url,
    build_forecast_version,
    count_gaps,
    default_source_label,
    extract_records,
    filter_by_provider,
    records_to_rows,
    rows_to_csv_text,
    settlement_period_to_timestamp,
)
from gridq.ingestion import load_market_forecast_csv

# A captured-shape Elexon Insights MID payload (4 records; SP4 has no price = a gap).
SAMPLE_PAYLOAD = {
    "data": [
        {
            "dataset": "MID",
            "dataProvider": "APXMIDP",
            "settlementDate": "2025-04-01",
            "settlementPeriod": 1,
            "startTime": "2025-03-31T23:00:00Z",
            "price": 85.5,
            "volume": 1200.0,
        },
        {
            "dataset": "MID",
            "dataProvider": "APXMIDP",
            "settlementDate": "2025-04-01",
            "settlementPeriod": 2,
            "startTime": "2025-03-31T23:30:00Z",
            "price": 72.0,
            "volume": 1100.0,
        },
        {
            "dataset": "MID",
            "dataProvider": "APXMIDP",
            "settlementDate": "2025-04-01",
            "settlementPeriod": 3,
            "startTime": "2025-04-01T00:00:00Z",
            "price": -15.0,
            "volume": 900.0,
        },
        {
            "dataset": "MID",
            "dataProvider": "APXMIDP",
            "settlementDate": "2025-04-01",
            "settlementPeriod": 4,
            "startTime": "2025-04-01T00:30:00Z",
            "price": None,
            "volume": None,
        },
    ]
}

CREATED_AT = datetime(2026, 7, 17, 9, 0, 0)
SOURCE = "elexon_insights_mid"
VERSION = "elexon_MID_2025-04-01_2025-04-01"


def _stub_fetch(payload: object) -> object:
    """Build a drop-in for ``fetch_prices.fetch`` returning ``payload`` (no network)."""

    def _fetch(url: str, *, timeout: float = 0.0) -> object:
        return payload

    return _fetch


# --- Settlement-period mapping ----------------------------------------------


def test_settlement_period_to_timestamp_standard_day() -> None:
    day = date(2025, 4, 1)
    assert settlement_period_to_timestamp(day, 1) == datetime(2025, 4, 1, 0, 0)
    assert settlement_period_to_timestamp(day, 3) == datetime(2025, 4, 1, 1, 0)
    assert settlement_period_to_timestamp(day, 48) == datetime(2025, 4, 1, 23, 30)


@pytest.mark.parametrize("period", [0, -1, 51, 99])
def test_settlement_period_out_of_range_raises(period: int) -> None:
    with pytest.raises(ValueError):
        settlement_period_to_timestamp(date(2025, 4, 1), period)


# --- Pure transform ----------------------------------------------------------


def test_records_to_rows_maps_period_price_and_labels() -> None:
    rows = records_to_rows(
        extract_records(SAMPLE_PAYLOAD),
        created_at=CREATED_AT,
        source=SOURCE,
        forecast_version=VERSION,
    )
    assert len(rows) == 4
    # SP1 -> 00:00, SP3 -> 01:00 (the documented mapping)
    assert rows[0]["timestamp"] == "2025-04-01T00:00:00"
    assert rows[1]["timestamp"] == "2025-04-01T00:30:00"
    assert rows[2]["timestamp"] == "2025-04-01T01:00:00"
    assert rows[3]["timestamp"] == "2025-04-01T01:30:00"
    # prices are floats, negatives kept verbatim
    assert rows[0]["price_gbp_per_mwh"] == pytest.approx(85.5)
    assert rows[2]["price_gbp_per_mwh"] == pytest.approx(-15.0)
    # labels stamped on every row
    assert all(r["source"] == SOURCE for r in rows)
    assert all(r["forecast_version"] == VERSION for r in rows)
    assert all(r["created_at"] == "2026-07-17T09:00:00" for r in rows)


def test_missing_price_is_a_gap_never_fabricated() -> None:
    rows = records_to_rows(
        extract_records(SAMPLE_PAYLOAD),
        created_at=CREATED_AT,
        source=SOURCE,
        forecast_version=VERSION,
    )
    assert rows[3]["price_gbp_per_mwh"] is None  # SP4 had no price
    assert count_gaps(rows) == 1


def test_malformed_records_are_skipped_not_placed() -> None:
    payload = {
        "data": [
            {"settlementDate": "2025-04-01", "settlementPeriod": 1, "price": 85.5},
            {"settlementDate": "2025-04-01", "settlementPeriod": 99, "price": 50.0},  # bad SP
            {"settlementDate": "2025-04-01", "price": 50.0},  # no period
            {"settlementPeriod": 2, "price": 50.0},  # no date
        ]
    }
    rows = records_to_rows(
        extract_records(payload), created_at=CREATED_AT, source=SOURCE, forecast_version=VERSION
    )
    assert len(rows) == 1
    assert rows[0]["timestamp"] == "2025-04-01T00:00:00"


def test_rows_are_sorted_by_timestamp() -> None:
    payload = {
        "data": [
            {"settlementDate": "2025-04-01", "settlementPeriod": 5, "price": 3.0},
            {"settlementDate": "2025-04-01", "settlementPeriod": 1, "price": 1.0},
            {"settlementDate": "2025-04-01", "settlementPeriod": 3, "price": 2.0},
        ]
    }
    rows = records_to_rows(
        extract_records(payload), created_at=CREATED_AT, source=SOURCE, forecast_version=VERSION
    )
    timestamps = [r["timestamp"] for r in rows]
    assert timestamps == sorted(timestamps)
    assert timestamps[0] == "2025-04-01T00:00:00"


# --- Provider filter & payload extraction -----------------------------------


def test_provider_filter_keeps_one_reference_series() -> None:
    payload = {
        "data": [
            {
                "settlementDate": "2025-04-01",
                "settlementPeriod": 1,
                "price": 85.5,
                "dataProvider": "APXMIDP",
            },
            {
                "settlementDate": "2025-04-01",
                "settlementPeriod": 1,
                "price": 83.0,
                "dataProvider": "N2EXMIDP",
            },
        ]
    }
    records = extract_records(payload)
    apx = filter_by_provider(records, "APXMIDP")
    assert len(apx) == 1
    assert apx[0]["dataProvider"] == "APXMIDP"
    assert len(filter_by_provider(records, "all")) == 2
    assert len(filter_by_provider(records, None)) == 2


def test_extract_records_rejects_unknown_shape() -> None:
    with pytest.raises(FetchError):
        extract_records({"unexpected": 123})
    with pytest.raises(FetchError):
        extract_records("not an object or list")


# --- Round trip through the real loader --------------------------------------


def test_output_round_trips_through_market_forecast_loader(tmp_path: Path) -> None:
    rows = records_to_rows(
        extract_records(SAMPLE_PAYLOAD),
        created_at=CREATED_AT,
        source=SOURCE,
        forecast_version=VERSION,
    )
    csv_path = tmp_path / "rt_wholesale_hh.csv"
    csv_path.write_text(rows_to_csv_text(rows), encoding="utf-8")

    forecast = load_market_forecast_csv(str(csv_path))
    assert len(forecast.points) == 4
    assert forecast.points[0].price_gbp_per_mwh == pytest.approx(85.5)
    assert forecast.points[2].price_gbp_per_mwh == pytest.approx(-15.0)
    assert forecast.points[3].price_gbp_per_mwh is None  # gap preserved end to end
    assert forecast.source == SOURCE
    assert forecast.forecast_version == VERSION


# --- CLI orchestration (network stubbed) ------------------------------------


def test_main_writes_parseable_csv_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_prices, "fetch", _stub_fetch(SAMPLE_PAYLOAD))
    out = tmp_path / "real" / "rt_wholesale_hh.csv"  # nested dir must be created
    rc = fetch_prices.main(
        [
            "--start",
            "2025-04-01",
            "--end",
            "2025-04-01",
            "--out",
            str(out),
            "--created-at",
            "2026-07-17T09:00:00",
        ]
    )
    assert rc == 0
    assert out.exists()
    forecast = load_market_forecast_csv(str(out))
    assert len(forecast.points) == 4
    assert forecast.source == "elexon_insights_mid"


def test_chunked_fetch_dedups_overlapping_records(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fetch(url: str, *, timeout: float = 0.0) -> object:
        calls.append(url)
        return SAMPLE_PAYLOAD

    monkeypatch.setattr(fetch_prices, "fetch", _fetch)
    # 10-day span, 7-day chunks => 2 requests; identical payloads dedupe to 4 records, not 8.
    records = fetch_prices.fetch_records(date(2025, 4, 1), date(2025, 4, 10), chunk_days=7)
    assert len(calls) == 2
    assert len(records) == 4


def test_main_reports_network_failure_without_fabricating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(url: str, *, timeout: float = 0.0) -> object:
        raise FetchError("network unreachable in test")

    monkeypatch.setattr(fetch_prices, "fetch", _boom)
    out = tmp_path / "should_not_exist.csv"
    rc = fetch_prices.main(["--start", "2025-04-01", "--end", "2025-04-01", "--out", str(out)])
    assert rc == 1
    assert not out.exists()  # nothing written on failure
    assert "network unreachable in test" in capsys.readouterr().err


# --- Determinism -------------------------------------------------------------


def test_identical_input_and_created_at_give_identical_bytes() -> None:
    records = extract_records(SAMPLE_PAYLOAD)
    first = rows_to_csv_text(
        records_to_rows(records, created_at=CREATED_AT, source=SOURCE, forecast_version=VERSION)
    )
    second = rows_to_csv_text(
        records_to_rows(records, created_at=CREATED_AT, source=SOURCE, forecast_version=VERSION)
    )
    assert first == second
    assert first.encode("utf-8") == second.encode("utf-8")


# --- Documented endpoint / labels (pins the contract for confirmation) -------


def test_url_and_labels_match_documented_endpoint() -> None:
    url = build_dataset_url(date(2025, 4, 1), date(2025, 4, 7))
    assert url == (
        "https://data.elexon.co.uk/bmrs/api/v1/datasets/MID"
        "?from=2025-04-01T00%3A00Z&to=2025-04-07T23%3A59Z&format=json"
    )
    assert default_source_label("MID") == "elexon_insights_mid"
    assert (
        build_forecast_version("MID", date(2025, 4, 1), date(2025, 12, 8))
        == "elexon_MID_2025-04-01_2025-12-08"
    )
