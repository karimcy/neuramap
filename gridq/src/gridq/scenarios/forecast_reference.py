"""ForecastReference — a hash-pinned pointer to an EXTERNAL price/revenue forecast.

External forecasts (e.g. Modo Energy wholesale curves) are *input data*, never numbers a
Claude session may invent. A forecast enters the system only as a reference to a real
file — identified by its source, version and a content hash — so a quote can always be
traced back to the exact forecast it used, and silent drift is detectable.

This module holds the model plus helpers that build a reference FROM a real file (loading
and hashing it, never accepting an invented hash), convert a loaded
``ingestion.MarketForecast`` into a reference, and verify a reference still matches the
file on disk. The forecast *values* are consumed elsewhere; here we only pin the reference.

**Hash vocabulary (aligned with ``gridq.ingestion``).** A forecast is pinned by the same
two hashes ingestion records:

- ``content_hash`` — the *canonical content* hash of the (timestamp, price, scenario)
  curve, format-independent (a CSV and a JSON export of the same curve hash identically).
  This is the provenance identity and is **required**. It deliberately reuses the run-level
  vocabulary's *kind* of hash (``sha256:``) but is a distinct concept from the run-level
  ``input_hash`` on ``RunContext``/``QuoteRecord`` — a forecast-content hash, not a run pin.
- ``file_sha256`` — the literal source-file bytes hash, **optional**. Present when the
  reference was built from a real file; absent for an in-memory forecast.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field, model_validator

from gridq.schemas.provenance import GridQModel

if TYPE_CHECKING:
    from gridq.ingestion.market_forecasts import MarketForecast

_REQUIRED_NON_EMPTY = ("source", "file_ref", "forecast_version", "content_hash", "price_unit")
# A valid hash is exactly what hash_json()/hash_file() emit: 'sha256:' + 64 lowercase hex chars.
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# Default price unit for forecasts ingested via gridq.ingestion (prices are price_gbp_per_mwh).
_DEFAULT_PRICE_UNIT = "GBP/MWh"


class ForecastReference(GridQModel):
    """A pinned reference to an external forecast file (not the forecast values themselves).

    The (``source`` + ``forecast_version`` + ``content_hash``) triple is what makes a
    forecast traceable rather than invented: it names where the numbers came from and
    fixes the exact forecast used, so any output built on them can be reproduced and audited.

    ``content_hash`` is the canonical content hash of the forecast curve (matching
    ingestion's ``content_hash``); ``file_sha256`` optionally pins the literal source-file
    bytes (matching ingestion's ``file_sha256``). Both must be well-formed ``sha256:<64 hex>``
    digests when present (anything else is rejected, not merely warned).

    Well-formedness still cannot prove a hash traces to real data, so ``verified`` is the
    machine-readable provenance signal: it is set True only when a reference is built from a
    real, loaded forecast *file* — ``build_forecast_reference``, or
    ``forecast_reference_from_market_forecast`` on a file-loaded forecast that carries a
    ``file_sha256`` — or after a successful ``verify_forecast_reference`` re-checks the file.
    A directly-constructed or in-memory reference is ``verified=False``, and downstream
    consumers (and the scenario engine) treat unverified references as not-bankable-backing.
    """

    source: str = Field(description="Provider/origin of the forecast, e.g. 'Modo Energy'.")
    file_ref: str = Field(description="Path or stable locator of the referenced forecast file.")
    forecast_version: str = Field(description="The provider's forecast version / vintage label.")
    content_hash: str = Field(
        description="Canonical content hash of the forecast curve ('sha256:<64 hex>')."
    )
    file_sha256: str | None = Field(
        default=None,
        description="Literal source-file bytes hash ('sha256:<64 hex>'), when built from a file.",
    )
    created_at: datetime = Field(description="When the forecast itself was produced (metadata).")
    price_unit: str = Field(description="Unit of the forecast prices, e.g. 'GBP/MWh'.")
    coverage_start: date = Field(description="First date the forecast covers.")
    coverage_end: date = Field(description="Last date the forecast covers.")
    verified: bool = Field(
        default=False,
        description="True when built/re-checked from a real forecast file (carries a file_sha256).",
    )
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _checks(self) -> ForecastReference:
        missing = [f for f in _REQUIRED_NON_EMPTY if not str(getattr(self, f)).strip()]
        if missing:
            raise ValueError(
                f"ForecastReference requires non-empty {', '.join(missing)} — a referenced "
                f"forecast must be identified by source + version + content hash, never invented."
            )
        if not _SHA256_RE.match(self.content_hash):
            raise ValueError(
                f"content_hash {self.content_hash!r} is not a well-formed 'sha256:<64 hex>' "
                f"digest — derive it from a real forecast (use build_forecast_reference or "
                f"forecast_reference_from_market_forecast)."
            )
        if self.file_sha256 is not None and not _SHA256_RE.match(self.file_sha256):
            raise ValueError(
                f"file_sha256 {self.file_sha256!r} is not a well-formed 'sha256:<64 hex>' digest."
            )
        if self.coverage_end < self.coverage_start:
            raise ValueError("coverage_end must not precede coverage_start.")
        return self


def _load_forecast_file(path: str | Path, **kwargs: object) -> MarketForecast:
    """Load a forecast file into a `MarketForecast`, picking CSV/JSON by suffix.

    Local import keeps the (pandas-backed) ingestion loaders off the scenarios import path.
    """
    from gridq.ingestion.market_forecasts import (
        load_market_forecast_csv,
        load_market_forecast_json,
    )

    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return load_market_forecast_json(path, **kwargs)  # type: ignore[arg-type]
    if suffix == ".csv":
        return load_market_forecast_csv(path, **kwargs)  # type: ignore[arg-type]
    raise ValueError(
        f"cannot infer forecast format from {str(path)!r}; supported suffixes: .csv, .json"
    )


def forecast_reference_from_market_forecast(
    forecast: MarketForecast,
    *,
    price_unit: str = _DEFAULT_PRICE_UNIT,
    source: str | None = None,
    forecast_version: str | None = None,
    file_ref: str | None = None,
    created_at: datetime | None = None,
    coverage_start: date | None = None,
    coverage_end: date | None = None,
) -> ForecastReference:
    """Convert a loaded ``ingestion.MarketForecast`` into a ``ForecastReference``.

    This is the single seam wiring ingestion to the scenario engine: a forecast loaded by
    ``load_market_forecast_csv`` / ``load_market_forecast_json`` can directly back a scenario.
    ``content_hash`` is **recomputed from the forecast's points** (never a pre-set value
    trusted verbatim), so the reference's provenance identity always matches the curve it
    pins; ``file_sha256`` is carried across as-is. The same forecast always pins identically.

    Provenance metadata is taken from the forecast (overridable per keyword) and is **never
    invented**: ``source`` / ``forecast_version`` / ``file_ref`` / ``created_at`` must be known
    (raises otherwise), and the coverage window is derived from the forecast's point
    timestamps when not supplied. ``verified`` is True iff the forecast carries a
    ``file_sha256`` (i.e. it was loaded from a real file) **and** pins at least one point; an
    in-memory or empty forecast yields an unverified reference with an explanatory warning.
    """
    from gridq.ingestion.market_forecasts import hash_market_forecast

    src = (source or forecast.source or "").strip()
    if not src:
        raise ValueError(
            "cannot build a ForecastReference: the forecast records no `source` and none was "
            "supplied — a forecast must name where its numbers came from (never invented)."
        )
    ver = (forecast_version or forecast.forecast_version or "").strip()
    if not ver:
        raise ValueError(
            "cannot build a ForecastReference: the forecast records no `forecast_version` and "
            "none was supplied — needed to pin which forecast vintage was used."
        )
    fref = (file_ref or forecast.file_ref or "").strip()
    if not fref:
        raise ValueError(
            "cannot build a ForecastReference: the forecast records no `file_ref` and none was "
            "supplied — the reference must locate the real forecast file."
        )
    created = created_at or forecast.created_at
    if created is None:
        raise ValueError(
            "cannot build a ForecastReference: the forecast records no `created_at` and none "
            "was supplied — the production date is metadata that is never invented."
        )

    cov_start, cov_end = coverage_start, coverage_end
    if cov_start is None or cov_end is None:
        timestamps = [p.timestamp for p in forecast.points]
        if not timestamps:
            raise ValueError(
                "cannot derive a coverage window: the forecast has no points and no explicit "
                "coverage_start/coverage_end was supplied."
            )
        cov_start = cov_start or min(timestamps).date()
        cov_end = cov_end or max(timestamps).date()

    # Recompute the canonical content hash from the points so the pin always reflects the
    # actual curve — a pre-set (possibly stale or fabricated) forecast.content_hash is never
    # trusted verbatim as the reference's provenance identity.
    content_hash = hash_market_forecast(forecast)
    file_sha256 = forecast.file_sha256

    # Carry the forecast's own integrity flags (interpolation, missing prices, ...) onto the
    # reference so a quote that leans on it inherits the provenance caveats.
    warnings_out = [i.message for i in forecast.report.errors]
    warnings_out += [i.message for i in forecast.report.warnings]

    # `verified` is a provenance-origin signal: True iff the forecast was loaded from a real
    # file (carries a file_sha256). The converter holds no file to re-hash, so it cannot make
    # a stronger byte-level guarantee than that. An empty curve is never verified.
    verified = file_sha256 is not None
    if not forecast.points:
        verified = False
        warnings_out.append(
            "forecast pins no price points (empty curve); its content_hash is the hash of an "
            "empty list and backs no data — verified=False."
        )
    elif file_sha256 is None:
        warnings_out.append(
            "forecast has no file_sha256 (not loaded from a real file via the ingestion "
            "loaders); its content hash cannot be confirmed against real bytes — verified=False."
        )

    return ForecastReference(
        source=src,
        file_ref=fref,
        forecast_version=ver,
        content_hash=content_hash,
        file_sha256=file_sha256,
        created_at=created,
        price_unit=price_unit,
        coverage_start=cov_start,
        coverage_end=cov_end,
        verified=verified,
        warnings=warnings_out,
    )


def build_forecast_reference(
    *,
    file_path: str | Path,
    source: str,
    forecast_version: str,
    price_unit: str = _DEFAULT_PRICE_UNIT,
    coverage_start: date,
    coverage_end: date,
    created_at: datetime,
    file_ref: str | None = None,
) -> ForecastReference:
    """Build a `ForecastReference` by LOADING and hashing a real forecast file.

    The file is parsed via the ingestion loaders (CSV/JSON by suffix), so ``content_hash`` is
    the canonical forecast-content hash and ``file_sha256`` the literal bytes — never supplied
    or guessed. Raises ``FileNotFoundError`` if the file is absent, so a reference can only be
    created against data that actually exists. The forecast is loaded from real bytes, so the
    result is ``verified=True``.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"forecast file not found: {path}")
    forecast = _load_forecast_file(
        path, source=source, forecast_version=forecast_version, created_at=created_at
    )
    return forecast_reference_from_market_forecast(
        forecast,
        price_unit=price_unit,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        file_ref=file_ref if file_ref is not None else str(path),
    )


def verify_forecast_reference(ref: ForecastReference, file_path: str | Path) -> list[str]:
    """Return warnings if the forecast on disk no longer matches the recorded content hash.

    Empty list means the reference still matches (no drift) — and ``ref.verified`` is set
    True in place. A non-empty list means missing/unreadable/changed data; ``ref.verified`` is
    left False. This catches a forecast file being swapped under a quote without re-pinning.
    Verification is content-level (re-loads and re-hashes the curve), so a benign re-export of
    the identical curve does not read as drift.
    """
    from gridq.ingestion.market_forecasts import MarketForecastError, hash_market_forecast

    path = Path(file_path)
    if not path.is_file():
        ref.verified = False
        return [
            f"forecast file {path} is missing; cannot verify reference "
            f"{ref.forecast_version!r} from {ref.source!r}."
        ]
    try:
        forecast = _load_forecast_file(path)
    except (MarketForecastError, ValueError) as exc:
        ref.verified = False
        return [
            f"forecast file {path} could not be re-read as a forecast ({exc}); cannot confirm "
            f"reference {ref.forecast_version!r} from {ref.source!r} — re-pin and reprice."
        ]
    current = hash_market_forecast(forecast)
    if current != ref.content_hash:
        ref.verified = False
        return [
            f"forecast file {path} content hash {current} != recorded {ref.content_hash} — the "
            f"referenced forecast changed; re-pin the reference and reprice."
        ]
    ref.verified = True
    return []


def require_forecast_reference(ref: ForecastReference | None) -> ForecastReference:
    """Assert a forecast reference is present.

    Use when a scenario claims forecast-backed prices: the prices must trace to a real,
    hash-pinned file, not to an invented spread.
    """
    if ref is None:
        raise ValueError(
            "a forecast-backed scenario requires a ForecastReference (source + version + hash)."
        )
    return ref
