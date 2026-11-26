"""Ingestion (M0) — the validated perimeter into the deterministic core.

Turns whatever a site is described by into a validated, *graded* `SiteModel`, and accepts the
two real time-series inputs GridQ underwrites from:

- **real half-hourly commercial load data** (CSV) — the site's own metered consumption/export;
- **real wholesale / Modo price-forecast files** (CSV/JSON) — gated upside, never the floor.

v1 scope (this lane):

- `load_site_model_json` — structured SiteModel JSON → validated `SiteModel`, clean errors.
- `load_half_hourly_csv` / `validate_half_hourly_coverage` / `summarise_half_hourly_data` —
  first-class half-hourly load ingestion + integrity validation + coverage summary.
- `infer_data_tier_from_hh_summary` — deterministic T0/T1/T2 grading from the HH summary.
- `load_market_forecast_csv` / `load_market_forecast_json` / `validate_market_forecast` /
  `hash_market_forecast` / `summarise_market_forecast` — Modo/wholesale forecast ingestion.
- `detect_missing_data` / `build_ranked_data_asks` — the ranked data ask artifact.

**The hard rule (docs/parallel_development_plan.md §5):** ingestion never invents, guesses or
interpolates a missing value. A missing input becomes a typed validation warning and/or a
ranked data ask — never a fabricated number. See docs/ingestion.md.
"""

from __future__ import annotations

from gridq.ingestion.access_log import (
    ACCESS_LOG_ENV,
    HhAccessEvent,
    clear_access_log,
    get_access_log,
    record_hh_access,
)
from gridq.ingestion.assembly import assemble_site_model, populate_missing_data
from gridq.ingestion.data_tier import (
    HALF_HOURLY_PERIODS_PER_DAY,
    T1_MIN_PERIODS,
    T2_MIN_COMPLETENESS_PCT,
    T2_MIN_PERIODS,
    T2_PERIOD_TOLERANCE,
    classify_site_data_tier,
    infer_data_tier_from_hh_summary,
)
from gridq.ingestion.hh_data import (
    HalfHourlyCoverage,
    HalfHourlySummary,
    load_half_hourly_csv,
    summarise_half_hourly_data,
    to_load_data_summary,
    validate_half_hourly_coverage,
)
from gridq.ingestion.improvement_asks import (
    DataAsk,
    build_ranked_data_asks,
    data_asks_to_missing_list,
    format_data_asks,
    rank_missing_data,
)
from gridq.ingestion.loaders import (
    load_site_model_json,
    load_site_model_json_with_report,
)
from gridq.ingestion.market_forecasts import (
    MarketForecast,
    MarketForecastPoint,
    MarketForecastSummary,
    hash_market_forecast,
    load_market_forecast_csv,
    load_market_forecast_json,
    summarise_market_forecast,
    validate_market_forecast,
)
from gridq.ingestion.missing_data import MissingDataItem, detect_missing_data
from gridq.ingestion.validators import (
    HalfHourlyDataError,
    IngestionError,
    MarketForecastError,
    Severity,
    SiteModelLoadError,
    ValidationIssue,
    ValidationReport,
    require_columns,
    validate_site_model,
)

__all__ = [
    # errors
    "IngestionError",
    "SiteModelLoadError",
    "HalfHourlyDataError",
    "MarketForecastError",
    # validation primitives
    "Severity",
    "ValidationIssue",
    "ValidationReport",
    "require_columns",
    "validate_site_model",
    # SiteModel JSON loader
    "load_site_model_json",
    "load_site_model_json_with_report",
    # half-hourly
    "load_half_hourly_csv",
    "validate_half_hourly_coverage",
    "summarise_half_hourly_data",
    "to_load_data_summary",
    "HalfHourlyCoverage",
    "HalfHourlySummary",
    # data tier
    "infer_data_tier_from_hh_summary",
    "classify_site_data_tier",
    "HALF_HOURLY_PERIODS_PER_DAY",
    "T2_MIN_PERIODS",
    "T2_PERIOD_TOLERANCE",
    "T2_MIN_COMPLETENESS_PCT",
    "T1_MIN_PERIODS",
    # market forecasts
    "load_market_forecast_csv",
    "load_market_forecast_json",
    "validate_market_forecast",
    "hash_market_forecast",
    "summarise_market_forecast",
    "MarketForecast",
    "MarketForecastPoint",
    "MarketForecastSummary",
    # missing-data asks
    "detect_missing_data",
    "MissingDataItem",
    "build_ranked_data_asks",
    "rank_missing_data",
    "data_asks_to_missing_list",
    "format_data_asks",
    "DataAsk",
    # assembly (graded, ask-annotated SiteModel)
    "assemble_site_model",
    "populate_missing_data",
    # confidential-data access log (M0 security floor)
    "record_hh_access",
    "get_access_log",
    "clear_access_log",
    "HhAccessEvent",
    "ACCESS_LOG_ENV",
]
