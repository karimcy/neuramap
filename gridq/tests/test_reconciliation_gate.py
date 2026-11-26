"""Validation Gate 3 — the declared load basis must reconcile with the measured series.

Hostile review F-10 (declared metadata never reconciled with the file the solver prices) and F-26
(the measured total was never computed at all). The regression these guard against is not
hypothetical: the Grundon analysis carried a FOUR-MONTH meter sum as an annual figure and
understated one site's load by 13.8% (`docs/grundon_harwell/02_data_reconciliation.md`).

The asymmetry that makes this dangerous: battery kW/kWh, capex and Capacity Market revenue scale
off the DECLARED figures, while every revenue line comes from the MEASURED file. A stale declared
number therefore produces a capex-to-revenue mismatch at full confidence.
"""

from __future__ import annotations

import pandas as pd
import pytest

from gridq.ingestion.hh_data import summarise_half_hourly_data
from gridq.ingestion.reconciliation import reconcile_declared_against_measured


def _series(kw: float, *, days: int = 365) -> pd.DataFrame:
    """A flat half-hourly import series at ``kw``, starting 2025-01-01."""
    n = days * 48
    idx = pd.date_range("2025-01-01", periods=n, freq="30min")
    return pd.DataFrame({"timestamp": idx, "import_kw": [kw] * n})


def _summary(df: pd.DataFrame):
    return summarise_half_hourly_data(df, file_ref="test.csv")


def test_measured_total_is_now_computed(make_site) -> None:
    """F-26: the measured energy total must exist before anything can be reconciled against it."""
    summary = _summary(_series(100.0))
    # 100 kW flat for 365 days = 100 * 24 * 365 = 876,000 kWh
    assert summary.total_import_kwh == pytest.approx(876_000.0, rel=1e-6)
    assert summary.annualised_import_kwh is not None


def test_matching_declaration_reconciles(make_site) -> None:
    site = make_site().model_copy(deep=True)
    site.load_data.annual_kwh = 876_000.0
    site.load_data.max_import_kw = 100.0
    site.load_data.half_hourly_rows = 365 * 48

    result = reconcile_declared_against_measured(site, _summary(_series(100.0)))
    assert result.reconciles
    assert not result.warnings, result.summary()


def test_a_four_month_sum_declared_as_annual_is_caught(make_site) -> None:
    """THE GRUNDON DEFECT. Jan-Apr of a flat year is ~33% of it — must be an ERROR, not a warning.

    The real case: Ash Plant's Jan-Apr 2025 total (33,534 kWh) was carried as the annual figure
    against a true 66,766 kWh. Under-declaring the year is exactly this shape.
    """
    site = make_site().model_copy(deep=True)
    site.load_data.max_import_kw = 100.0
    site.load_data.half_hourly_rows = 365 * 48
    # Declare only the first four months of a flat 876 MWh year.
    site.load_data.annual_kwh = 876_000.0 * (4 / 12)

    result = reconcile_declared_against_measured(site, _summary(_series(100.0)))

    assert not result.reconciles, "a four-month sum declared as annual must NOT reconcile"
    annual = next(f for f in result.errors if f.field == "annual_kwh")
    assert annual.divergence_pct == pytest.approx(66.7, abs=1.0)
    assert "does NOT reconcile" in annual.message


def test_a_stale_declared_peak_is_caught(make_site) -> None:
    """The declared peak drives sizing and capex; the measured series drives revenue."""
    site = make_site().model_copy(deep=True)
    site.load_data.annual_kwh = 876_000.0
    site.load_data.half_hourly_rows = 365 * 48
    site.load_data.max_import_kw = 200.0  # 2x the measured 100 kW

    result = reconcile_declared_against_measured(site, _summary(_series(100.0)))
    assert not result.reconciles
    peak = next(f for f in result.errors if f.field == "max_import_kw")
    assert peak.divergence_pct == pytest.approx(100.0, abs=0.5)


def test_small_divergence_warns_but_does_not_block(make_site) -> None:
    """Meter reads and billing periods move totals a few percent legitimately."""
    site = make_site().model_copy(deep=True)
    site.load_data.max_import_kw = 100.0
    site.load_data.half_hourly_rows = 365 * 48
    site.load_data.annual_kwh = 876_000.0 * 1.08  # 8% out — warn, don't block

    result = reconcile_declared_against_measured(site, _summary(_series(100.0)))
    assert result.reconciles, "8% is a warning, not a blocker"
    assert any(f.field == "annual_kwh" for f in result.warnings)


def test_row_count_mismatch_flags_a_different_extract(make_site) -> None:
    site = make_site().model_copy(deep=True)
    site.load_data.annual_kwh = 876_000.0
    site.load_data.max_import_kw = 100.0
    site.load_data.half_hourly_rows = 12_096  # a different extract entirely

    result = reconcile_declared_against_measured(site, _summary(_series(100.0)))
    assert any(f.field == "half_hourly_rows" for f in result.warnings)


def test_suspicious_zero_readings_are_flagged(make_site) -> None:
    """Harwell defect H-1: 338 exact zeros inside a continuously-occupied series."""
    df = _series(100.0)
    df.loc[df.index[:338], "import_kw"] = 0.0  # meter dropouts recorded as zero

    summary = _summary(df)
    assert summary.coverage.zero_reading_count == 338
    codes = [issue.code for issue in summary.report.issues]
    assert "hh.suspicious_zero_readings" in codes, (
        f"a run of exact zeros on a never-idle site must be flagged; got {codes}"
    )


def test_a_genuinely_idle_site_is_not_flagged_for_zeros(make_site) -> None:
    """Zero is a legal reading. A site that really does shut down must not be nagged."""
    n = 365 * 48
    idx = pd.date_range("2025-01-01", periods=n, freq="30min")
    # Weekday-only operation: genuinely zero at weekends.
    kw = [0.0 if ts.weekday() >= 5 else 100.0 for ts in idx]
    summary = _summary(pd.DataFrame({"timestamp": idx, "import_kw": kw}))

    codes = [issue.code for issue in summary.report.issues]
    assert "hh.suspicious_zero_readings" not in codes, (
        "a site with a real weekend shutdown has a low 5th-percentile load and must not be flagged"
    )
