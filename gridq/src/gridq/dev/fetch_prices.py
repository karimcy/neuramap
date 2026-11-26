"""Fetch historical GB half-hourly wholesale prices from the Elexon Insights API.

**Developer/CLI tool — OFF the pricing path.** This module is run *by hand* to produce an
input CSV; it is **never** imported by ``gridq run``, the solver, finance, quotes, or any
pricing code (confirm with ``grep -R fetch_prices src/gridq/{optimisation,finance,quotes}``).
It is a sibling of :mod:`gridq.dev.team_sync` / :mod:`gridq.dev.review_gates` — coordination
and tooling, not product logic.

**Wholesale is GATED UPSIDE — never the priced floor.** The CSV this tool writes is a
provenance-pinned *gated input* only (like a Modo forecast file). Wholesale/P415 can never be
marked ``FIRM`` and never enters the priced floor / P50–P90 cashflows / DSCR (see
``GATED_UPSIDE_STREAMS`` and CLAUDE.md scope). This tool prices nothing and invents nothing:
if the API returns gaps, they are written as empty prices and reported — never filled.

Output CSV
----------
Columns ``timestamp, price_gbp_per_mwh, source, forecast_version, created_at`` — the exact
alias set :func:`gridq.ingestion.market_forecasts.load_market_forecast_csv` reads. The file
therefore round-trips through the existing loader with no changes to it.

Endpoint (CONFIRM against the live API before relying on it)
------------------------------------------------------------
Base ``https://data.elexon.co.uk/bmrs/api/v1``, dataset **MID** (Market Index Data — the
reference wholesale price per settlement period, in £/MWh). The URL constructed per chunk is::

    {base}/datasets/{dataset}?from={date}T00:00Z&to={date}T23:59Z&format=json

e.g. for one week of MID::

    https://data.elexon.co.uk/bmrs/api/v1/datasets/MID
        ?from=2025-04-01T00:00Z&to=2025-04-07T23:59Z&format=json

The base URL, dataset, chunk size and data-provider filter are all overridable on the CLI, so
if the precise MID path/params differ in your account they can be corrected without a code
change. The CLI prints the exact first-chunk URL it used so the endpoint can be confirmed.
MID carries two data providers per settlement period (``APXMIDP`` = APX/EPEX, ``N2EXMIDP`` =
Nord Pool N2EX); we keep one reference series by filtering to ``--data-provider APXMIDP`` by
default (pass ``--data-provider all`` to keep every provider).

Settlement-period → timestamp
-----------------------------
GB settlement periods number 1..48 on a standard day (SP1 → 00:00, SP2 → 00:30, ... SP48 →
23:30), 46 on the spring clock-forward day and 50 on the autumn clock-back day. The mapping
here is the naive local period-start ``midnight + (period-1)·30min``. This is exact for the
standard 48-period day; **on the two annual DST-transition days the naive wall-clock hour can
be off by an hour after the boundary** — a documented limitation, not a crash: out-of-range
periods (>50 or <1) are skipped as malformed and reported, never fabricated.

Determinism
-----------
The network fetch is isolated in :func:`fetch` (one thin HTTP GET, the monkeypatch seam for
tests). The transform (:func:`records_to_rows`) and serialisation (:func:`rows_to_csv_text`)
are pure: identical records + identical ``created_at`` ⇒ identical CSV bytes. ``created_at`` is
always passed in (never read from the clock inside a pure function).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

# --- Endpoint / defaults (all overridable on the CLI) -----------------------

INSIGHTS_BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"
DEFAULT_DATASET = "MID"
DEFAULT_DATA_PROVIDER = "APXMIDP"
DEFAULT_CHUNK_DAYS = 7
_HTTP_TIMEOUT_S = 60.0
_USER_AGENT = "gridq-fetch-prices/1.0 (off-pricing-path dev tool; https://neura.energy)"

# Longest GB day (clocks back) has 50 settlement periods; shortest (clocks forward) has 46.
_MAX_SETTLEMENT_PERIOD = 50

# Provider-filter sentinels meaning "keep every data provider".
_KEEP_ALL_PROVIDERS = frozenset({"", "all", "any", "*"})

# Elexon Insights field names, with tolerant aliases (the API uses camelCase).
_DATE_KEYS = ("settlementDate", "settlement_date", "SettlementDate")
_PERIOD_KEYS = ("settlementPeriod", "settlement_period", "SettlementPeriod")
_PRICE_KEYS = ("price", "Price", "price_gbp_per_mwh", "midPrice")
_PROVIDER_KEYS = ("dataProvider", "data_provider", "DataProvider")

# JSON keys that may hold the array of records in an Insights response.
_ARRAY_KEYS = ("data", "results", "items")

# The output CSV columns, in fixed order — the loader's recognised alias set.
_CSV_FIELDS = ("timestamp", "price_gbp_per_mwh", "source", "forecast_version", "created_at")


class FetchError(RuntimeError):
    """A network / decode / API-shape failure. Raised instead of ever fabricating prices."""


@dataclass(frozen=True)
class FetchResult:
    """Outcome of a fetch+write run (all counts are honest, nothing is filled)."""

    out_path: Path
    record_count: int  # records returned by the API (post provider-filter)
    rows_written: int  # rows with a derivable timestamp actually written
    gap_count: int  # rows written with a missing/unparseable price (never filled)
    skipped_count: int  # records dropped as malformed (no date/period/valid SP)
    source: str
    forecast_version: str
    sample_url: str


# --- Small helpers -----------------------------------------------------------


def _first_present(record: Mapping[str, Any], keys: Sequence[str]) -> Any:
    """Return the first non-null value among ``keys`` in ``record``, else ``None``."""
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return None


def _to_float_or_none(value: Any) -> float | None:
    """Parse a price to float; return ``None`` for missing/empty/unparseable (a gap).

    Never raises and never fabricates — an unparseable cell becomes a reported gap, not a
    guessed number.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # guard: bool is an int subclass
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "null", "none"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def settlement_period_to_timestamp(settlement_date: date, settlement_period: int) -> datetime:
    """Map a GB settlement period to its naive local half-hour period-start datetime.

    SP1 → 00:00, SP2 → 00:30, SP3 → 01:00, ... SP48 → 23:30. Exact on a standard 48-period
    day; approximate across the two annual DST-transition days (see module docstring). Raises
    ``ValueError`` for a period outside 1..50 so a malformed record is skipped, not misplaced.
    """
    if not 1 <= settlement_period <= _MAX_SETTLEMENT_PERIOD:
        raise ValueError(
            f"settlement period {settlement_period} out of range 1..{_MAX_SETTLEMENT_PERIOD}"
        )
    midnight = datetime.combine(settlement_date, time(0, 0))
    return midnight + timedelta(minutes=30 * (settlement_period - 1))


def default_source_label(dataset: str) -> str:
    """The ``source`` cell recorded on every row, e.g. ``elexon_insights_mid``."""
    return f"elexon_insights_{dataset.lower()}"


def build_forecast_version(dataset: str, start: date, end: date) -> str:
    """A version label carrying the dataset and the fetched date range, e.g.
    ``elexon_MID_2025-04-01_2025-12-08``."""
    return f"elexon_{dataset}_{start.isoformat()}_{end.isoformat()}"


def iter_date_chunks(start: date, end: date, chunk_days: int) -> Iterator[tuple[date, date]]:
    """Yield inclusive ``(chunk_start, chunk_end)`` windows covering ``start..end``.

    Chunking keeps each request small (the Insights API caps a single request's range); the
    windows are non-overlapping by date and :func:`fetch_records` de-duplicates edges anyway.
    """
    if chunk_days < 1:
        raise ValueError("chunk_days must be >= 1")
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def build_dataset_url(
    chunk_start: date,
    chunk_end: date,
    *,
    dataset: str = DEFAULT_DATASET,
    base_url: str = INSIGHTS_BASE_URL,
) -> str:
    """Construct the Insights dataset URL for one inclusive date window."""
    params = {
        "from": f"{chunk_start.isoformat()}T00:00Z",
        "to": f"{chunk_end.isoformat()}T23:59Z",
        "format": "json",
    }
    return f"{base_url.rstrip('/')}/datasets/{dataset}?{urlencode(params)}"


# --- Network seam (the ONLY place that touches the wire) ---------------------


def fetch(url: str, *, timeout: float = _HTTP_TIMEOUT_S) -> Any:
    """Perform one HTTP GET and return the parsed JSON payload.

    This is the single network seam — tests monkeypatch this to return a captured payload, so
    the suite never touches the network. Raises :class:`FetchError` (never fabricates) on any
    connection, HTTP, timeout or decode failure.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(
            f"Elexon Insights request failed for {url}: {exc}. "
            "No prices were written — this tool never fabricates prices."
        ) from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FetchError(f"Elexon Insights returned non-JSON for {url}: {exc}") from exc


def extract_records(payload: Any) -> list[Mapping[str, Any]]:
    """Pull the list of records out of an Insights response payload.

    Accepts ``{"data": [...]}`` (the MID shape) or a bare top-level list. Raises
    :class:`FetchError` on an unrecognised shape so an API contract change surfaces loudly.
    """
    if isinstance(payload, Mapping):
        for key in _ARRAY_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, Mapping)]
        raise FetchError(
            f"Insights payload object has no record array under any of {_ARRAY_KEYS}; "
            f"got keys {sorted(payload.keys())}."
        )
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, Mapping)]
    raise FetchError(
        f"Insights payload is neither an object nor a list (got {type(payload).__name__})."
    )


def _dedup_key(record: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """A stable identity for a record (date, period, provider), or ``None`` if unkeyable."""
    day = _first_present(record, _DATE_KEYS)
    period = _first_present(record, _PERIOD_KEYS)
    if day is None or period is None:
        return None
    provider = _first_present(record, _PROVIDER_KEYS)
    return (str(day), str(period), str(provider))


def fetch_records(
    start: date,
    end: date,
    *,
    dataset: str = DEFAULT_DATASET,
    base_url: str = INSIGHTS_BASE_URL,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    timeout: float = _HTTP_TIMEOUT_S,
    http_get: Callable[..., Any] | None = None,
) -> list[Mapping[str, Any]]:
    """Fetch all records across ``start..end`` in chunks, de-duplicating overlapping edges.

    ``http_get`` defaults to :func:`fetch`; pass a stub in tests (or monkeypatch the module's
    ``fetch``). De-duplication is on ``(date, period, provider)`` — it only removes exact
    repeats from overlapping windows, never distinct data.
    """
    getter = http_get if http_get is not None else fetch
    seen: set[tuple[str, str, str]] = set()
    out: list[Mapping[str, Any]] = []
    for chunk_start, chunk_end in iter_date_chunks(start, end, chunk_days):
        url = build_dataset_url(chunk_start, chunk_end, dataset=dataset, base_url=base_url)
        payload = getter(url, timeout=timeout)
        for record in extract_records(payload):
            key = _dedup_key(record)
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            out.append(record)
    return out


def filter_by_provider(
    records: Sequence[Mapping[str, Any]], data_provider: str | None
) -> list[Mapping[str, Any]]:
    """Keep only records for ``data_provider`` (case-insensitive).

    ``None`` / ``""`` / ``all`` / ``any`` / ``*`` keep every provider. Records with no provider
    field are always kept (we never silently drop data whose provider we can't read).
    """
    if data_provider is None or data_provider.strip().lower() in _KEEP_ALL_PROVIDERS:
        return list(records)
    wanted = data_provider.strip().lower()
    kept: list[Mapping[str, Any]] = []
    for record in records:
        provider = _first_present(record, _PROVIDER_KEYS)
        if provider is None or str(provider).strip().lower() == wanted:
            kept.append(record)
    return kept


# --- Pure transform + serialisation -----------------------------------------


def records_to_rows(
    records: Sequence[Mapping[str, Any]],
    *,
    created_at: datetime,
    source: str,
    forecast_version: str,
) -> list[dict[str, Any]]:
    """Map Elexon records → normalised, loader-ready rows (pure; no clock, no invention).

    Each row is ``{timestamp, price_gbp_per_mwh, source, forecast_version, created_at}``.
    Records without a usable settlement date/period (or an out-of-range period) are skipped —
    they cannot be placed on a timestamp and are never fabricated. A present-but-unparseable
    price becomes ``None`` (a reported gap), never a guessed value. Rows are returned in
    ascending timestamp order (stable for ties) so the output is deterministic.
    """
    created_iso = created_at.isoformat()
    placed: list[tuple[datetime, float | None]] = []
    for record in records:
        day_raw = _first_present(record, _DATE_KEYS)
        period_raw = _first_present(record, _PERIOD_KEYS)
        if day_raw is None or period_raw is None:
            continue
        try:
            day = date.fromisoformat(str(day_raw)[:10])
            period = int(period_raw)
            timestamp = settlement_period_to_timestamp(day, period)
        except (ValueError, TypeError):
            continue  # malformed record — skipped, never guessed
        price = _to_float_or_none(_first_present(record, _PRICE_KEYS))
        placed.append((timestamp, price))

    placed.sort(key=lambda item: item[0])  # stable: preserves input order on equal timestamps
    return [
        {
            "timestamp": timestamp.isoformat(),
            "price_gbp_per_mwh": price,
            "source": source,
            "forecast_version": forecast_version,
            "created_at": created_iso,
        }
        for timestamp, price in placed
    ]


def count_gaps(rows: Sequence[Mapping[str, Any]]) -> int:
    """Number of rows with no price (missing/unparseable). These are reported, never filled."""
    return sum(1 for row in rows if row.get("price_gbp_per_mwh") is None)


def rows_to_csv_text(rows: Sequence[Mapping[str, Any]]) -> str:
    """Serialise rows to deterministic CSV text (fixed columns, ``\\n`` line endings).

    ``None`` prices are written as an empty field — the loader reads that back as a missing
    price. Identical rows always produce byte-identical output.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(_CSV_FIELDS), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {field: ("" if row.get(field) is None else row[field]) for field in _CSV_FIELDS}
        )
    return buffer.getvalue()


# --- Orchestration -----------------------------------------------------------


def run(
    *,
    start: date,
    end: date,
    out_path: str | Path,
    dataset: str = DEFAULT_DATASET,
    data_provider: str | None = DEFAULT_DATA_PROVIDER,
    base_url: str = INSIGHTS_BASE_URL,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    timeout: float = _HTTP_TIMEOUT_S,
    created_at: datetime | None = None,
    http_get: Callable[..., Any] | None = None,
) -> FetchResult:
    """Fetch → transform → write, returning a :class:`FetchResult` of honest counts.

    ``created_at`` is stamped on every row; when ``None`` the wall clock is read here (this is
    the orchestration boundary, not a pure function). Raises :class:`FetchError` on any network
    failure — no CSV is written and no price is ever fabricated.
    """
    stamp = created_at if created_at is not None else datetime.now().replace(microsecond=0)
    raw_records = fetch_records(
        start,
        end,
        dataset=dataset,
        base_url=base_url,
        chunk_days=chunk_days,
        timeout=timeout,
        http_get=http_get,
    )
    records = filter_by_provider(raw_records, data_provider)
    source = default_source_label(dataset)
    forecast_version = build_forecast_version(dataset, start, end)
    rows = records_to_rows(
        records, created_at=stamp, source=source, forecast_version=forecast_version
    )

    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rows_to_csv_text(rows), encoding="utf-8")

    first_chunk = next(iter_date_chunks(start, end, chunk_days))
    return FetchResult(
        out_path=destination,
        record_count=len(records),
        rows_written=len(rows),
        gap_count=count_gaps(rows),
        skipped_count=len(records) - len(rows),
        source=source,
        forecast_version=forecast_version,
        sample_url=build_dataset_url(*first_chunk, dataset=dataset, base_url=base_url),
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m gridq.dev.fetch_prices",
        description=(
            "Fetch historical GB half-hourly wholesale prices from the Elexon Insights API "
            "(free, no API key) into a versioned CSV the market-forecast loader can read. "
            "OFF the pricing path; wholesale is gated-upside provenance input, never the floor."
        ),
    )
    parser.add_argument("--start", required=True, help="inclusive start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="inclusive end date, YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="output CSV path (parents created)")
    parser.add_argument(
        "--dataset", default=DEFAULT_DATASET, help=f"Insights dataset (default {DEFAULT_DATASET})"
    )
    parser.add_argument(
        "--data-provider",
        default=DEFAULT_DATA_PROVIDER,
        help=(
            f"keep only this MID data provider (default {DEFAULT_DATA_PROVIDER}); "
            "pass 'all' to keep every provider"
        ),
    )
    parser.add_argument(
        "--base-url", default=INSIGHTS_BASE_URL, help=f"API base (default {INSIGHTS_BASE_URL})"
    )
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=DEFAULT_CHUNK_DAYS,
        help=f"days per request (default {DEFAULT_CHUNK_DAYS})",
    )
    parser.add_argument(
        "--timeout", type=float, default=_HTTP_TIMEOUT_S, help="per-request timeout (seconds)"
    )
    parser.add_argument(
        "--created-at",
        default=None,
        help="ISO datetime stamped on every row (default: now; set for reproducible output)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for ``python -m gridq.dev.fetch_prices``.

    Prints the row count, gap/skip counts, output path and the exact endpoint used, then
    returns 0. On a network/API failure it prints a clear message and returns 1 — it never
    writes a fabricated CSV.
    """
    args = _build_arg_parser().parse_args(argv)
    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError as exc:
        print(f"fetch_prices: bad date ({exc}); use YYYY-MM-DD.", file=sys.stderr)
        return 1

    created_at = None
    if args.created_at is not None:
        try:
            created_at = datetime.fromisoformat(args.created_at)
        except ValueError as exc:
            print(f"fetch_prices: bad --created-at ({exc}).", file=sys.stderr)
            return 1

    try:
        result = run(
            start=start,
            end=end,
            out_path=args.out,
            dataset=args.dataset,
            data_provider=args.data_provider,
            base_url=args.base_url,
            chunk_days=args.chunk_days,
            timeout=args.timeout,
            created_at=created_at,
        )
    except FetchError as exc:
        print(f"fetch_prices: {exc}", file=sys.stderr)
        return 1

    print(
        f"Fetched {result.record_count} record(s); wrote {result.rows_written} row(s) "
        f"({result.gap_count} without a price, {result.skipped_count} malformed skipped) "
        f"to {result.out_path}"
    )
    print(f"  dataset={args.dataset}  source={result.source}  version={result.forecast_version}")
    print(f"  endpoint (confirm): {result.sample_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
