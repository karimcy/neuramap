"""Tests for half-hourly (HH) data ingestion (M0).

Covers: a valid HH CSV loads with its row count preserved; missing intervals and duplicate
timestamps are detected (never silently fixed); negative readings are flagged; unparseable
timestamps and a missing import column fail cleanly; the kWh→kW unit transform; the loader
invents no data; and deterministic T0/T1/T2 tier classification.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gridq.ingestion import (
    HalfHourlyCoverage,
    HalfHourlyDataError,
    HalfHourlySummary,
    infer_data_tier_from_hh_summary,
    load_half_hourly_csv,
    summarise_half_hourly_data,
    validate_half_hourly_coverage,
)
from gridq.ingestion.data_tier import T2_MIN_PERIODS
from gridq.schemas.enums import DataTier

START = datetime(2025, 1, 1, 0, 0)


def _write_hh_csv(
    path: Path,
    *,
    n: int = 48,
    header: str = "timestamp,import_kw,export_kw",
    drop: set[int] | None = None,
    duplicate: set[int] | None = None,
    negative_at: set[int] | None = None,
) -> str:
    """Write a synthetic HH CSV (test-only). Returns the path."""
    drop = drop or set()
    duplicate = duplicate or set()
    negative_at = negative_at or set()
    lines = [header]
    for i in range(n):
        if i in drop:
            continue
        ts = (START + timedelta(minutes=30 * i)).isoformat()
        val = -5.0 if i in negative_at else float(100 + i)
        lines.append(f"{ts},{val},0")
        if i in duplicate:
            lines.append(f"{ts},{val},0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


# --- valid load ------------------------------------------------------------


def test_valid_hh_csv_loads_with_row_count_preserved(tmp_path: Path) -> None:
    path = _write_hh_csv(tmp_path / "hh.csv", n=48)
    df = load_half_hourly_csv(path)
    assert len(df) == 48
    assert "timestamp" in df.columns
    assert "import_kw" in df.columns


def test_summary_reports_coverage_and_load_stats(tmp_path: Path) -> None:
    path = _write_hh_csv(tmp_path / "hh.csv", n=48)
    summary = summarise_half_hourly_data(load_half_hourly_csv(path), file_ref=path)
    assert summary.coverage.present_intervals == 48
    assert summary.coverage.missing_interval_count == 0
    assert summary.max_import_kw == pytest.approx(147.0)
    assert summary.import_basis == "power"
    assert summary.warnings == []


# --- missing intervals & duplicates (detected, never fixed) ----------------


def test_missing_intervals_detected(tmp_path: Path) -> None:
    path = _write_hh_csv(tmp_path / "gap.csv", n=48, drop={10, 11})
    df = load_half_hourly_csv(path)
    coverage = validate_half_hourly_coverage(df)
    assert coverage.present_intervals == 46
    assert coverage.expected_intervals == 48
    assert coverage.missing_interval_count == 2
    # The gap is reported, not back-filled: the frame still has exactly 46 rows.
    assert len(df) == 46
    summary = summarise_half_hourly_data(df)
    assert any("missing" in w.lower() for w in summary.warnings)


def test_duplicate_timestamps_flagged(tmp_path: Path) -> None:
    path = _write_hh_csv(tmp_path / "dup.csv", n=48, duplicate={5})
    df = load_half_hourly_csv(path)
    coverage = validate_half_hourly_coverage(df)
    assert coverage.duplicate_timestamp_count == 1
    assert len(df) == 49  # the duplicate row is preserved, not dropped


def test_negative_readings_flagged(tmp_path: Path) -> None:
    path = _write_hh_csv(tmp_path / "neg.csv", n=48, negative_at={7})
    summary = summarise_half_hourly_data(load_half_hourly_csv(path))
    assert summary.negative_import_count == 1
    assert any("negative" in w.lower() for w in summary.warnings)


def test_negative_allowed_when_explicit(tmp_path: Path) -> None:
    path = _write_hh_csv(tmp_path / "neg.csv", n=48, negative_at={7})
    summary = summarise_half_hourly_data(load_half_hourly_csv(path), allow_negative=True)
    assert summary.negative_import_count == 1  # still counted
    assert not any("negative" in w.lower() for w in summary.warnings)  # but not warned


# --- clean failures --------------------------------------------------------


def test_unparseable_timestamps_fail(tmp_path: Path) -> None:
    path = tmp_path / "bad_ts.csv"
    path.write_text("timestamp,import_kw\nnot-a-date,5\n2025-01-01T00:30:00,6\n", encoding="utf-8")
    with pytest.raises(HalfHourlyDataError, match="unparseable"):
        load_half_hourly_csv(str(path))


def test_missing_import_column_fails(tmp_path: Path) -> None:
    path = tmp_path / "no_import.csv"
    path.write_text("timestamp,export_kw\n2025-01-01T00:00:00,5\n", encoding="utf-8")
    with pytest.raises(HalfHourlyDataError, match="import_kw"):
        load_half_hourly_csv(str(path))


def test_missing_file_fails(tmp_path: Path) -> None:
    with pytest.raises(HalfHourlyDataError, match="not found"):
        load_half_hourly_csv(str(tmp_path / "nope.csv"))


def test_empty_file_fails(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("timestamp,import_kw\n", encoding="utf-8")
    with pytest.raises(HalfHourlyDataError):
        load_half_hourly_csv(str(path))


# --- kWh → kW unit transform ----------------------------------------------


def test_import_kwh_is_converted_to_kw(tmp_path: Path) -> None:
    path = tmp_path / "kwh.csv"
    path.write_text(
        "timestamp,import_kwh\n2025-01-01T00:00:00,50\n2025-01-01T00:30:00,60\n",
        encoding="utf-8",
    )
    summary = summarise_half_hourly_data(load_half_hourly_csv(path))
    assert summary.import_basis == "energy"
    # 60 kWh over a 30-minute interval = 120 kW average.
    assert summary.max_import_kw == pytest.approx(120.0)


def test_bare_kwh_column_is_read_as_import_energy(tmp_path: Path) -> None:
    # Common installer/normalised export: a lone `kwh` consumption column.
    path = tmp_path / "bare_kwh.csv"
    path.write_text(
        "timestamp,kwh\n2025-01-01T00:00:00,50\n2025-01-01T00:30:00,60\n",
        encoding="utf-8",
    )
    df = load_half_hourly_csv(path)
    assert "import_kwh" in df.columns  # `kwh` canonicalised to import energy
    summary = summarise_half_hourly_data(df)
    assert summary.import_basis == "energy"
    assert summary.max_import_kw == pytest.approx(120.0)  # 60 kWh / 0.5 h


def test_single_sheet_xlsx_loads_like_csv(tmp_path: Path) -> None:
    import pandas as pd

    path = tmp_path / "hh.xlsx"
    pd.DataFrame(
        {"timestamp": ["2025-01-01 00:00", "2025-01-01 00:30"], "kwh": [50.0, 60.0]}
    ).to_excel(path, index=False, sheet_name="data")
    df = load_half_hourly_csv(path)  # dispatches on .xlsx
    assert "import_kwh" in df.columns and len(df) == 2
    summary = summarise_half_hourly_data(df)
    assert summary.import_basis == "energy"
    assert summary.max_import_kw == pytest.approx(120.0)


def test_multi_sheet_xlsx_is_rejected_not_guessed(tmp_path: Path) -> None:
    import pandas as pd

    path = tmp_path / "multi.xlsx"
    frame = pd.DataFrame({"timestamp": ["2025-01-01 00:00"], "kwh": [50.0]})
    with pd.ExcelWriter(path) as writer:
        frame.to_excel(writer, index=False, sheet_name="Sheet1")
        frame.to_excel(writer, index=False, sheet_name="Readings")
    with pytest.raises(HalfHourlyDataError, match="never guessed"):
        load_half_hourly_csv(path)


# --- no invented data ------------------------------------------------------


def test_loader_does_not_fill_gaps(tmp_path: Path) -> None:
    path = _write_hh_csv(tmp_path / "gap.csv", n=48, drop={20, 21, 22})
    df = load_half_hourly_csv(path)
    assert len(df) == 45  # 48 − 3 dropped; the loader added no rows to fill the gap
    summary = summarise_half_hourly_data(df)
    assert summary.coverage.missing_interval_count == 3
    assert summary.coverage.present_intervals == 45


# --- deterministic tier classification -------------------------------------


def _summary(
    present: int, completeness: float | None, *, coverage_days: float | None = 365.0
) -> HalfHourlySummary:
    cov = HalfHourlyCoverage(
        row_count=present,
        present_intervals=present,
        expected_intervals=present,
        missing_interval_count=0,
        duplicate_timestamp_count=0,
        completeness_pct=completeness,
        coverage_days=coverage_days,
    )
    return HalfHourlySummary(coverage=cov, import_basis="power", has_export=False)


def test_tier_t0_when_no_summary() -> None:
    assert infer_data_tier_from_hh_summary(None) == DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE


def test_tier_t0_when_trivial_series() -> None:
    assert (
        infer_data_tier_from_hh_summary(_summary(1, 100.0))
        == DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE
    )


def test_tier_t1_for_partial_hh(tmp_path: Path) -> None:
    # A genuine short series (one day) is T1.
    path = _write_hh_csv(tmp_path / "day.csv", n=48)
    summary = summarise_half_hourly_data(load_half_hourly_csv(path))
    assert infer_data_tier_from_hh_summary(summary) == DataTier.T1_PARTIAL_HALF_HOURLY


def test_tier_t1_when_full_period_but_low_completeness() -> None:
    # Enough periods, but completeness below the floor → not yet T2.
    assert (
        infer_data_tier_from_hh_summary(_summary(T2_MIN_PERIODS, 70.0))
        == DataTier.T1_PARTIAL_HALF_HOURLY
    )


def test_tier_t2_for_full_year() -> None:
    assert (
        infer_data_tier_from_hh_summary(_summary(T2_MIN_PERIODS, 99.0))
        == DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY
    )


def test_tier_classification_is_deterministic() -> None:
    summary = _summary(T2_MIN_PERIODS, 95.0)
    results = {infer_data_tier_from_hh_summary(summary) for _ in range(5)}
    assert results == {DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY}


def test_tier_t1_when_dense_short_span_hits_period_floor() -> None:
    # Enough intervals + high completeness, but spanning only ~12 days (a 1-minute file): "≥12
    # months" is a calendar duration, so this must NOT grade T2.
    summary = _summary(T2_MIN_PERIODS, 99.0, coverage_days=12.0)
    assert infer_data_tier_from_hh_summary(summary) == DataTier.T1_PARTIAL_HALF_HOURLY


def test_tier_t1_when_coverage_span_unknown() -> None:
    # Without a measured span we cannot assert ≥12 months, so we do not upgrade to T2.
    summary = _summary(T2_MIN_PERIODS, 99.0, coverage_days=None)
    assert infer_data_tier_from_hh_summary(summary) == DataTier.T1_PARTIAL_HALF_HOURLY
