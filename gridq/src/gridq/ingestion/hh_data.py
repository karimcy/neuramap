"""Half-hourly (HH) commercial site load data — first-class ingestion (M0).

Loads a site's *real* metered half-hourly series from CSV, validates its integrity, and
summarises it into the coverage metadata the `SiteModel`/data-tier layer consumes. This is
the most important real input GridQ takes (see docs/parallel_development_plan.md §5).

Accepted shape (full spec: docs/input_data_formats.md):

- **Required:** ``timestamp`` and one of ``import_kw`` / ``import_kwh``.
- **Optional:** ``export_kw`` / ``export_kwh``, ``site_load_kw``, ``meter_id``, ``quality_flag``.

Hard rules this module obeys:

- **Never invents data.** Missing intervals are *counted and reported*, never back-filled.
  Negative or null readings are *flagged*, never silently corrected. The returned frame has
  exactly as many rows as the input file.
- **Timestamps must parse.** A file with unparseable timestamps raises `HalfHourlyDataError`
  naming the offending values — it does not coerce-then-guess.
- **Energy↔power is a labelled unit transform, not a fabricated value.** When only
  ``import_kwh`` is given, the average kW per interval is derived deterministically as
  ``kWh × (60 / interval_minutes)`` (≡ ``× 2`` for 30-minute periods) and labelled as derived.
"""

from __future__ import annotations

import warnings
from datetime import date
from pathlib import Path

import pandas as pd
from pydantic import Field

from gridq.governance.dpa_guard import check_load
from gridq.ingestion.access_log import record_hh_access
from gridq.ingestion.validators import (
    HalfHourlyDataError,
    Severity,
    ValidationReport,
    require_columns,
)
from gridq.schemas.provenance import GridQModel
from gridq.schemas.site import LoadDataSummary

# Canonical column names → recognised aliases (all compared case-insensitively, stripped).
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "datetime", "date_time", "settlement_datetime", "time", "ts"),
    "import_kw": ("import_kw", "import_power_kw", "demand_kw", "kw_import", "import_avg_kw"),
    "import_kwh": (
        "import_kwh",
        "import_energy_kwh",
        "kwh_import",
        "consumption_kwh",
        # Bare single-column consumption exports (common installer/normalised format,
        # e.g. `timestamp,kwh`): a lone energy column is site import/consumption.
        "kwh",
        "energy_kwh",
    ),
    "export_kw": ("export_kw", "export_power_kw", "generation_kw", "kw_export"),
    "export_kwh": ("export_kwh", "export_energy_kwh", "kwh_export"),
    "site_load_kw": ("site_load_kw", "load_kw", "net_load_kw"),
    # WP-UW-07 (D106): the metered reactive channels, both quadrants. Optional; a file
    # without them loads exactly as before and the dispatch keeps its labelled unity-PF bound.
    "reactive_import_kvarh": (
        "reactive_import_kvarh",
        "import_kvarh",
        "kvarh_import",
        "reactive_import_energy_kvarh",
    ),
    "reactive_export_kvarh": (
        "reactive_export_kvarh",
        "export_kvarh",
        "kvarh_export",
        "reactive_export_energy_kvarh",
    ),
    "reactive_import_kvar": ("reactive_import_kvar", "import_kvar", "kvar_import"),
    "reactive_export_kvar": ("reactive_export_kvar", "export_kvar", "kvar_export"),
    "meter_id": ("meter_id", "mpan", "meter", "mpan_core"),
    "quality_flag": ("quality_flag", "quality", "flag", "status"),
}

_NUMERIC_COLUMNS = (
    "import_kw",
    "import_kwh",
    "export_kw",
    "export_kwh",
    "site_load_kw",
    "reactive_import_kvarh",
    "reactive_export_kvarh",
    "reactive_import_kvar",
    "reactive_export_kvar",
)

DEFAULT_INTERVAL_MINUTES = 30

#: Above this share of exact-zero readings, the zeros are treated as a genuine load pattern (a
#: weekend/night shutdown) rather than meter dropouts, and are not flagged. Below it, a run of
#: zeros on an otherwise never-idle site is the dropout signature. A data-integrity threshold,
#: not a pricing assumption — it carries no money (same precedent as `data_tier.py`).
_ZERO_RUN_SUSPICION_MAX_PCT = 10.0


class HalfHourlyCoverage(GridQModel):
    """Integrity + coverage metrics for a half-hourly series (output of `validate_*`)."""

    row_count: int
    present_intervals: int  # unique, parseable timestamps
    expected_intervals: int | None  # over [start, end] at the inferred grid
    missing_interval_count: int | None  # expected − present; NEVER back-filled
    duplicate_timestamp_count: int  # surplus rows sharing a timestamp
    start: date | None = None
    end: date | None = None
    coverage_days: float | None = None
    approx_months: float | None = None
    completeness_pct: float | None = None
    granularity_minutes: int | None = None

    # --- usable-reading integrity (hostile review F-07) -------------------------------------
    # A timestamp is not a reading. `present_intervals` counts unique parseable TIMESTAMPS, so a
    # row whose import value is null still counted as "present" and a series 40% full of nulls
    # could reach 100% completeness and grade T2 — the system's strongest trust label — while the
    # missing fraction was silently extrapolated over by the annualisation. Completeness is now
    # measured on USABLE readings; the timestamp-based figure is kept under its own name so the
    # two can never be confused again.
    #: Timestamps carrying a non-null, non-negative import reading. The honest denominator.
    usable_intervals: int | None = None
    #: Timestamps present but carrying no usable import value.
    null_reading_count: int | None = None
    #: Readings of exactly zero. A legal value, never dropped — but on a continuously-occupied
    #: site a run of exact zeros is usually a meter dropout recorded as 0, which depresses the
    #: modelled baseline and understates avoided-import value. Counted so it can be judged.
    zero_reading_count: int | None = None
    #: Negative import readings (import channels should not be negative). Flagged, never corrected.
    negative_reading_count: int | None = None
    #: The OLD completeness definition — unique timestamps ÷ expected. Retained for continuity and
    #: diagnostics only; never use it as a data-quality gate.
    timestamp_completeness_pct: float | None = None


class HalfHourlySummary(GridQModel):
    """A full, serialisable summary of a half-hourly series.

    Carries the coverage block plus the load statistics and a `ValidationReport`. This is
    the object the data-tier classifier (`infer_data_tier_from_hh_summary`) consumes and the
    bridge to `SiteModel.load_data` (`to_load_data_summary`).
    """

    coverage: HalfHourlyCoverage
    import_basis: str  # "power" (import_kw given) | "energy" (derived from import_kwh)
    has_export: bool
    max_import_kw: float | None = None
    min_import_kw: float | None = None
    mean_import_kw: float | None = None
    max_export_kw: float | None = None
    # --- measured energy (hostile review F-26) ----------------------------------------------
    # The measured total was never computed anywhere, which is why a declared `annual_kwh` could
    # never be checked against the series actually being priced (F-10). Without it, gross unit
    # errors and wrong-meter attachments in the primary input are undetectable by construction.
    #: Total import energy actually measured over the covered window.
    total_import_kwh: float | None = None
    #: `total_import_kwh` scaled to a full year by the covered fraction. Meaningful ONLY on
    #: near-full-year coverage — on a part-year sample this inherits the seasonal bias of the
    #: months present (finding F-40/F-43), so it is reported for reconciliation, never priced.
    annualised_import_kwh: float | None = None
    negative_import_count: int = 0
    negative_export_count: int = 0
    null_import_count: int = 0
    null_export_count: int = 0
    file_ref: str | None = None
    report: ValidationReport = Field(default_factory=ValidationReport)

    @property
    def warnings(self) -> list[str]:
        """Human-readable warning strings (convenience for callers/telemetry)."""
        return self.report.messages(severities=[Severity.WARNING, Severity.ERROR])


def _canonicalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename recognised aliases to canonical names; lower/strip everything else."""
    lookup: dict[str, str] = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            lookup[alias] = canonical
    rename: dict[str, str] = {}
    for col in df.columns:
        key = str(col).strip().lower()
        rename[col] = lookup.get(key, key)
    return df.rename(columns=rename)


def _parse_timestamps(df: pd.DataFrame) -> pd.Series:
    """Parse the ``timestamp`` column strictly; raise naming offenders if any fail.

    Timezone handling is deterministic and never guessed:

    - **Naive** values (no UTC offset) stay naive — they are the site's *local clock*,
      labelling the start of each half-hour period (the documented convention).
    - A **uniform UTC offset** yields a tz-aware series; the offset is kept, not stripped.
    - **Mixed offsets** (+00:00/+01:00 — the standard UK HH export across a clock change)
      are re-parsed onto a single tz-aware **UTC** axis. That is a labelled conversion of
      the same physical instants — no row is dropped or re-timed.

    Unparseable values still raise `HalfHourlyDataError` naming the offenders.
    """
    raw = df["timestamp"]
    with warnings.catch_warnings():
        # Mixed/unrecognised formats fall back to per-element parsing; that is expected for
        # real installer files, so suppress the noisy notice (still strict via the NaT check).
        warnings.simplefilter("ignore", UserWarning)
        try:
            parsed = pd.to_datetime(raw, errors="coerce", utc=False)
        except ValueError:
            # Mixed UTC offsets cannot share one naive/fixed-offset dtype (pandas raises);
            # re-parse onto a single UTC axis. Unparseable values still become NaT and fail
            # the strict check below.
            parsed = pd.to_datetime(raw, errors="coerce", utc=True)
    if parsed.dtype == object:
        # Older pandas returns object dtype (per-row offsets) for mixed-offset input instead
        # of raising; normalise to the same single UTC axis.
        parsed = pd.to_datetime(raw, errors="coerce", utc=True)
    # A value is unparseable if it became NaT but was not already null in the source.
    bad_mask = parsed.isna() & raw.notna()
    if bool(bad_mask.any()):
        offenders = raw[bad_mask].astype(str).unique().tolist()[:5]
        raise HalfHourlyDataError(
            f"timestamp column has {int(bad_mask.sum())} unparseable value(s); "
            f"first offenders: {offenders}. Timestamps are never guessed — fix the source."
        )
    if bool(parsed.isna().any()):
        raise HalfHourlyDataError(
            f"timestamp column has {int(parsed.isna().sum())} empty value(s); "
            "every half-hourly row must carry a timestamp."
        )
    return parsed


def _read_tabular(path: str | Path) -> pd.DataFrame:
    """Read a CSV **or** Excel (.xlsx/.xls) half-hourly file into a raw DataFrame. Invents nothing.

    Dispatches on the file extension. Excel is read via pandas/openpyxl; a multi-sheet workbook is
    **rejected** with an error naming the sheets found — the intended sheet is never guessed (supply
    a single-sheet file or export the intended sheet to CSV). Read failures raise
    `HalfHourlyDataError`. No rows are added, dropped, filled or re-typed here.
    """
    suffix = Path(path).suffix.lower()
    try:
        if suffix in (".xlsx", ".xls"):
            sheets = pd.read_excel(path, sheet_name=None)  # {sheet_name: frame} for every sheet
            if not sheets:
                raise HalfHourlyDataError(f"half-hourly workbook {path} has no sheets.")
            if len(sheets) > 1:
                raise HalfHourlyDataError(
                    f"half-hourly workbook {path} has {len(sheets)} sheets "
                    f"({', '.join(sheets)}); the sheet to use is never guessed. Supply a "
                    "single-sheet file or export the intended sheet to CSV."
                )
            return next(iter(sheets.values()))
        return pd.read_csv(path)
    except FileNotFoundError as exc:
        raise HalfHourlyDataError(f"half-hourly file not found: {path}") from exc
    except HalfHourlyDataError:
        raise
    except ImportError as exc:  # openpyxl/xlrd missing for an Excel read
        raise HalfHourlyDataError(
            f"could not read half-hourly Excel file {path}: {exc} (install openpyxl)."
        ) from exc
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError, OSError) as exc:
        raise HalfHourlyDataError(f"could not read half-hourly file {path}: {exc}") from exc


def load_half_hourly_csv(
    path: str | Path,
    *,
    expected_interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
    site_id: str | None = None,
    actor: str | None = None,
    is_personal_data: bool = False,
) -> pd.DataFrame:
    """Read and normalise a half-hourly **CSV or Excel (.xlsx/.xls)** file into a DataFrame.

    Invents nothing. Returns a frame with canonical column names, a parsed ``timestamp`` column,
    numeric load columns coerced to float (unparseable cells become NaN and are *counted later*,
    not filled), and **exactly the input row count**. Raises `HalfHourlyDataError` if the
    file is unreadable, has no rows, lacks a required column, or has unparseable timestamps.
    (The name is kept for compatibility; the loader dispatches on extension — see `_read_tabular`.)

    ``expected_interval_minutes`` is recorded for downstream coverage maths only; this
    function performs no resampling.

    Every successful read is recorded in the confidential-data access log (M0 security floor)
    via `record_hh_access`; ``site_id``/``actor``/``is_personal_data`` are carried onto the log
    entry so the access is attributable. Logging is a side effect only — it never alters the
    returned frame or any downstream hash.

    The DPA guard runs first (WP-GRC-05): a customer dataset without valid, verified DPA evidence
    raises `gridq.governance.DpaEvidenceMissing` before a byte is parsed, and the basis the guard
    allowed the read on is written onto the access-log entry.
    """
    decision = check_load(path, site_id=site_id)
    df = _read_tabular(path)

    if df.empty:
        raise HalfHourlyDataError(f"half-hourly file {path} has no data rows.")

    df = _canonicalise_columns(df)
    require_columns(
        list(df.columns),
        required=["timestamp"],
        any_of=[["import_kw", "import_kwh"]],
        context=f"half-hourly CSV {path}",
        error_cls=HalfHourlyDataError,
    )

    df = df.copy()
    df["timestamp"] = _parse_timestamps(df)
    for col in _NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    # Stash the declared grid for the summary step (metadata only; no resampling).
    df.attrs["expected_interval_minutes"] = int(expected_interval_minutes)
    # Confidential-data access is logged by construction, after a successful read.
    record_hh_access(
        str(path),
        site_id=site_id,
        actor=actor,
        row_count=int(len(df)),
        is_personal_data=is_personal_data,
        dpa_basis=decision.basis,
    )
    return df


def validate_half_hourly_coverage(
    df: pd.DataFrame,
    *,
    expected_interval_minutes: int | None = None,
) -> HalfHourlyCoverage:
    """Compute integrity + coverage metrics for a (loaded) half-hourly frame.

    Counts duplicate timestamps and missing intervals over the observed window — it never
    fills, drops or re-times a row. ``expected_interval_minutes`` defaults to the value
    recorded by `load_half_hourly_csv` (else 30).
    """
    if "timestamp" not in df.columns:
        raise HalfHourlyDataError(
            "frame has no 'timestamp' column — load it via load_half_hourly_csv first."
        )
    interval = int(
        expected_interval_minutes
        if expected_interval_minutes is not None
        else df.attrs.get("expected_interval_minutes", DEFAULT_INTERVAL_MINUTES)
    )

    ts = pd.to_datetime(df["timestamp"])
    row_count = int(len(df))
    present = int(ts.nunique())
    duplicates = row_count - present

    # F-07: count USABLE readings, not merely timestamps. The import column is whichever of the
    # canonical pair the loader produced; with neither, nothing is usable and we say so rather
    # than inferring completeness from timestamps alone.
    import_col = next((c for c in ("import_kw", "import_kwh") if c in df.columns), None)
    usable: int | None = None
    nulls: int | None = None
    zeros: int | None = None
    negatives: int | None = None
    if import_col is not None:
        values = pd.to_numeric(df[import_col], errors="coerce")
        null_mask = values.isna()
        nulls = int(null_mask.sum())
        negatives = int((values < 0).sum())
        zeros = int((values == 0).sum())
        # Usable = a real, non-negative number. Zeros stay usable (zero is a legal reading) but
        # are counted above so a suspicious run of them is visible rather than silently priced.
        usable = int(((~null_mask) & (values >= 0)).sum())

    start_ts = ts.min()
    end_ts = ts.max()
    start = start_ts.date() if pd.notna(start_ts) else None
    end = end_ts.date() if pd.notna(end_ts) else None

    coverage_days: float | None = None
    expected: int | None = None
    missing: int | None = None
    completeness: float | None = None
    timestamp_completeness: float | None = None
    granularity: int | None = None

    if pd.notna(start_ts) and pd.notna(end_ts) and present > 0:
        span_seconds = (end_ts - start_ts).total_seconds()
        coverage_days = span_seconds / 86400.0
        step_seconds = interval * 60
        expected = int(round(span_seconds / step_seconds)) + 1
        missing = max(expected - present, 0)
        timestamp_completeness = (
            round(min(present / expected * 100.0, 100.0), 4) if expected else None
        )
        # F-07: the gate-bearing figure is USABLE readings ÷ expected intervals. It falls back to
        # the timestamp figure only when there is no import column at all to judge (in which case
        # nothing downstream can price the series anyway).
        basis = usable if usable is not None else present
        completeness = round(min(basis / expected * 100.0, 100.0), 4) if expected else None
        # Infer the actual median grid spacing to detect a non-HH or irregular file.
        if present > 1:
            diffs = ts.drop_duplicates().sort_values().diff().dropna()
            if not diffs.empty:
                granularity = int(round(diffs.median().total_seconds() / 60))

    approx_months = round(coverage_days / 30.436875, 4) if coverage_days is not None else None

    return HalfHourlyCoverage(
        row_count=row_count,
        present_intervals=present,
        expected_intervals=expected,
        missing_interval_count=missing,
        duplicate_timestamp_count=duplicates,
        start=start,
        end=end,
        coverage_days=round(coverage_days, 4) if coverage_days is not None else None,
        approx_months=approx_months,
        completeness_pct=completeness,
        granularity_minutes=granularity,
        usable_intervals=usable,
        null_reading_count=nulls,
        zero_reading_count=zeros,
        negative_reading_count=negatives,
        timestamp_completeness_pct=timestamp_completeness,
    )


def _import_kw_series(df: pd.DataFrame, interval_minutes: int) -> tuple[pd.Series, str]:
    """Return (import in kW, basis). Derives kW from kWh by a labelled unit transform."""
    if "import_kw" in df.columns:
        return df["import_kw"], "power"
    factor = 60.0 / interval_minutes  # kWh per interval → average kW
    return df["import_kwh"] * factor, "energy"


def _export_kw_series(df: pd.DataFrame, interval_minutes: int) -> pd.Series | None:
    if "export_kw" in df.columns:
        return df["export_kw"]
    if "export_kwh" in df.columns:
        return df["export_kwh"] * (60.0 / interval_minutes)
    return None


def summarise_half_hourly_data(
    df: pd.DataFrame,
    *,
    allow_negative: bool = False,
    file_ref: str | None = None,
    expected_interval_minutes: int | None = None,
) -> HalfHourlySummary:
    """Summarise a loaded half-hourly frame into coverage + load stats + a report.

    Flags (does not fix) duplicates, missing intervals, null readings and — unless
    ``allow_negative=True`` — negative import/export readings. The summary's row count
    always equals the input frame's row count; no value is invented.
    """
    interval = int(
        expected_interval_minutes
        if expected_interval_minutes is not None
        else df.attrs.get("expected_interval_minutes", DEFAULT_INTERVAL_MINUTES)
    )
    coverage = validate_half_hourly_coverage(df, expected_interval_minutes=interval)
    report = ValidationReport()

    import_kw, basis = _import_kw_series(df, interval)
    export_kw = _export_kw_series(df, interval)

    null_import = int(import_kw.isna().sum())
    neg_import = int((import_kw < 0).sum())
    null_export = int(export_kw.isna().sum()) if export_kw is not None else 0
    neg_export = int((export_kw < 0).sum()) if export_kw is not None else 0

    max_import = float(import_kw.max()) if import_kw.notna().any() else None
    min_import = float(import_kw.min()) if import_kw.notna().any() else None
    mean_import = float(import_kw.mean()) if import_kw.notna().any() else None
    max_export = (
        float(export_kw.max()) if export_kw is not None and export_kw.notna().any() else None
    )

    # F-26: the measured energy total. kW is an average over the interval, so energy is
    # kW x interval_hours summed — a labelled unit transform, never a fabricated value.
    interval_hours = interval / 60.0
    total_import_kwh: float | None = None
    annualised_import_kwh: float | None = None
    if import_kw.notna().any():
        total_import_kwh = float(import_kw.fillna(0.0).sum()) * interval_hours
        covered_days = coverage.coverage_days
        if covered_days and covered_days > 0:
            annualised_import_kwh = total_import_kwh * (365.25 / covered_days)

    # F-07 / Harwell defect H-1: a run of exact zeros inside an otherwise continuously-occupied
    # series is almost always a meter dropout recorded as 0, not real consumption. It is a LEGAL
    # reading so it is never dropped — but it biases the model in a known direction (it depresses
    # the modelled baseline, understating avoided-import value and the peak-shaving headroom), so
    # it must be judged rather than silently priced. Zeros only look suspicious on a site that is
    # otherwise never idle — see the share test below.
    zeros = coverage.zero_reading_count or 0
    if zeros and mean_import and coverage.usable_intervals:
        zero_pct = 100.0 * zeros / coverage.usable_intervals
        nonzero = import_kw[import_kw > 0]
        typical = float(nonzero.quantile(0.05)) if not nonzero.empty else 0.0
        # The discriminator is the SHARE of zeros, not just the height of the baseload. A site
        # that genuinely shuts down (weekends, night shifts) is zero for a large, structured part
        # of the year — that is a load pattern, not a fault, and must not be nagged. A small
        # scattered minority of exact zeros on a site that is otherwise never idle is the
        # signature of a meter dropout recorded as 0. Harwell: 1.9% of the year, against a site
        # that never drops below ~55 kW otherwise.
        scattered_minority = zero_pct >= 0.1 and zero_pct <= _ZERO_RUN_SUSPICION_MAX_PCT
        if scattered_minority and typical > 0.1 * mean_import:
            report.add(
                "hh.suspicious_zero_readings",
                Severity.WARNING,
                f"{zeros} interval(s) ({zero_pct:.2f}% of usable readings) read exactly 0, but "
                f"this site's 5th-percentile non-zero load is {typical:.1f} kW against a "
                f"{mean_import:.1f} kW mean — it is not a site that genuinely falls idle. These "
                "are most likely meter dropouts recorded as zero. They are retained, never "
                "corrected, but they depress the modelled baseline and understate avoided-import "
                "value; confirm with the supplier before underwriting on them.",
                detail={"zero_intervals": str(zeros), "pct_of_usable": f"{zero_pct:.4f}"},
            )

    if coverage.duplicate_timestamp_count > 0:
        report.add(
            "hh.duplicate_timestamps",
            Severity.WARNING,
            f"{coverage.duplicate_timestamp_count} duplicate timestamp row(s) detected; "
            "they are reported, not de-duplicated.",
            detail={"count": str(coverage.duplicate_timestamp_count)},
        )
    if coverage.missing_interval_count:
        report.add(
            "hh.missing_intervals",
            Severity.WARNING,
            f"{coverage.missing_interval_count} half-hourly interval(s) are missing over "
            f"the coverage window; gaps are counted, never back-filled.",
            detail={
                "missing": str(coverage.missing_interval_count),
                "present": str(coverage.present_intervals),
                "expected": str(coverage.expected_intervals),
            },
        )
    if null_import > 0:
        report.add(
            "hh.null_import",
            Severity.WARNING,
            f"{null_import} import reading(s) are blank/non-numeric; flagged, not filled.",
            detail={"count": str(null_import)},
        )
    if neg_import > 0 and not allow_negative:
        report.add(
            "hh.negative_import",
            Severity.WARNING,
            f"{neg_import} negative import reading(s) detected (allow_negative=False); "
            "negative import is physically unusual for a consuming site — verify the source.",
            detail={"count": str(neg_import)},
        )
    if neg_export > 0 and not allow_negative:
        report.add(
            "hh.negative_export",
            Severity.WARNING,
            f"{neg_export} negative export reading(s) detected (allow_negative=False); "
            "verify the sign convention of the source.",
            detail={"count": str(neg_export)},
        )
    if coverage.granularity_minutes is not None and coverage.granularity_minutes != interval:
        report.add(
            "hh.irregular_grid",
            Severity.WARNING,
            f"inferred grid is ~{coverage.granularity_minutes} min, not the expected "
            f"{interval} min; coverage maths assume a {interval}-min grid.",
            detail={"inferred_minutes": str(coverage.granularity_minutes)},
        )

    return HalfHourlySummary(
        coverage=coverage,
        import_basis=basis,
        has_export=export_kw is not None,
        max_import_kw=round(max_import, 6) if max_import is not None else None,
        min_import_kw=round(min_import, 6) if min_import is not None else None,
        mean_import_kw=round(mean_import, 6) if mean_import is not None else None,
        total_import_kwh=round(total_import_kwh, 3) if total_import_kwh is not None else None,
        annualised_import_kwh=(
            round(annualised_import_kwh, 3) if annualised_import_kwh is not None else None
        ),
        max_export_kw=round(max_export, 6) if max_export is not None else None,
        negative_import_count=neg_import,
        negative_export_count=neg_export,
        null_import_count=null_import,
        null_export_count=null_export,
        file_ref=file_ref,
        report=report,
    )


def to_load_data_summary(summary: HalfHourlySummary) -> LoadDataSummary:
    """Bridge a `HalfHourlySummary` into the `SiteModel.load_data` contract block.

    Carries only *measured* coverage metadata; ``annual_kwh`` is deliberately left ``None``
    here (it must come from the real series or a stated annual figure, never a guess).
    """
    cov = summary.coverage
    return LoadDataSummary(
        annual_kwh=None,
        half_hourly_file_ref=summary.file_ref,
        half_hourly_rows=cov.present_intervals,
        start_date=cov.start,
        end_date=cov.end,
        missing_intervals=cov.missing_interval_count,
        max_import_kw=summary.max_import_kw,
        max_export_kw=summary.max_export_kw,
        validation_warnings=summary.warnings,
    )
