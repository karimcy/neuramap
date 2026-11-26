"""Tests for the scenario domain models + the ForecastReference contract."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from gridq.ingestion import (
    MarketForecast,
    MarketForecastError,
    MarketForecastPoint,
    hash_market_forecast,
    load_market_forecast_csv,
    load_market_forecast_json,
)
from gridq.scenarios import (
    ForecastReference,
    Scenario,
    ScenarioName,
    ScenarioSet,
    build_forecast_reference,
    forecast_reference_from_market_forecast,
    normalise_weights,
    verify_forecast_reference,
    weights_sum_ok,
)


def _scenario(scenario_id: str, name: ScenarioName, weight: float, **kw: object) -> Scenario:
    return Scenario(scenario_id=scenario_id, name=name, probability_weight=weight, **kw)


def _valid_forecast_ref(**overrides: object) -> ForecastReference:
    base: dict[str, object] = dict(
        source="Modo Energy",
        file_ref="data/forecasts/modo_2026q2.csv",
        forecast_version="modo-2026-q2",
        content_hash="sha256:" + "ab" * 32,
        created_at=datetime(2026, 4, 1, 0, 0, 0),
        price_unit="GBP/MWh",
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2040, 12, 31),
    )
    base.update(overrides)
    return ForecastReference(**base)  # type: ignore[arg-type]


# --- ScenarioSet validation ------------------------------------------------------------


def test_low_central_high_set_validates() -> None:
    s = ScenarioSet(
        scenario_set_id="lch",
        assumption_version="gb_v0_1",
        scenarios=[
            _scenario("low", ScenarioName.LOW, 0.25, price_multiplier=0.9),
            _scenario("central", ScenarioName.CENTRAL, 0.50),
            _scenario("high", ScenarioName.HIGH, 0.25, price_multiplier=1.1),
        ],
    )
    assert [sc.name for sc in s.scenarios] == [
        ScenarioName.LOW,
        ScenarioName.CENTRAL,
        ScenarioName.HIGH,
    ]
    # weights sum to 1.0 → no validation warning
    assert s.validation_warnings == []


def test_invalid_weights_are_warninged_not_silent() -> None:
    s = ScenarioSet(
        scenario_set_id="bad",
        assumption_version="gb_v0_1",
        scenarios=[
            _scenario("low", ScenarioName.LOW, 0.5),
            _scenario("central", ScenarioName.CENTRAL, 0.5),
            _scenario("high", ScenarioName.HIGH, 0.5),  # sums to 1.5
        ],
    )
    assert any("not 1.0" in w for w in s.validation_warnings)


def test_duplicate_scenario_ids_rejected() -> None:
    with pytest.raises(ValidationError):
        ScenarioSet(
            scenario_set_id="dup",
            assumption_version="gb_v0_1",
            scenarios=[
                _scenario("x", ScenarioName.LOW, 0.5),
                _scenario("x", ScenarioName.HIGH, 0.5),
            ],
        )


def test_empty_scenario_set_rejected() -> None:
    with pytest.raises(ValidationError):
        ScenarioSet(scenario_set_id="empty", assumption_version="gb_v0_1", scenarios=[])


def test_probability_weight_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        _scenario("x", ScenarioName.LOW, 1.5)
    with pytest.raises(ValidationError):
        _scenario("x", ScenarioName.LOW, -0.1)


def test_non_positive_multiplier_rejected() -> None:
    with pytest.raises(ValidationError):
        _scenario("x", ScenarioName.LOW, 0.5, price_multiplier=0.0)
    with pytest.raises(ValidationError):
        _scenario("x", ScenarioName.LOW, 0.5, cost_multiplier=-1.0)


def test_scenario_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Scenario(
            scenario_id="x",
            name=ScenarioName.LOW,
            probability_weight=0.5,
            bogus_field=1,  # type: ignore[call-arg]
        )


# --- ForecastReference: hash/source required when used ---------------------------------


def test_forecast_reference_valid() -> None:
    ref = _valid_forecast_ref()
    assert ref.source == "Modo Energy"
    assert ref.content_hash.startswith("sha256:")
    assert ref.file_sha256 is None  # optional; absent on a directly-constructed reference
    assert ref.warnings == []
    # a directly-constructed reference is NOT verified — its hash wasn't checked against a file
    assert ref.verified is False


def test_forecast_reference_requires_source_and_hash() -> None:
    with pytest.raises(ValidationError):
        _valid_forecast_ref(source="   ")  # blank source
    with pytest.raises(ValidationError):
        _valid_forecast_ref(content_hash="")  # missing hash


def test_forecast_reference_rejects_bad_coverage_window() -> None:
    with pytest.raises(ValidationError):
        _valid_forecast_ref(coverage_start=date(2030, 1, 1), coverage_end=date(2029, 1, 1))


def test_forecast_reference_rejects_malformed_hash() -> None:
    # a non-sha256 prefix, a too-short digest, and uppercase hex are all rejected (raise, not warn)
    with pytest.raises(ValidationError):
        _valid_forecast_ref(content_hash="md5:deadbeef")
    with pytest.raises(ValidationError):
        _valid_forecast_ref(content_hash="sha256:deadbeef")  # too short
    with pytest.raises(ValidationError):
        _valid_forecast_ref(content_hash="sha256:" + "AB" * 32)  # uppercase hex


def test_forecast_reference_rejects_malformed_file_sha256() -> None:
    # file_sha256 is optional, but when present it must also be a well-formed sha256 digest
    with pytest.raises(ValidationError):
        _valid_forecast_ref(file_sha256="sha256:deadbeef")  # too short
    # a well-formed file_sha256 is accepted alongside content_hash
    ref = _valid_forecast_ref(file_sha256="sha256:" + "cd" * 32)
    assert ref.file_sha256 == "sha256:" + "cd" * 32


def test_build_and_verify_forecast_reference_against_real_file(tmp_path: object) -> None:
    p = tmp_path / "modo.csv"  # type: ignore[operator]
    p.write_text("ts,price\n2026-01-01T00:00,80.0\n", encoding="utf-8")
    ref = build_forecast_reference(
        file_path=p,
        source="Modo Energy",
        forecast_version="modo-2026-q2",
        price_unit="GBP/MWh",
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2040, 12, 31),
        created_at=datetime(2026, 4, 1),
    )
    assert ref.content_hash.startswith("sha256:")
    assert ref.file_sha256 and ref.file_sha256.startswith("sha256:")  # literal-bytes pin recorded
    assert ref.verified is True  # built from a real, loaded forecast → verified
    assert verify_forecast_reference(ref, p) == []  # matches → no drift

    p.write_text("ts,price\n2026-01-01T00:00,999.0\n", encoding="utf-8")  # tamper
    drift = verify_forecast_reference(ref, p)
    assert drift and "changed" in drift[0]
    assert ref.verified is False  # drift clears the verified flag


# --- MarketForecast -> ForecastReference converter (the ingestion <-> scenarios seam) ---

_GOOD_FORECAST_CSV = (
    "timestamp,price_gbp_per_mwh,scenario,source,forecast_version,created_at\n"
    "2025-01-01T00:00:00,80.5,central,Modo,2025-06-v1,2025-06-01T00:00:00\n"
    "2025-01-01T00:30:00,72.0,central,Modo,2025-06-v1,2025-06-01T00:00:00\n"
    "2025-01-01T01:00:00,-15.0,central,Modo,2025-06-v1,2025-06-01T00:00:00\n"
)


def test_converter_from_loaded_forecast(tmp_path: Path) -> None:
    path = tmp_path / "modo.csv"
    path.write_text(_GOOD_FORECAST_CSV, encoding="utf-8")
    forecast = load_market_forecast_csv(path)

    ref = forecast_reference_from_market_forecast(forecast)

    # hashes carry over 1:1 from ingestion's vocabulary
    assert ref.content_hash == forecast.content_hash
    assert ref.file_sha256 == forecast.file_sha256
    # provenance metadata is taken from the forecast, never invented
    assert ref.source == "Modo"
    assert ref.forecast_version == "2025-06-v1"
    assert ref.file_ref == str(path)
    assert ref.created_at == datetime(2025, 6, 1)
    assert ref.price_unit == "GBP/MWh"  # ingestion prices are £/MWh
    # coverage window derived from the point timestamps
    assert ref.coverage_start == date(2025, 1, 1)
    assert ref.coverage_end == date(2025, 1, 1)
    # loaded from a real file → verified, and re-verifying the same file shows no drift
    assert ref.verified is True
    assert verify_forecast_reference(ref, path) == []


def test_converter_carries_forecast_integrity_warnings(tmp_path: Path) -> None:
    # an interpolated forecast must advertise that caveat on the reference it backs
    path = tmp_path / "gap.csv"
    path.write_text(
        "timestamp,price_gbp_per_mwh,source,forecast_version,created_at\n"
        "2025-01-01T00:00:00,80.0,Modo,v1,2025-06-01T00:00:00\n"
        "2025-01-01T00:30:00,,Modo,v1,2025-06-01T00:00:00\n"
        "2025-01-01T01:00:00,100.0,Modo,v1,2025-06-01T00:00:00\n",
        encoding="utf-8",
    )
    forecast = load_market_forecast_csv(path, allow_interpolation=True)
    ref = forecast_reference_from_market_forecast(forecast)
    assert any("interpolated" in w for w in ref.warnings)


def test_converter_in_memory_forecast_is_unverified() -> None:
    # a forecast not loaded from a file has no file_sha256 → the reference is NOT verified
    forecast = MarketForecast(
        points=[
            MarketForecastPoint(timestamp=datetime(2026, 1, 1, 0, 0), price_gbp_per_mwh=80.0),
            MarketForecastPoint(timestamp=datetime(2026, 1, 1, 0, 30), price_gbp_per_mwh=72.0),
        ],
        source="Modo Energy",
        forecast_version="modo-2026-q2",
        created_at=datetime(2026, 4, 1),
        file_ref="data/forecasts/modo_2026q2.csv",
    )
    ref = forecast_reference_from_market_forecast(forecast)
    assert ref.file_sha256 is None
    assert ref.verified is False
    assert any("file_sha256" in w for w in ref.warnings)
    # content_hash is still a real, recomputed canonical hash of the curve
    assert ref.content_hash == hash_market_forecast(forecast)


def _point() -> MarketForecastPoint:
    return MarketForecastPoint(timestamp=datetime(2026, 1, 1), price_gbp_per_mwh=80.0)


def test_converter_refuses_to_invent_provenance() -> None:
    # each kind of missing-and-not-supplied provenance is refused, never invented
    with pytest.raises(ValueError, match="source"):
        forecast_reference_from_market_forecast(
            MarketForecast(
                points=[_point()],
                forecast_version="v1",
                created_at=datetime(2026, 4, 1),
                file_ref="x.csv",
            )
        )
    with pytest.raises(ValueError, match="forecast_version"):
        forecast_reference_from_market_forecast(
            MarketForecast(
                points=[_point()], source="Modo", created_at=datetime(2026, 4, 1), file_ref="x.csv"
            )
        )
    with pytest.raises(ValueError, match="file_ref"):
        forecast_reference_from_market_forecast(
            MarketForecast(
                points=[_point()],
                source="Modo",
                forecast_version="v1",
                created_at=datetime(2026, 4, 1),
            )
        )
    with pytest.raises(ValueError, match="created_at"):
        forecast_reference_from_market_forecast(
            MarketForecast(
                points=[_point()], source="Modo", forecast_version="v1", file_ref="x.csv"
            )
        )
    # no points and no explicit coverage window → cannot derive coverage, refuse
    with pytest.raises(ValueError, match="coverage window"):
        forecast_reference_from_market_forecast(
            MarketForecast(
                points=[],
                source="Modo",
                forecast_version="v1",
                created_at=datetime(2026, 4, 1),
                file_ref="x.csv",
            )
        )


def test_converter_empty_curve_is_never_verified() -> None:
    # an empty forecast with explicit coverage + a file hash must NOT pass as verified data
    forecast = MarketForecast(
        points=[],
        source="Modo",
        forecast_version="v1",
        created_at=datetime(2026, 4, 1),
        file_ref="x.csv",
        file_sha256="sha256:" + "ab" * 32,
    )
    ref = forecast_reference_from_market_forecast(
        forecast, coverage_start=date(2026, 1, 1), coverage_end=date(2026, 12, 31)
    )
    assert ref.verified is False
    assert any("empty curve" in w for w in ref.warnings)


def test_converter_overrides_are_honoured() -> None:
    # forecast records its own values; every override below differs from them and must win
    forecast = MarketForecast(
        points=[_point()],  # min timestamp date is 2026-01-01
        source="Modo",
        forecast_version="v1",
        created_at=datetime(2026, 4, 1),
        file_ref="x.csv",
    )
    ref = forecast_reference_from_market_forecast(
        forecast,
        price_unit="EUR/MWh",
        source="Override Source",
        forecast_version="override-v2",
        file_ref="overridden/path.csv",
        created_at=datetime(2027, 9, 9),
        coverage_start=date(2025, 12, 31),  # differs from the derived min-timestamp date
        coverage_end=date(2040, 12, 31),
    )
    assert ref.source == "Override Source"
    assert ref.forecast_version == "override-v2"
    assert ref.file_ref == "overridden/path.csv"
    assert ref.created_at == datetime(2027, 9, 9)
    assert ref.price_unit == "EUR/MWh"
    assert ref.coverage_start == date(2025, 12, 31)
    assert ref.coverage_end == date(2040, 12, 31)


def test_json_loaded_forecast_through_the_seam(tmp_path: Path) -> None:
    # the JSON arm of the loader -> converter -> verify seam must work end to end
    path = tmp_path / "modo.json"
    payload = {
        "source": "Modo",
        "forecast_version": "2025-06-v1",
        "created_at": "2025-06-01T00:00:00",
        "points": [
            {"timestamp": "2025-01-01T00:00:00", "price_gbp_per_mwh": 80.5, "scenario": "central"},
            {"timestamp": "2025-01-01T00:30:00", "price_gbp_per_mwh": 72.0, "scenario": "central"},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    forecast = load_market_forecast_json(path)
    ref = forecast_reference_from_market_forecast(forecast)
    assert ref.content_hash == forecast.content_hash
    assert ref.file_sha256 == forecast.file_sha256
    assert ref.verified is True
    assert verify_forecast_reference(ref, path) == []

    # build_forecast_reference must also route a .json file correctly
    built = build_forecast_reference(
        file_path=path,
        source="Modo",
        forecast_version="2025-06-v1",
        coverage_start=date(2025, 1, 1),
        coverage_end=date(2025, 1, 1),
        created_at=datetime(2025, 6, 1),
    )
    assert built.verified is True
    assert verify_forecast_reference(built, path) == []


def test_content_hash_is_format_independent(tmp_path: Path) -> None:
    # the same curve as CSV and as JSON must pin to the SAME content_hash (the docstring promise)
    csv_path = tmp_path / "curve.csv"
    csv_path.write_text(
        "timestamp,price_gbp_per_mwh\n2025-01-01T00:00:00,80.5\n2025-01-01T00:30:00,72.0\n",
        encoding="utf-8",
    )
    json_path = tmp_path / "curve.json"
    json_path.write_text(
        json.dumps(
            [
                {"timestamp": "2025-01-01T00:00:00", "price_gbp_per_mwh": 80.5},
                {"timestamp": "2025-01-01T00:30:00", "price_gbp_per_mwh": 72.0},
            ]
        ),
        encoding="utf-8",
    )
    csv_ref = forecast_reference_from_market_forecast(
        load_market_forecast_csv(csv_path),
        source="Modo",
        forecast_version="v1",
        created_at=datetime(2025, 6, 1),
    )
    json_ref = forecast_reference_from_market_forecast(
        load_market_forecast_json(json_path),
        source="Modo",
        forecast_version="v1",
        created_at=datetime(2025, 6, 1),
    )
    # content hash is format-independent; the literal file bytes are not
    assert csv_ref.content_hash == json_ref.content_hash
    assert csv_ref.file_sha256 != json_ref.file_sha256


def test_verify_reports_missing_file(tmp_path: Path) -> None:
    ref = _valid_forecast_ref()
    ref.verified = True  # prove it gets cleared
    drift = verify_forecast_reference(ref, tmp_path / "does_not_exist.csv")
    assert drift and "missing" in drift[0]
    assert ref.verified is False


def test_verify_reports_unreadable_forecast(tmp_path: Path) -> None:
    ref = _valid_forecast_ref()
    ref.verified = True  # prove it gets cleared
    junk = tmp_path / "junk.csv"
    junk.write_text("foo,bar\n1,2\n", encoding="utf-8")  # no timestamp/price columns
    drift = verify_forecast_reference(ref, junk)
    assert drift and "re-pin" in drift[0]
    assert ref.verified is False


def test_build_rejects_unsupported_suffix(tmp_path: Path) -> None:
    p = tmp_path / "forecast.parquet"
    p.write_text("not really parquet", encoding="utf-8")
    with pytest.raises((ValueError, MarketForecastError), match="format|suffix"):
        build_forecast_reference(
            file_path=p,
            source="Modo",
            forecast_version="v1",
            coverage_start=date(2025, 1, 1),
            coverage_end=date(2025, 1, 1),
            created_at=datetime(2025, 6, 1),
        )


# --- weights helpers --------------------------------------------------------------------


def test_normalise_weights_passthrough_and_normalise() -> None:
    ok = normalise_weights([0.25, 0.5, 0.25])
    assert ok.normalised is False
    assert weights_sum_ok([0.25, 0.5, 0.25])

    off = normalise_weights([1.0, 1.0, 2.0])
    assert off.normalised is True
    assert abs(sum(off.weights) - 1.0) < 1e-12
    assert off.warnings


def test_normalise_weights_non_positive_falls_back_to_equal() -> None:
    res = normalise_weights([0.0, 0.0])
    assert res.normalised is True
    assert res.weights == (0.5, 0.5)
    assert any("equal weights" in w for w in res.warnings)
