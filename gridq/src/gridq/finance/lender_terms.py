"""Supplied lender terms into the finance engine (WP-UW-03, D75 to D77).

`gridq.schemas.LenderTerms` is the typed input; this module is everything the engine does with it:

* **load / parse**: `load_lender_terms` reads a JSON or YAML file (the CLI's ``--lender-terms``);
  `lender_terms_from_fields` reads the flat ``lender_*`` fields of the app's new-quote form. Both
  raise `LenderTermsError` with a reader-facing message; neither fills in a missing figure.
* **convert**: `debt_terms_from_lender_terms` maps the terms onto the `DebtTerms` the debt
  machinery already runs on (case, amortisation shape, sculpting target included).
* **covenant**: `covenant_tiers_from_lender_terms` resolves the default and lock-up thresholds
  from the terms (D76). Terms that state no covenant give *no* threshold and a stated reason: the
  library's placeholder ``dscr_covenants.min_p90_dscr`` is never substituted for a lender's
  silence, because it would put a number on the record that no lender set.
* **refuse**: `period_refusal_reason` refuses a semi-annual or quarterly test period. The model
  is annual; reporting an annual ratio against a semi-annual covenant would be a substituted
  method, so the DSCR is unavailable with that reason instead.
* **label**: `lender_terms_warnings` puts the source on the record. Synthetic terms are labelled
  as synthetic on every case they reach.

Nothing here reads the assumption library for a lender figure.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from gridq.finance.debt import (
    COVENANT_SOURCE_LENDER_TERMS,
    SCULPTED_BASIS_BY_CASE,
    SCULPTING_ANNUITY,
    SCULPTING_STRAIGHT_LINE,
    CovenantTiers,
    DebtTerms,
)
from gridq.schemas.lender_terms import (
    AmortisationProfile,
    CalculationPeriod,
    LenderTerms,
    LenderTermsSource,
)

__all__ = [
    "LENDER_FORM_FIELDS",
    "LenderTermsError",
    "covenant_tiers_from_lender_terms",
    "debt_terms_from_lender_terms",
    "lender_terms_are_firm",
    "lender_terms_from_fields",
    "lender_terms_warnings",
    "load_lender_terms",
    "parse_lender_terms",
    "period_refusal_reason",
]


class LenderTermsError(ValueError):
    """Supplied lender terms could not be read or did not validate. The message is reader-facing."""


#: The reason a covenant is not evaluated when the supplied terms state none (D76).
NO_LENDER_COVENANT_REASON = (
    "Not evaluated: the supplied lender terms state no DSCR covenant, and the assumption library "
    "placeholder is not substituted for a lender's terms."
)


def _summarise_validation(exc: ValidationError) -> str:
    """One line per pydantic error, field path first, so a person can fix the input."""
    lines = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ())) or "(terms)"
        lines.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(lines)


def parse_lender_terms(data: object) -> LenderTerms:
    """Validate a mapping (already parsed from JSON/YAML/form) into `LenderTerms`."""
    if isinstance(data, LenderTerms):
        return data
    if not isinstance(data, Mapping):
        raise LenderTermsError(
            f"lender terms must be a mapping of fields, got {type(data).__name__}"
        )
    try:
        return LenderTerms.model_validate(dict(data))
    except ValidationError as exc:
        raise LenderTermsError(f"invalid lender terms: {_summarise_validation(exc)}") from exc


def parse_lender_terms_text(text: str, *, suffix: str = ".json") -> LenderTerms:
    """Parse JSON or YAML text into `LenderTerms` (YAML for ``.yaml``/``.yml``, else JSON)."""
    try:
        data = yaml.safe_load(text) if suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise LenderTermsError(f"lender terms are not valid {suffix.lstrip('.')}: {exc}") from exc
    return parse_lender_terms(data)


def load_lender_terms(path: Path) -> LenderTerms:
    """Read a lender-terms file (JSON, or YAML by extension) into `LenderTerms`."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise LenderTermsError(f"could not read lender terms file {path}: {exc}") from exc
    return parse_lender_terms_text(text, suffix=Path(path).suffix or ".json")


#: The app form's flat field names, mapped to the nested `LenderTerms` path they fill.
LENDER_FORM_FIELDS: dict[str, tuple[str, ...]] = {
    "lender_source": ("provenance", "source"),
    "lender_reference": ("provenance", "reference"),
    "lender_as_of": ("provenance", "as_of"),
    "lender_tenor_years": ("tenor_years",),
    "lender_interest_rate_pct": ("interest_rate_pct",),
    "lender_gearing_pct": ("gearing_pct",),
    "lender_amortisation": ("amortisation",),
    "lender_cashflow_case": ("cashflow_case",),
    "lender_calculation_period": ("calculation_period",),
    "lender_sculpting_target_dscr": ("sculpting_target_dscr",),
    "lender_default_dscr": ("covenant", "default_dscr"),
    "lender_lock_up_dscr": ("covenant", "lock_up_dscr"),
}


def lender_terms_from_fields(fields: Mapping[str, object]) -> LenderTerms | None:
    """Build `LenderTerms` from flat form fields, ``None`` when no lender field was filled in.

    A form with every ``lender_*`` field blank is a run without lender terms (the DSCR is then
    unavailable with its reason). A form with SOME fields filled is an attempt at terms, and an
    incomplete or invalid one raises `LenderTermsError`: silently dropping a half-entered term
    sheet would publish a run the user believes carries their lender's terms. A
    ``lender_terms_json`` field, when non-blank, is parsed as the whole object instead.
    """
    raw_json = fields.get("lender_terms_json")
    if isinstance(raw_json, str) and raw_json.strip():
        return parse_lender_terms_text(raw_json, suffix=".json")
    nested: dict[str, object] = {}
    for name, path in LENDER_FORM_FIELDS.items():
        value = fields.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        cursor = nested
        for part in path[:-1]:
            child = cursor.setdefault(part, {})
            assert isinstance(child, dict)
            cursor = child
        cursor[path[-1]] = value.strip() if isinstance(value, str) else value
    if not nested:
        return None
    return parse_lender_terms(nested)


def debt_terms_from_lender_terms(terms: LenderTerms) -> DebtTerms:
    """Map supplied `LenderTerms` onto the `DebtTerms` the debt machinery runs on."""
    case = terms.cashflow_case.value
    if terms.amortisation is AmortisationProfile.SCULPTED:
        basis = SCULPTED_BASIS_BY_CASE[case]
    elif terms.amortisation is AmortisationProfile.STRAIGHT_LINE:
        basis = SCULPTING_STRAIGHT_LINE
    else:
        basis = SCULPTING_ANNUITY
    return DebtTerms(
        gearing_pct=terms.gearing_pct,
        annual_rate_pct=terms.interest_rate_pct,
        tenor_years=terms.tenor_years,
        sculpting_basis=basis,
        cashflow_case=case,
        sculpting_target_dscr=terms.sculpting_target_dscr,
    )


def covenant_tiers_from_lender_terms(terms: LenderTerms) -> CovenantTiers:
    """The covenant thresholds the supplied terms state, never the library's (D76)."""
    if terms.covenant is None:
        return CovenantTiers(
            default_dscr=None,
            source=COVENANT_SOURCE_LENDER_TERMS,
            absent_reason=NO_LENDER_COVENANT_REASON,
        )
    absent = (
        None
        if terms.covenant.default_dscr is not None
        else (
            "Not evaluated: the supplied lender terms state a lock-up threshold but no default "
            "threshold; the lock-up test is reported on its own."
        )
    )
    return CovenantTiers(
        default_dscr=terms.covenant.default_dscr,
        lock_up_dscr=terms.covenant.lock_up_dscr,
        source=COVENANT_SOURCE_LENDER_TERMS,
        absent_reason=absent,
    )


def period_refusal_reason(terms: LenderTerms) -> str | None:
    """Why a supplied test period cannot be measured, or ``None`` for an annual test."""
    if terms.calculation_period is CalculationPeriod.ANNUAL:
        return None
    period = terms.calculation_period.value.replace("_", "-")
    return (
        f"The lender terms test the DSCR {period}, and GridQ's cash-flow model is annual. An "
        "annual ratio is not substituted for a "
        f"{period} covenant test, so no DSCR is reported and no debt is sized on these terms. "
        "Supply annual-test terms, or this is an open modelling item (WP-UW-03)."
    )


def lender_terms_are_firm(terms: LenderTerms) -> bool:
    """True only for term-sheet terms that state a default covenant: the bankable precondition."""
    return (
        terms.provenance.source is LenderTermsSource.TERM_SHEET
        and terms.covenant is not None
        and terms.covenant.default_dscr is not None
    )


def lender_terms_warnings(terms: LenderTerms) -> list[str]:
    """The source label every case carrying these terms states (dash-free; rendered verbatim)."""
    source = terms.provenance.source
    if source is LenderTermsSource.SYNTHETIC:
        return [
            "SYNTHETIC LENDER TERMS: the DSCR on this case is measured against made-up terms "
            f"({terms.label()}). They are not a lender's offer and this case is not a financing "
            "case; never present the DSCR or the covenant verdict as one."
        ]
    if source is LenderTermsSource.LENDER_INDICATION:
        return [
            f"Lender terms are an INDICATION, not a term sheet ({terms.label()}): the DSCR and "
            "covenant verdict are indicative and cannot support a bankable verdict."
        ]
    return [f"Lender terms supplied from a term sheet: {terms.label()}."]
