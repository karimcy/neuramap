"""Market / wholesale price-forecast ingestion (M0) — prepared for Modo files.

Loads a wholesale price forecast (e.g. a Modo Energy market/revenue forecast export) from
CSV or JSON into a `MarketForecast`, validates it, summarises it, and pins it with a content
hash for provenance.

**Gated upside, never the priced floor.** Wholesale/P415 is gated upside until the
route-to-market partner/API is known (BLOCKERS B002; CLAUDE.md scope). This loader only
*ingests* a forecast file the user supplies; it prices nothing.

Accepted shape (full spec: docs/input_data_formats.md):

- **Required:** ``timestamp`` and ``price_gbp_per_mwh``.
- **Optional:** ``scenario``, ``source``, ``forecast_version``, ``created_at``.

Hard rules:

- **No generated prices.** A forecast is only ever loaded from a file the user provides.
- **No interpolation unless explicitly requested and labelled.** With the default
  ``allow_interpolation=False`` a missing price stays missing and raises a warning. With
  ``allow_interpolation=True`` interior gaps are filled, each filled point is marked
  ``interpolated=True``, and the forecast is flagged ``interpolated=True`` — never silent,
  never extrapolated beyond the known range.
- **Source / version / hash are recorded** so a quote can cite exactly which forecast it used.

.. note::
   The exact Modo export schema (column names, scenario labels, units, file layout) is not
   yet confirmed — see OPEN_COMMENTS. The alias map below is a best-effort starting point;
   confirm against a real Modo file before relying on it.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

from gridq.ingestion.validators import (
    MarketForecastError,
    Severity,
    ValidationReport,
    require_columns,
)
from gridq.provenance.hashing import hash_file, hash_json
from gridq.schemas.provenance import GridQModel

# Canonical column → recognised aliases (case-insensitive, stripped).
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": (
        "timestamp",
        "datetime",
        "date_time",
        "settlement_datetime",
        "period_start",
        "settlement_period_start",
        "time",
        "ts",
    ),
    "price_gbp_per_mwh": (
        "price_gbp_per_mwh",
        "price_gbp_mwh",
        "gbp_per_mwh",
        "wholesale_price_gbp_per_mwh",
        "price_gbp",
        "price",
    ),
    "scenario": ("scenario", "case", "scenario_name"),
    "source": ("source", "provider", "data_source"),
    "forecast_version": ("forecast_version", "version", "vintage"),
    "created_at": ("created_at", "created", "generated_at", "run_date", "forecast_date"),
}

# JSON keys that may hold the array of forecast points.
_JSON_ARRAY_KEYS = ("points", "data", "series", "forecast", "prices", "rows")


class MarketForecastPoint(GridQModel):
    """One forecast point: a timestamp, a price (£/MWh) and an optional scenario label."""

    timestamp: datetime
    price_gbp_per_mwh: float | None = None
    scenario: str | None = None
    interpolated: bool = False


class MarketForecast(GridQModel):
    """A loaded wholesale price forecast plus its provenance metadata.

    ``content_hash`` pins the timestamp/price/scenario content; ``file_sha256`` pins the
    literal source-file bytes. Both are recorded so a quote can cite the exact forecast.
    """

    points: list[MarketForecastPoint] = Field(default_factory=list)
    source: str | None = None
    forecast_version: str | None = None
    created_at: datetime | None = None
    file_ref: str | None = None
    file_sha256: str | None = None
    content_hash: str | None = None
    interpolated: bool = False
    report: ValidationReport = Field(default_factory=ValidationReport)


class MarketForecastSummary(GridQModel):
    """A serialisable summary of a `MarketForecast`."""

    point_count: int
    priced_point_count: int  # points with a non-null price
    missing_price_count: int
    scenarios: list[str]
    start: datetime | None = None
    end: datetime | None = None
    min_price_gbp_per_mwh: float | None = None
    max_price_gbp_per_mwh: float | None = None
    mean_price_gbp_per_mwh: float | None = None
    negative_price_count: int = 0
    source: str | None = None
    forecast_version: str | None = None
    created_at: datetime | None = None
    content_hash: str | None = None
    file_sha256: str | None = None
    interpolated: bool = False
    report: ValidationReport = Field(default_factory=ValidationReport)


def _canonicalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    lookup: dict[str, str] = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            lookup[alias] = canonical
    rename = {
        col: lookup.get(str(col).strip().lower(), str(col).strip().lower()) for col in df.columns
    }
    return df.rename(columns=rename)


def _parse_timestamps(series: pd.Series, *, context: str) -> pd.Series:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        parsed = pd.to_datetime(series, errors="coerce", utc=False)
    bad = parsed.isna() & series.notna()
    if bool(bad.any()):
        offenders = series[bad].astype(str).unique().tolist()[:5]
        raise MarketForecastError(
            f"{context}: {int(bad.sum())} unparseable timestamp(s); first offenders: "
            f"{offenders}. Timestamps are never guessed."
        )
    if bool(parsed.isna().any()):
        raise MarketForecastError(f"{context}: empty timestamp value(s) present.")
    return parsed


def _first_non_null(series: pd.Series) -> str | None:
    non_null = series.dropna()
    return str(non_null.iloc[0]) if not non_null.empty else None


def _build_forecast(
    df: pd.DataFrame,
    *,
    context: str,
    source: str | None,
    forecast_version: str | None,
    created_at: str | datetime | None,
    file_ref: str | None,
    file_sha256: str | None,
    allow_interpolation: bool,
) -> MarketForecast:
    """Shared CSV/JSON → MarketForecast builder. Performs no value invention."""
    if df.empty:
        raise MarketForecastError(f"{context}: no forecast rows present.")

    df = _canonicalise_columns(df)
    require_columns(
        list(df.columns),
        required=["timestamp", "price_gbp_per_mwh"],
        context=context,
        error_cls=MarketForecastError,
    )

    df = df.copy()
    df["timestamp"] = _parse_timestamps(df["timestamp"], context=context)
    df["price_gbp_per_mwh"] = pd.to_numeric(df["price_gbp_per_mwh"], errors="coerce")
    df = df.sort_values("timestamp", kind="stable").reset_index(drop=True)

    # Forecast-level metadata: explicit args win; otherwise pull from a column if present.
    src = source or (_first_non_null(df["source"]) if "source" in df.columns else None)
    ver = forecast_version or (
        _first_non_null(df["forecast_version"]) if "forecast_version" in df.columns else None
    )
    created = created_at
    if created is None and "created_at" in df.columns:
        created = _first_non_null(df["created_at"])

    interpolated_mask = pd.Series(False, index=df.index)
    forecast_interpolated = False
    if allow_interpolation and bool(df["price_gbp_per_mwh"].isna().any()):
        before = df["price_gbp_per_mwh"].isna()
        # Interior gaps only — never extrapolate beyond the known range.
        df["price_gbp_per_mwh"] = df["price_gbp_per_mwh"].interpolate(
            method="linear", limit_area="inside"
        )
        interpolated_mask = before & df["price_gbp_per_mwh"].notna()
        forecast_interpolated = bool(interpolated_mask.any())

    scenarios = df["scenario"] if "scenario" in df.columns else pd.Series([None] * len(df))

    points = [
        MarketForecastPoint(
            timestamp=row_ts.to_pydatetime(),
            price_gbp_per_mwh=(None if pd.isna(price) else float(price)),
            scenario=(None if pd.isna(scen) else str(scen)),
            interpolated=bool(interpolated_mask.iloc[i]),
        )
        for i, (row_ts, price, scen) in enumerate(
            zip(df["timestamp"], df["price_gbp_per_mwh"], scenarios, strict=False)
        )
    ]

    forecast = MarketForecast(
        points=points,
        source=src,
        forecast_version=ver,
        created_at=_coerce_dt(created),
        file_ref=file_ref,
        file_sha256=file_sha256,
        interpolated=forecast_interpolated,
    )
    forecast.content_hash = hash_market_forecast(forecast)
    forecast.report = validate_market_forecast(forecast)
    return forecast


def _coerce_dt(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.to_pydatetime()


def load_market_forecast_csv(
    path: str | Path,
    *,
    source: str | None = None,
    forecast_version: str | None = None,
    created_at: str | datetime | None = None,
    allow_interpolation: bool = False,
) -> MarketForecast:
    """Load a wholesale price forecast from CSV. Raises if the price column is absent.

    Explicit ``source`` / ``forecast_version`` / ``created_at`` override any same-named
    column. With ``allow_interpolation=False`` (default) missing prices stay missing and are
    flagged — nothing is generated or interpolated.
    """
    try:
        df = pd.read_csv(path)
    except FileNotFoundError as exc:
        raise MarketForecastError(f"market forecast CSV not found: {path}") from exc
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError, OSError) as exc:
        raise MarketForecastError(f"could not read market forecast CSV {path}: {exc}") from exc

    return _build_forecast(
        df,
        context=f"market forecast CSV {path}",
        source=source,
        forecast_version=forecast_version,
        created_at=created_at,
        file_ref=str(path),
        file_sha256=hash_file(path),
        allow_interpolation=allow_interpolation,
    )


def load_market_forecast_json(
    path: str | Path,
    *,
    source: str | None = None,
    forecast_version: str | None = None,
    created_at: str | datetime | None = None,
    allow_interpolation: bool = False,
) -> MarketForecast:
    """Load a wholesale price forecast from JSON.

    Accepts either a top-level array of point objects, or an object carrying forecast-level
    metadata (``source`` / ``forecast_version`` / ``created_at``) and an array under one of
    ``points``/``data``/``series``/``forecast``/``prices``/``rows``. Raises if the price
    field is absent from the points.
    """
    try:
        raw_text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise MarketForecastError(f"market forecast JSON not found: {path}") from exc
    except OSError as exc:
        raise MarketForecastError(f"could not read market forecast JSON {path}: {exc}") from exc

    try:
        payload: Any = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise MarketForecastError(f"market forecast JSON {path} is not valid JSON: {exc}") from exc

    records, meta = _extract_json_records(payload, context=f"market forecast JSON {path}")
    df = pd.DataFrame.from_records(records)

    return _build_forecast(
        df,
        context=f"market forecast JSON {path}",
        source=source or meta.get("source"),
        forecast_version=forecast_version or meta.get("forecast_version"),
        created_at=created_at or meta.get("created_at"),
        file_ref=str(path),
        file_sha256=hash_file(path),
        allow_interpolation=allow_interpolation,
    )


def _extract_json_records(
    payload: Any, *, context: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pull (records, forecast-level metadata) out of a JSON payload."""
    if isinstance(payload, list):
        return list(payload), {}
    if isinstance(payload, Mapping):
        for key in _JSON_ARRAY_KEYS:
            if isinstance(payload.get(key), list):
                meta = {
                    k: payload[k]
                    for k in ("source", "forecast_version", "created_at")
                    if k in payload
                }
                return list(payload[key]), meta
        raise MarketForecastError(
            f"{context}: object payload has no forecast array under any of {_JSON_ARRAY_KEYS}."
        )
    raise MarketForecastError(
        f"{context}: expected a JSON array of points or an object containing one."
    )


def validate_market_forecast(series: MarketForecast) -> ValidationReport:
    """Validate a loaded forecast. Returns a `ValidationReport`; raises nothing.

    Flags: missing prices, duplicate/non-monotonic timestamps, and absent provenance
    (source / version). Negative prices are recorded as INFO only — GB wholesale prices
    legitimately go negative, so they are never treated as an error.
    """
    report = ValidationReport()
    points = series.points

    if not points:
        report.add("forecast.empty", Severity.ERROR, "Forecast has no points.")
        return report

    missing = sum(1 for p in points if p.price_gbp_per_mwh is None)
    if missing:
        report.add(
            "forecast.missing_prices",
            Severity.WARNING,
            f"{missing} forecast point(s) have no price; missing prices are never generated "
            "or interpolated unless explicitly requested (allow_interpolation=True).",
            detail={"count": str(missing)},
        )

    timestamps = [p.timestamp for p in points]
    duplicates = len(timestamps) - len(set(timestamps))
    if duplicates:
        report.add(
            "forecast.duplicate_timestamps",
            Severity.WARNING,
            f"{duplicates} duplicate forecast timestamp(s) detected.",
            detail={"count": str(duplicates)},
        )
    if timestamps != sorted(timestamps):
        report.add(
            "forecast.non_monotonic",
            Severity.INFO,
            "Forecast timestamps are not strictly increasing after sort (duplicates present).",
        )

    negatives = sum(
        1 for p in points if p.price_gbp_per_mwh is not None and p.price_gbp_per_mwh < 0
    )
    if negatives:
        report.add(
            "forecast.negative_prices",
            Severity.INFO,
            f"{negatives} negative price point(s) — legitimate for GB wholesale; recorded.",
            detail={"count": str(negatives)},
        )

    if series.interpolated:
        filled = sum(1 for p in points if p.interpolated)
        report.add(
            "forecast.interpolated",
            Severity.WARNING,
            f"{filled} price point(s) were interpolated (explicitly requested). The forecast "
            "is labelled interpolated=True and must be presented as such.",
            detail={"count": str(filled)},
        )

    if not series.source:
        report.add(
            "forecast.no_source",
            Severity.WARNING,
            "Forecast source is not recorded; a quote must be able to cite where the price "
            "forecast came from.",
            field="source",
        )
    if not series.forecast_version:
        report.add(
            "forecast.no_version",
            Severity.WARNING,
            "Forecast version/vintage is not recorded; needed to pin which forecast was used.",
            field="forecast_version",
        )
    return report


def hash_market_forecast(series: MarketForecast) -> str:
    """Return a deterministic ``sha256:`` hash of the forecast's content.

    Hashes only the (timestamp, price, scenario) content in canonical order — independent of
    file format or row order — so the same curve always yields the same hash. This is the
    provenance pin for "which forecast did this quote use".
    """
    canonical = sorted(
        (
            {
                "timestamp": p.timestamp.isoformat(),
                "price_gbp_per_mwh": p.price_gbp_per_mwh,
                "scenario": p.scenario,
            }
            for p in series.points
        ),
        key=lambda d: (d["timestamp"], d["scenario"] or ""),
    )
    return hash_json(canonical)


def summarise_market_forecast(series: MarketForecast) -> MarketForecastSummary:
    """Summarise a loaded forecast (counts, price range, scenarios, provenance)."""
    points = series.points
    prices = [p.price_gbp_per_mwh for p in points if p.price_gbp_per_mwh is not None]
    timestamps = [p.timestamp for p in points]
    scenarios = sorted({p.scenario for p in points if p.scenario is not None})

    return MarketForecastSummary(
        point_count=len(points),
        priced_point_count=len(prices),
        missing_price_count=len(points) - len(prices),
        scenarios=scenarios,
        start=min(timestamps) if timestamps else None,
        end=max(timestamps) if timestamps else None,
        min_price_gbp_per_mwh=round(min(prices), 6) if prices else None,
        max_price_gbp_per_mwh=round(max(prices), 6) if prices else None,
        mean_price_gbp_per_mwh=round(sum(prices) / len(prices), 6) if prices else None,
        negative_price_count=sum(1 for p in prices if p < 0),
        source=series.source,
        forecast_version=series.forecast_version,
        created_at=series.created_at,
        content_hash=series.content_hash or hash_market_forecast(series),
        file_sha256=series.file_sha256,
        interpolated=series.interpolated,
        report=series.report,
    )
