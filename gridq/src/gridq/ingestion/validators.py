"""Shared validation primitives for ingestion (M0).

This module holds the *typed* vocabulary the rest of ingestion speaks:

- a small exception hierarchy (`IngestionError` + specific subclasses) so callers can
  distinguish a bad SiteModel JSON from a bad half-hourly CSV from a bad forecast file;
- a `ValidationIssue` / `ValidationReport` pair — the structured, severity-tagged record
  that ingestion attaches to a result instead of ever guessing a value;
- column-presence helpers used by the CSV loaders;
- `validate_site_model(...)` — cross-field SiteModel checks that go *beyond* what the
  Pydantic schema already enforces (the schema is the first gate; this is the second).

**Data-integrity rule (binding — see docs/parallel_development_plan.md §5):** ingestion
never fabricates, guesses or interpolates a missing value. A missing or implausible input
becomes a `ValidationIssue` (warning) and/or a ranked data ask — never an invented number.
Nothing in this module mutates input data or fills a gap.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum

from pydantic import Field

from gridq.schemas.consents import ConsentState
from gridq.schemas.enums import ConsentStatus, CreditStatus, DataTier, ExportRoute
from gridq.schemas.provenance import GridQModel
from gridq.schemas.site import SiteModel


class IngestionError(Exception):
    """Base class for every ingestion failure (file/parse/validation)."""


class SiteModelLoadError(IngestionError):
    """A SiteModel JSON file could not be read, parsed or schema-validated."""


class HalfHourlyDataError(IngestionError):
    """A half-hourly CSV could not be read, parsed or normalised (e.g. bad timestamps)."""


class MarketForecastError(IngestionError):
    """A market/wholesale forecast file could not be read, parsed or normalised."""


class Severity(StrEnum):
    """Severity of a validation issue."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ValidationIssue(GridQModel):
    """One typed, machine-readable validation finding.

    ``code`` is a stable, kebab/snake identifier (e.g. ``"hh.missing_intervals"``) so
    downstream code and telemetry can branch on it without string-matching prose.
    """

    code: str
    severity: Severity
    message: str
    field: str | None = None
    detail: dict[str, str] = Field(default_factory=dict)


class ValidationReport(GridQModel):
    """A collection of `ValidationIssue`s with convenience accessors.

    A report is *advisory by default*: it never raises. Callers decide whether an
    ``ERROR``-severity issue is fatal for their step.
    """

    issues: list[ValidationIssue] = Field(default_factory=list)

    def add(
        self,
        code: str,
        severity: Severity,
        message: str,
        *,
        field: str | None = None,
        detail: dict[str, str] | None = None,
    ) -> None:
        """Append one issue to the report."""
        self.issues.append(
            ValidationIssue(
                code=code,
                severity=severity,
                message=message,
                field=field,
                detail=detail or {},
            )
        )

    @property
    def errors(self) -> list[ValidationIssue]:
        """All ``ERROR``-severity issues."""
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        """All ``WARNING``-severity issues."""
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def ok(self) -> bool:
        """True when the report carries no ``ERROR``-severity issues."""
        return not self.errors

    def messages(self, *, severities: Iterable[Severity] | None = None) -> list[str]:
        """Flatten issues to human-readable strings, optionally filtered by severity."""
        wanted = set(severities) if severities is not None else None
        return [i.message for i in self.issues if wanted is None or i.severity in wanted]


def require_columns(
    present: Sequence[str],
    *,
    required: Sequence[str],
    any_of: Sequence[Sequence[str]] = (),
    context: str,
    error_cls: type[IngestionError],
) -> None:
    """Raise ``error_cls`` if a required column (or one of an ``any_of`` group) is absent.

    - ``required`` — every name must appear in ``present``.
    - ``any_of`` — each inner group must have *at least one* member present (e.g.
      ``[["import_kw", "import_kwh"]]`` means "import power *or* import energy").

    The message names exactly what is missing and what was found — never a generic
    "invalid file".
    """
    present_set = {c.strip().lower() for c in present}

    missing = [c for c in required if c.lower() not in present_set]
    if missing:
        raise error_cls(
            f"{context}: missing required column(s) {missing}. "
            f"Found columns: {sorted(present_set)}."
        )

    for group in any_of:
        if not any(c.lower() in present_set for c in group):
            raise error_cls(
                f"{context}: need at least one of {list(group)}. "
                f"Found columns: {sorted(present_set)}."
            )


# --- SiteModel cross-field validation (second gate, after the schema) ------

_EXPORT_FORBIDDEN_FOR_EXPORT = {ConsentStatus.NOT_REQUIRED}


def validate_site_model(site: SiteModel) -> ValidationReport:
    """Run cross-field SiteModel checks beyond the Pydantic schema.

    The schema (``SiteModel``) already enforces field types, ``extra='forbid'`` and the
    tier/coverage invariants. This adds *coherence* checks that the schema cannot express
    on its own, and surfaces them as typed issues rather than exceptions so the pipeline
    can run degraded and *say so*. Returns a `ValidationReport`; raises nothing.
    """
    report = ValidationReport()

    _check_export_coherence(site, report)
    _check_export_arrangement_coherence(site, report)
    _check_connection_plausibility(site, report)
    _check_tier_vs_data(site, report)
    _check_counterparty(site, report)

    return report


def _exporting(site: SiteModel) -> bool:
    """A site exports iff it has positive export capacity (MEC > 0)."""
    return bool(site.connection.mec_kw and site.connection.mec_kw > 0)


def _check_export_coherence(site: SiteModel, report: ValidationReport) -> None:
    if not _exporting(site):
        return
    consents: ConsentState = site.consents
    if consents.export_mpan is None:
        report.add(
            "site.export_mpan_missing",
            Severity.WARNING,
            "Site has export capacity (mec_kw > 0) but no export MPAN recorded; "
            "export-side streams cannot be firmed without it.",
            field="consents.export_mpan",
        )
    for name in ("g99", "g100"):
        status: ConsentStatus = getattr(consents, name)
        if status in _EXPORT_FORBIDDEN_FOR_EXPORT:
            # WARNING, not ERROR (issue 018 / D8): consents are frontend-declared inputs, never a
            # backend hard-block. A 'not_required' export consent alongside export capacity is an
            # incoherence to surface and confirm, not a fatal error - the run must still proceed
            # and record the declared state. The `report.ok` seam in _site_from_form /
            # load_site_model_json is kept for future *structural* errors.
            report.add(
                f"site.{name}_incoherent_with_export",
                Severity.WARNING,
                f"Site exports (mec_kw > 0) but {name.upper()} consent is declared "
                f"'{status.value}', which is incoherent with exporting; the declared state is "
                "recorded and surfaced - confirm the consent record.",
                field=f"consents.{name}",
            )


def _check_export_arrangement_coherence(site: SiteModel, report: ValidationReport) -> None:
    """Warn (never fail) when an export route is declared with no export path evidenced.

    Soft by design (WARNING severity, never ERROR): the demo/intake path must still run. If a
    site declares a ``ppa_export`` or ``flexibility_aggregator`` route but there is no export
    path — no export capacity (``connection.mec_kw`` is unset or 0) AND no ``consents.export_mpan``
    — the arrangement cannot be delivered yet. That is a ranked data-ask (confirm the export
    connection / MPAN), not a failure. The ``aggregator_cut_pct`` range is enforced by the schema
    Field, not here.
    """
    arrangement = site.export_arrangement
    if arrangement is None:
        return
    if arrangement.route not in (ExportRoute.PPA_EXPORT, ExportRoute.FLEXIBILITY_AGGREGATOR):
        return
    if not _exporting(site) and site.consents.export_mpan is None:
        report.add(
            "site.export_route_without_export_path",
            Severity.WARNING,
            f"Export route '{arrangement.route.value}' is declared but no export path is "
            "evidenced (connection.mec_kw is unset/0 and no consents.export_mpan); confirm the "
            "export connection/MPAN before relying on the arrangement.",
            field="export_arrangement.route",
        )


def _check_connection_plausibility(site: SiteModel, report: ValidationReport) -> None:
    conn = site.connection
    if conn.mic_kw == 0:
        report.add(
            "site.mic_zero",
            Severity.WARNING,
            "Import capacity (mic_kw) is 0 — the connection cannot import; confirm MIC.",
            field="connection.mic_kw",
        )
    if conn.import_headroom_kw is not None and conn.import_headroom_kw < 0:
        report.add(
            "site.import_oversubscribed",
            Severity.INFO,
            "Negative import headroom — the site is over-subscribed against MIC at peak.",
            field="connection.import_headroom_kw",
        )


def _check_tier_vs_data(site: SiteModel, report: ValidationReport) -> None:
    ld = site.load_data
    if (
        site.data_tier == DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY
        and ld.half_hourly_file_ref is None
    ):
        # T2 carries coverage metadata (rows + window) per the schema, but the canonical
        # series itself should be referenced; flag a T2 with no file reference for review.
        report.add(
            "site.t2_no_file_ref",
            Severity.WARNING,
            "Site is tier T2 but load_data.half_hourly_file_ref is unset; the validated "
            "half-hourly series should be referenced before the quote is treated as firm.",
            field="load_data.half_hourly_file_ref",
        )
    if site.data_tier == DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE and ld.has_hh_coverage:
        report.add(
            "site.t0_has_hh",
            Severity.INFO,
            "Site is graded T0 but carries half-hourly coverage metadata; it may qualify "
            "for a higher tier — re-grade from the half-hourly summary.",
            field="data_tier",
        )


def _check_counterparty(site: SiteModel, report: ValidationReport) -> None:
    cp = site.counterparty
    if not cp.company_number:
        report.add(
            "site.company_number_missing",
            Severity.WARNING,
            "Counterparty company number is missing — required for the credit mapping (M9).",
            field="counterparty.company_number",
        )
    elif not _looks_like_uk_company_number(cp.company_number):
        # A light, deterministic format sanity check (no external/Companies House lookup): UK
        # company numbers are 8 characters — 8 digits, or a 2-letter prefix + 6 digits.
        report.add(
            "site.company_number_malformed",
            Severity.WARNING,
            f"Counterparty company number {cp.company_number!r} is not a well-formed UK company "
            "number (expected 8 alphanumeric characters); confirm before relying on the credit "
            "assessment.",
            field="counterparty.company_number",
        )
    if cp.credit_status in {CreditStatus.UNKNOWN, CreditStatus.NOT_ASSESSED}:
        report.add(
            "site.credit_status_unassessed",
            Severity.WARNING,
            f"Counterparty credit status is '{cp.credit_status.value}' — the deterministic "
            "credit->rate/tenor mapping (M9) cannot set a debt rate or tenor until a credit band "
            "(strong/adequate/weak) is supplied; no band is assumed.",
            field="counterparty.credit_status",
        )


def _looks_like_uk_company_number(value: str) -> bool:
    """True if ``value`` is a plausible UK company number (8 alphanumeric chars). Format-only."""
    candidate = value.strip()
    return len(candidate) == 8 and candidate.isalnum()
