"""Top-level ingestion loaders (M0).

The entry points that turn a file on disk into a validated in-memory object:

- `load_site_model_json` — a structured `SiteModel` JSON → validated `SiteModel`.

Half-hourly and market-forecast file loaders live in their own modules
(`gridq.ingestion.hh_data`, `gridq.ingestion.market_forecasts`) and are re-exported from the
package root for convenience. Every loader fails *cleanly* — a specific, actionable error
that names the offending file/field/reason — and none of them invents a missing value.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from gridq.governance.dpa_guard import check_load
from gridq.ingestion.access_log import record_hh_access
from gridq.ingestion.validators import SiteModelLoadError, ValidationReport, validate_site_model
from gridq.schemas.site import SiteModel


def _format_validation_error(exc: ValidationError, *, path: str | Path) -> str:
    """Render a Pydantic ValidationError into a readable, field-pathed message."""
    lines = [f"SiteModel JSON {path} failed schema validation ({exc.error_count()} error(s)):"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def load_site_model_json(path: str | Path, *, run_cross_checks: bool = True) -> SiteModel:
    """Load and validate a structured `SiteModel` JSON file.

    Validates against the versioned `SiteModel` schema (``extra='forbid'`` + the tier/coverage
    invariants) and, by default, runs the cross-field coherence checks in
    `validators.validate_site_model`. Any ``ERROR``-severity coherence issue is raised as a
    `SiteModelLoadError` (so an incoherent-but-schema-valid site is rejected distinctly from a
    schema-invalid one); warnings do not raise and remain available via
    `load_site_model_json_with_report`.

    Raises `SiteModelLoadError` with a specific, actionable message if the file is missing,
    is not valid JSON, or fails schema/coherence validation. Never fills a missing field.
    """
    site, report = load_site_model_json_with_report(path)
    if run_cross_checks and not report.ok:
        problems = "\n".join(f"  - {i.field or '<site>'}: {i.message}" for i in report.errors)
        raise SiteModelLoadError(
            f"SiteModel JSON {path} is schema-valid but failed coherence checks:\n{problems}"
        )
    return site


def load_site_model_json_with_report(
    path: str | Path,
) -> tuple[SiteModel, ValidationReport]:
    """Load a `SiteModel` JSON and return ``(site, coherence_report)``.

    Schema failures still raise `SiteModelLoadError`; the returned report carries the
    cross-field coherence findings (which may include warnings *and* errors) for callers that
    want to inspect them rather than raise.

    The DPA guard runs before the file is read (WP-GRC-05): a customer site record without valid,
    verified DPA evidence raises `gridq.governance.DpaEvidenceMissing`. A customer-data read is
    written to the access log with the basis the guard allowed it on.
    """
    file_path = Path(path)
    decision = check_load(file_path)
    try:
        raw_text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SiteModelLoadError(f"SiteModel JSON not found: {path}") from exc
    except OSError as exc:
        raise SiteModelLoadError(f"could not read SiteModel JSON {path}: {exc}") from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SiteModelLoadError(
            f"SiteModel JSON {path} is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, col {exc.colno})"
        ) from exc

    if not isinstance(data, dict):
        raise SiteModelLoadError(
            f"SiteModel JSON {path} must contain a JSON object at the top level, "
            f"got {type(data).__name__}."
        )

    try:
        site = SiteModel.model_validate(data)
    except ValidationError as exc:
        raise SiteModelLoadError(_format_validation_error(exc, path=path)) from exc

    if decision.basis != "not_customer_data":
        record_hh_access(
            str(path), action="read_site_model", site_id=site.site_id, dpa_basis=decision.basis
        )
    return site, validate_site_model(site)
