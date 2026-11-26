"""Tests for market / wholesale price-forecast ingestion (M0).

Covers: CSV and JSON loads; a missing price column fails cleanly; missing prices create
warnings and are never generated; interpolation only happens when explicitly requested and is
labelled; the content hash is deterministic, order-independent and content-sensitive; the
summary records source/version/hash; and the loader invents no prices.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gridq.ingestion import (
    MarketForecastError,
    hash_market_forecast,
    load_market_forecast_csv,
    load_market_forecast_json,
    summarise_market_forecast,
    validate_market_forecast,
)

CSV_HEADER = "timestamp,price_gbp_per_mwh,scenario,source,forecast_version"


def _write_csv(path: Path, body: str, *, header: str = CSV_HEADER) -> str:
    path.write_text(header + "\n" + body, encoding="utf-8")
    return str(path)


def _good_csv(path: Path) -> str:
    return _write_csv(
        path,
        "2025-01-01T00:00:00,80.5,central,Modo,2025-06-v1\n"
        "2025-01-01T00:30:00,72.0,central,Modo,2025-06-v1\n"
        "2025-01-01T01:00:00,-15.0,central,Modo,2025-06-v1\n",
    )


# --- CSV / JSON load --------------------------------------------------------


def test_market_forecast_csv_loads(tmp_path: Path) -> None:
    forecast = load_market_forecast_csv(_good_csv(tmp_path / "fc.csv"))
    assert len(forecast.points) == 3
    assert forecast.source == "Modo"
    assert forecast.forecast_version == "2025-06-v1"
    assert forecast.content_hash and forecast.content_hash.startswith("sha256:")
    assert forecast.file_sha256 and forecast.file_sha256.startswith("sha256:")


def test_market_forecast_json_array_loads(tmp_path: Path) -> None:
    path = tmp_path / "fc.json"
    payload = [
        {"timestamp": "2025-01-01T00:00:00", "price_gbp_per_mwh": 80.5, "scenario": "central"},
        {"timestamp": "2025-01-01T00:30:00", "price_gbp_per_mwh": 72.0, "scenario": "central"},
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    forecast = load_market_forecast_json(str(path))
    assert len(forecast.points) == 2
    assert forecast.points[0].price_gbp_per_mwh == pytest.approx(80.5)


def test_market_forecast_json_object_with_metadata_loads(tmp_path: Path) -> None:
    path = tmp_path / "fc.json"
    payload = {
        "source": "Modo",
        "forecast_version": "2025-06-v1",
        "created_at": "2025-06-01T00:00:00",
        "points": [
            {"timestamp": "2025-01-01T00:00:00", "price_gbp_per_mwh": 80.5},
            {"timestamp": "2025-01-01T00:30:00", "price_gbp_per_mwh": 72.0},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    forecast = load_market_forecast_json(str(path))
    assert forecast.source == "Modo"
    assert forecast.forecast_version == "2025-06-v1"
    assert forecast.created_at is not None


def test_explicit_source_overrides_columns(tmp_path: Path) -> None:
    forecast = load_market_forecast_csv(
        _good_csv(tmp_path / "fc.csv"), source="OverrideSource", forecast_version="v9"
    )
    assert forecast.source == "OverrideSource"
    assert forecast.forecast_version == "v9"


# --- clean failures ---------------------------------------------------------


def test_missing_price_column_fails_csv(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path / "no_price.csv",
        "2025-01-01T00:00:00,central\n",
        header="timestamp,scenario",
    )
    with pytest.raises(MarketForecastError, match="price_gbp_per_mwh"):
        load_market_forecast_csv(path)


def test_missing_price_column_fails_json(tmp_path: Path) -> None:
    path = tmp_path / "no_price.json"
    path.write_text(
        json.dumps([{"timestamp": "2025-01-01T00:00:00", "scenario": "central"}]),
        encoding="utf-8",
    )
    with pytest.raises(MarketForecastError, match="price_gbp_per_mwh"):
        load_market_forecast_json(str(path))


def test_unparseable_timestamp_fails(tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "bad.csv", "rubbish,80.5,central,Modo,v1\n")
    with pytest.raises(MarketForecastError, match="unparseable"):
        load_market_forecast_csv(path)


def test_bad_json_fails(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(MarketForecastError, match="not valid JSON"):
        load_market_forecast_json(str(path))


# --- missing prices: warned, never generated -------------------------------


def test_missing_prices_create_warnings(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path / "gap.csv",
        "2025-01-01T00:00:00,80.5,central,Modo,v1\n"
        "2025-01-01T00:30:00,,central,Modo,v1\n"
        "2025-01-01T01:00:00,72.0,central,Modo,v1\n",
    )
    forecast = load_market_forecast_csv(path)
    assert any(i.code == "forecast.missing_prices" for i in forecast.report.issues)
    # The gap is NOT filled by default — the price stays missing.
    assert forecast.points[1].price_gbp_per_mwh is None
    assert forecast.interpolated is False


def test_no_interpolation_by_default(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path / "gap.csv",
        "2025-01-01T00:00:00,80.0,central,Modo,v1\n"
        "2025-01-01T00:30:00,,central,Modo,v1\n"
        "2025-01-01T01:00:00,100.0,central,Modo,v1\n",
    )
    forecast = load_market_forecast_csv(path)
    summary = summarise_market_forecast(forecast)
    assert summary.missing_price_count == 1
    assert summary.priced_point_count == 2


def test_interpolation_only_when_requested_and_labelled(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path / "gap.csv",
        "2025-01-01T00:00:00,80.0,central,Modo,v1\n"
        "2025-01-01T00:30:00,,central,Modo,v1\n"
        "2025-01-01T01:00:00,100.0,central,Modo,v1\n",
    )
    forecast = load_market_forecast_csv(path, allow_interpolation=True)
    assert forecast.interpolated is True
    assert forecast.points[1].price_gbp_per_mwh == pytest.approx(90.0)  # linear midpoint
    assert forecast.points[1].interpolated is True
    assert any(i.code == "forecast.interpolated" for i in forecast.report.issues)


def test_interpolation_does_not_extrapolate(tmp_path: Path) -> None:
    # A leading gap (no earlier known value) must NOT be filled — that would be invention.
    path = _write_csv(
        tmp_path / "lead_gap.csv",
        "2025-01-01T00:00:00,,central,Modo,v1\n2025-01-01T00:30:00,100.0,central,Modo,v1\n",
    )
    forecast = load_market_forecast_csv(path, allow_interpolation=True)
    assert forecast.points[0].price_gbp_per_mwh is None  # not extrapolated


# --- hashing ----------------------------------------------------------------


def test_hash_is_deterministic_and_content_sensitive(tmp_path: Path) -> None:
    f1 = load_market_forecast_csv(_good_csv(tmp_path / "a.csv"))
    f2 = load_market_forecast_csv(_good_csv(tmp_path / "b.csv"))
    assert hash_market_forecast(f1) == hash_market_forecast(f2)  # same content → same hash

    f2.points[0].price_gbp_per_mwh = 999.0
    assert hash_market_forecast(f2) != hash_market_forecast(f1)  # changed content → new hash


def test_hash_is_order_independent(tmp_path: Path) -> None:
    forecast = load_market_forecast_csv(_good_csv(tmp_path / "fc.csv"))
    original = hash_market_forecast(forecast)
    forecast.points.reverse()
    assert hash_market_forecast(forecast) == original


# --- summary records provenance --------------------------------------------


def test_summary_records_source_version_and_hash(tmp_path: Path) -> None:
    forecast = load_market_forecast_csv(_good_csv(tmp_path / "fc.csv"))
    summary = summarise_market_forecast(forecast)
    assert summary.source == "Modo"
    assert summary.forecast_version == "2025-06-v1"
    assert summary.content_hash == forecast.content_hash
    assert summary.point_count == 3
    assert summary.negative_price_count == 1  # the -15.0 point
    assert summary.scenarios == ["central"]


def test_validate_flags_absent_provenance(tmp_path: Path) -> None:
    # No source/version columns or args → provenance warnings.
    path = _write_csv(
        tmp_path / "anon.csv",
        "2025-01-01T00:00:00,80.5\n",
        header="timestamp,price_gbp_per_mwh",
    )
    forecast = load_market_forecast_csv(path)
    report = validate_market_forecast(forecast)
    codes = {i.code for i in report.issues}
    assert "forecast.no_source" in codes
    assert "forecast.no_version" in codes
