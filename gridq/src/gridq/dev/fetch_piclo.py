"""Fetch GB local-flexibility (Piclo Flex) datasets from the public Piclo Data Hub S3 bucket.

**Developer/CLI tool — OFF the pricing path.** Run *by hand* to pull a provenance-pinned public
figure into a versioned assumption release; **never** imported by ``gridq run``, the solver,
finance, quotes, or any pricing code. Sibling of :mod:`gridq.dev.fetch_prices`.

**Never fabricates.** Each dataset is fetched raw (keyless S3, no auth), hashed + sized + counted,
written to a gitignored artifact under ``data/fetched/piclo/`` with a committed provenance
sidecar; for ``competitions`` a *suggested* YAML block (median utilisation £/MWh) is printed to
stdout — this tool **only prints** the suggestion, it never edits ``assumptions/``. Local flex is
a CONDITIONAL stream, only priced on a confirmed tender window and net of the aggregator cut —
whatever the fetched guide price. If the fetch fails it raises and writes nothing.

Datasets (Piclo Data Hub, public S3, no auth)
---------------------------------------------
- ``competitions`` — ``piclo_flex_uk_competitions.xlsx``: DSO flexibility competitions with the
  ``Utilisation Guide Price (£/MWh)`` and ``Availability Guide Price (£/MW/h)`` columns. Parsed
  with pandas/openpyxl; we report the count + GB-wide medians and suggest the utilisation price.
- ``bids`` — ``bidding-data.xlsx``: accepted bids. Download + hash + row-count only.
- ``lcm`` — ``lcm_results_report.csv``: NESO Local Constraint Market results. Download + hash +
  row-count only.

Determinism
-----------
The network fetch is isolated in :func:`gridq.dev._fetch_common.http_get_bytes` (monkeypatched in
tests). The parse/summary transforms are pure over the fetched bytes; ``fetched_utc`` is always
passed into the sidecar so it is byte-reproducible.
"""

from __future__ import annotations

import argparse
import io
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from gridq.dev._fetch_common import (
    FetchError,
    build_sidecar,
    http_get_bytes,
    sha256_hex,
    write_json,
    write_sidecar,
)

PICLO_BASE_URL = "https://piclo-open-data.s3.eu-west-1.amazonaws.com/public-data"
LICENCE = "Piclo Data Hub open data (data.piclo.com terms)"

_UTIL_COL = "Utilisation Guide Price (£/MWh)"
_AVAIL_COL = "Availability Guide Price (£/MW/h)"


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    filename: str
    kind: str  # "xlsx_competitions" | "xlsx_rows" | "csv_rows"


DATASETS: dict[str, DatasetSpec] = {
    "competitions": DatasetSpec(
        "competitions", "piclo_flex_uk_competitions.xlsx", "xlsx_competitions"
    ),
    "bids": DatasetSpec("bids", "bidding-data.xlsx", "xlsx_rows"),
    "lcm": DatasetSpec("lcm", "lcm_results_report.csv", "csv_rows"),
}


def dataset_url(spec: DatasetSpec, *, base_url: str = PICLO_BASE_URL) -> str:
    """The public S3 URL for one dataset."""
    return f"{base_url.rstrip('/')}/{spec.filename}"


# --- Pure summary transforms (over the fetched raw bytes) --------------------


def _median_or_none(series: pd.Series) -> float | None:
    """Median of the numeric-coerced, non-null values; ``None`` when the column is empty."""
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return None
    return round(float(numeric.median()), 4)


def _count_or_zero(series: pd.Series) -> int:
    return int(pd.to_numeric(series, errors="coerce").dropna().shape[0])


def summarise_competitions(raw: bytes) -> dict[str, Any]:
    """Row count + GB-wide utilisation/availability guide-price medians from the XLSX bytes.

    Missing / all-null price columns yield ``None`` medians (a reported gap, never a guessed
    number). Raises :class:`FetchError` if the workbook cannot be parsed or the price column is
    absent (a schema change surfaces loudly).
    """
    try:
        frame = pd.read_excel(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001 - any parse failure is a fetch failure, never guessed
        raise FetchError(f"could not parse Piclo competitions XLSX: {exc}") from exc
    if _UTIL_COL not in frame.columns:
        raise FetchError(
            f"Piclo competitions XLSX missing {_UTIL_COL!r}; columns={list(frame.columns)}"
        )
    util_median = _median_or_none(frame[_UTIL_COL])
    avail_median = _median_or_none(frame[_AVAIL_COL]) if _AVAIL_COL in frame.columns else None
    return {
        "row_count": int(frame.shape[0]),
        "utilisation_price_rows": _count_or_zero(frame[_UTIL_COL]),
        "utilisation_median_gbp_per_mwh": util_median,
        "availability_price_rows": (
            _count_or_zero(frame[_AVAIL_COL]) if _AVAIL_COL in frame.columns else 0
        ),
        "availability_median_gbp_per_mw_h": avail_median,
    }


def count_xlsx_rows(raw: bytes) -> int:
    """Row count of the first sheet of an XLSX (download+hash+count only)."""
    try:
        return int(pd.read_excel(io.BytesIO(raw)).shape[0])
    except Exception as exc:  # noqa: BLE001
        raise FetchError(f"could not parse Piclo XLSX for row count: {exc}") from exc


def count_csv_rows(raw: bytes) -> int:
    """Data-row count of a CSV (excludes the header); never fabricates on a decode issue."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FetchError(f"could not decode Piclo CSV: {exc}") from exc
    lines = [ln for ln in text.splitlines() if ln.strip() != ""]
    return max(len(lines) - 1, 0)


# --- Suggested YAML (print-only) ---------------------------------------------


def suggested_yaml(
    summary: dict[str, Any], *, source_url: str, sha12: str, fetched_date: str, sidecar_path: str
) -> str | None:
    """Suggested YAML for ``local_flex_piclo.utilisation_price_gbp_per_mwh`` (competitions only).

    ``None`` when no utilisation median could be derived (never emits a guessed number).
    """
    median = summary.get("utilisation_median_gbp_per_mwh")
    if median is None:
        return None
    cite = (
        f"Piclo Data Hub {source_url} sha256:{sha12} fetched {fetched_date}; sidecar {sidecar_path}"
    )
    return (
        "local_flex_piclo:\n"
        "  utilisation_price_gbp_per_mwh:\n"
        f"    value: {median}\n"
        "    unit: GBP/MWh\n"
        f'    source: "{cite}"\n'
        "    status: needs_review\n"
        f"    # derivation: median of {summary['utilisation_price_rows']} GB-wide "
        f"'{_UTIL_COL}' values. CONDITIONAL; only priced on a confirmed tender "
        "window, net of the cut.\n"
    )


# --- Orchestration -----------------------------------------------------------


@dataclass(frozen=True)
class PicloFetchResult:
    dataset: str
    out_path: Path
    sidecar_path: Path
    record_count: int
    sha256: str
    summary: dict[str, Any]
    suggested_yaml: str | None
    source_url: str


def run(
    *,
    dataset: str,
    out_path: str | Path,
    base_url: str = PICLO_BASE_URL,
    fetched_utc: datetime | None = None,
    http_get: Any = None,
) -> PicloFetchResult:
    """Fetch → summarise → write artifact + sidecar; return the result (never fabricates)."""
    if dataset not in DATASETS:
        raise FetchError(f"unknown dataset {dataset!r}; choose one of {sorted(DATASETS)}.")
    spec = DATASETS[dataset]
    getter = http_get if http_get is not None else http_get_bytes
    stamp = (
        fetched_utc
        if fetched_utc is not None
        else datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    )

    url = dataset_url(spec, base_url=base_url)
    raw = getter(url)

    if spec.kind == "xlsx_competitions":
        summary = summarise_competitions(raw)
        record_count = summary["row_count"]
    elif spec.kind == "xlsx_rows":
        record_count = count_xlsx_rows(raw)
        summary = {"row_count": record_count}
    else:  # csv_rows
        record_count = count_csv_rows(raw)
        summary = {"row_count": record_count}

    out = Path(out_path)
    sidecar_path = out.with_name(out.stem + ".provenance.json")
    sha = sha256_hex(raw)
    fetched_date = stamp.date().isoformat()

    yaml_block = None
    if spec.kind == "xlsx_competitions":
        yaml_block = suggested_yaml(
            summary,
            source_url=url,
            sha12=sha[:12],
            fetched_date=fetched_date,
            sidecar_path=str(sidecar_path),
        )

    write_json(
        out,
        {
            "dataset": dataset,
            "filename": spec.filename,
            "source_url": url,
            "record_count": record_count,
            "summary": summary,
        },
    )
    sidecar = build_sidecar(
        raw=raw,
        source_url=url,
        dataset_id=spec.filename,
        fetched_utc=stamp,
        licence=LICENCE,
        fetcher="gridq.dev.fetch_piclo",
        fetcher_args={"dataset": dataset},
        record_count=record_count,
    )
    write_sidecar(sidecar_path, sidecar)

    return PicloFetchResult(
        dataset=dataset,
        out_path=out,
        sidecar_path=sidecar_path,
        record_count=record_count,
        sha256=sha,
        summary=summary,
        suggested_yaml=yaml_block,
        source_url=url,
    )


def _default_out(dataset: str, fetched: date) -> str:
    return f"data/fetched/piclo/{dataset}_{fetched.isoformat()}.json"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m gridq.dev.fetch_piclo",
        description=(
            "Fetch a Piclo Flex dataset from the public Piclo Data Hub S3 bucket into a "
            "provenance-pinned artifact + sidecar; for 'competitions' PRINT a suggested YAML "
            "block. OFF the pricing path; never edits assumptions/; never fabricates."
        ),
    )
    parser.add_argument("--dataset", required=True, choices=sorted(DATASETS))
    parser.add_argument(
        "--out", default=None, help="artifact path (default data/fetched/piclo/...)"
    )
    parser.add_argument(
        "--base-url", default=PICLO_BASE_URL, help=f"S3 base (default {PICLO_BASE_URL})"
    )
    parser.add_argument(
        "--fetched-utc", default=None, help="ISO datetime for the sidecar (default: now)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Prints a summary (+ suggested YAML for competitions); 0, or 1 on failure."""
    args = _build_arg_parser().parse_args(argv)
    fetched_utc = None
    if args.fetched_utc is not None:
        try:
            fetched_utc = datetime.fromisoformat(args.fetched_utc)
        except ValueError as exc:
            print(f"fetch_piclo: bad --fetched-utc ({exc}).", file=sys.stderr)
            return 1
    stamp = fetched_utc or datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    out_path = args.out or _default_out(args.dataset, stamp.date())

    try:
        result = run(
            dataset=args.dataset, out_path=out_path, base_url=args.base_url, fetched_utc=stamp
        )
    except FetchError as exc:
        print(f"fetch_piclo: {exc}", file=sys.stderr)
        return 1

    print(
        f"Fetched {result.record_count} row(s) for {result.dataset}; "
        f"sha256={result.sha256[:12]}... -> {result.out_path}"
    )
    print(f"  sidecar: {result.sidecar_path}")
    print(f"  summary: {result.summary}")
    if result.suggested_yaml is not None:
        print("  --- suggested YAML (print-only; transcribe by hand into a gb_v1_x release) ---")
        print(result.suggested_yaml)
    return 0


if __name__ == "__main__":
    sys.exit(main())
