"""Deterministic data-tier classification (M0).

Grades a site's load data into the three tiers the underwriting telemetry reports against
(System Brief M0; docs/issues/008-ingestion-workflow.md):

- **T0** — annual kWh + tariff + postcode; no half-hourly data.
- **T1** — *some* genuine half-hourly data, below the validated-12-month bar.
- **T2** — ≥ ~12 months of half-hourly data that pass completeness validation.

The classifier is a pure function of its input (`HalfHourlySummary` + flags), so the same
data always yields the same tier — grading is reproducible.

### A note on the tier-boundary constants

The numbers below are **structural calendar boundaries** (48 half-hours/day; ~12 months ≈
17 520 periods) plus a v1 ingestion completeness floor. They are *not* pricing assumptions
and carry no money — so they live here, labelled, rather than in the assumption library.
This mirrors the existing precedent for `BASELINE_POWER_FRACTION` (a labelled integration
heuristic deliberately kept out of X.1). The `SiteModel` schema already hard-codes the same
17 520-period T2 bar. Whether the *completeness floor* should be versioned in X.1 is raised
as an OPEN_COMMENTS question for the team; until then it is a documented default here.
"""

from __future__ import annotations

from gridq.ingestion.hh_data import HalfHourlySummary
from gridq.schemas.enums import DataTier

# --- structural tier boundaries (see module docstring) ---------------------

#: Half-hourly periods in a standard (non-DST) day.
HALF_HOURLY_PERIODS_PER_DAY = 48

#: ~12 months of half-hourly periods (48 × 365). The T2 floor; matches the SiteModel schema.
T2_MIN_PERIODS = 17_520

#: Tolerance (in periods) for DST/leap-year wobble so a genuine ~12-month series still grades
#: T2 even if it is a handful of intervals short of the nominal 17 520.
T2_PERIOD_TOLERANCE = HALF_HOURLY_PERIODS_PER_DAY * 2  # ±2 days

#: v1 completeness floor (% of expected intervals present) required for T2. Documented
#: default, candidate for X.1 versioning — see module docstring + OPEN_COMMENTS.
T2_MIN_COMPLETENESS_PCT = 90.0

#: Minimum *calendar span* (days) for T2 — "≥12 months" is a duration, not just an interval count.
#: A dense sub-grid file can hit the period floor over a short window; the span gate rejects that.
#: 365 − 2 days, matching the ±2-day period tolerance (a full-year series spans ~365 days).
T2_MIN_COVERAGE_DAYS = 363.0

#: A series with at least this many present intervals counts as genuine (non-trivial) HH
#: data for T1. A single stray interval is not T1.
T1_MIN_PERIODS = 2


def infer_data_tier_from_hh_summary(
    summary: HalfHourlySummary | None,
    *,
    has_annual_kwh: bool = False,
) -> DataTier:
    """Classify a site's data tier from its half-hourly summary.

    - No summary (or no genuine HH data) → **T0** (the floor; assumes annual kWh + tariff +
      postcode are present, which is the T0 minimum the SiteModel already requires).
    - Genuine HH below the validated-12-month bar → **T1**.
    - ≥ ~12 months present (within tolerance), spanning ≥ ~12 months of *calendar time*, *and*
      completeness ≥ the floor → **T2**. The calendar-span gate stops a dense sub-30-minute file
      from grading T2 on interval count alone (the Brief's T2 is "≥12 months").

    ``has_annual_kwh`` is accepted for symmetry/telemetry; it does not change the result
    because the absence of HH data lands at T0 regardless. The function never *upgrades* a
    tier on the strength of data it does not have.
    """
    if summary is None:
        return DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE

    present = summary.coverage.present_intervals
    if present < T1_MIN_PERIODS:
        return DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE

    completeness = summary.coverage.completeness_pct
    coverage_days = summary.coverage.coverage_days
    meets_period_floor = present >= (T2_MIN_PERIODS - T2_PERIOD_TOLERANCE)
    meets_completeness = completeness is not None and completeness >= T2_MIN_COMPLETENESS_PCT
    # A genuine ~12-month *span*, not just enough intervals over a dense short window.
    meets_duration = coverage_days is not None and coverage_days >= T2_MIN_COVERAGE_DAYS

    if meets_period_floor and meets_completeness and meets_duration:
        return DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY

    return DataTier.T1_PARTIAL_HALF_HOURLY


def classify_site_data_tier(
    *,
    hh_summary: HalfHourlySummary | None = None,
    has_annual_kwh: bool = False,
) -> DataTier:
    """Convenience wrapper mirroring `infer_data_tier_from_hh_summary` for site-level use."""
    return infer_data_tier_from_hh_summary(hh_summary, has_annual_kwh=has_annual_kwh)
