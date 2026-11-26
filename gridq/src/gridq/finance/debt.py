"""Debt sizing, the debt-service schedule, DSCR and the covenant check (M5, part 2).

The debt machinery here is fully implemented and tested *as machinery* — given debt terms and
a CFADS profile it produces a debt-service schedule, the DSCR by year, the minimum DSCR and a
covenant verdict. It is gated on real inputs: the early releases (``gb_v0_1``/``gb_v1_0``) carry
**no** debt gearing, so the integration path finds the debt structure absent and reports DSCR
**unavailable, with a warning — never guessed**. From ``gb_v1_2`` the library carries a labelled
placeholder gearing (pending Sim's portfolio finance, BLOCKERS B001) and the M9 credit mapping
supplies rate/tenor, so DebtTerms assemble and the DSCR/covenant machinery runs end-to-end —
still never bankable on the placeholder.

Per the spec, debt is sized and the DSCR is measured on the **P90** case (the bankable
constraint). Two debt-service shapes are supported, selected by ``DebtTerms.sculpting_basis``:

- ``"ANNUITY"`` (the default): a level-payment loan; the DSCR is *measured* and drifts with the
  cashflow profile.
- ``"P90_DSCR"``: the schedule is *sculpted* to the P90 CFADS profile — a flat target DSCR ``x``
  is solved so that the present value (at the debt rate) of ``CFADS_y / x`` over the tenor equals
  the principal, and each year's service is ``CFADS_y / x``. When sculpting is impossible (a
  non-positive P90 CFADS year inside the tenor, or a horizon shorter than the tenor) the schedule
  degrades honestly to the annuity with a warning — service is never negative and the recorded
  basis is what actually happened.

WP-UW-03 (D75 to D77) generalises both axes for supplied `LenderTerms` while leaving every legacy
path byte-identical: the case CFADS may be P50, P90 or the contracted LENDER case
(``DebtTerms.cashflow_case``, default P90); a ``"STRAIGHT_LINE"`` profile joins the annuity; the
sculpt is named for the case it follows (``P50_DSCR`` / ``P90_DSCR`` / ``LENDER_DSCR``); and a
``sculpting_target_dscr`` sizes the debt as the smaller of the gearing cap and what the case CFADS
supports at the target. Every unavailable `DscrResult` now carries a `DscrStatus` and a
reader-facing reason, so a missing ratio always says why.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gridq.assumptions import Assumption, AssumptionLibrary, AssumptionStatus
from gridq.schemas.lender_terms import DscrStatus

# Assumption-library keys the flat-key debt reader needs. Gearing exists from gb_v1_2 (a labelled
# placeholder pending Sim's portfolio finance, B001); the flat rate/tenor keys are deliberately
# absent from every release — per-site rate & tenor come from the M9 credit mapping
# (finance/credit.py), not flat library keys. Naming them here (rather than guessing values) is
# how the model stays honest.
GEARING_KEY = ("financing", "debt_gearing_pct")
RATE_KEY = ("financing", "debt_interest_rate_pct")
TENOR_KEY = ("financing", "debt_tenor_years")
COVENANT_KEY = ("dscr_covenants", "min_p90_dscr")
# W8: the per-basis covenant for the CONTRACTED CFADS measurement — absent from current
# releases (the per-basis gate is lender/Sim policy); until it exists the contracted-basis DSCR
# is measured and reported, never gated. The blended P90 covenant remains the formal gate.
CONTRACTED_COVENANT_KEY = ("dscr_covenants", "min_p90_dscr_contracted")

# The two supported debt-service shapes (see module docstring). ANNUITY is the default so any
# DebtTerms constructed without an explicit basis behaves exactly as the historical level-payment
# schedule — the record now names the shape that is actually produced.
SCULPTING_ANNUITY = "ANNUITY"
SCULPTING_P90_DSCR = "P90_DSCR"
# WP-UW-03 (D77): equal-principal amortisation, and a sculpt named for the case it follows.
SCULPTING_STRAIGHT_LINE = "STRAIGHT_LINE"
SCULPTING_P50_DSCR = "P50_DSCR"
SCULPTING_LENDER_DSCR = "LENDER_DSCR"

# The cash-flow case the debt is sized and measured on (`CashflowCase` values). P90 is the default
# so every DebtTerms built before WP-UW-03 measures exactly what it always measured.
CASE_P50 = "P50"
CASE_P90 = "P90"
CASE_LENDER = "LENDER"
_CASES = (CASE_P50, CASE_P90, CASE_LENDER)
#: The one sculpted basis each case may carry: a sculpt follows the series it is measured on.
SCULPTED_BASIS_BY_CASE: dict[str, str] = {
    CASE_P50: SCULPTING_P50_DSCR,
    CASE_P90: SCULPTING_P90_DSCR,
    CASE_LENDER: SCULPTING_LENDER_DSCR,
}
_SCULPTED_BASES = frozenset(SCULPTED_BASIS_BY_CASE.values())
_SCULPTING_BASES = (
    SCULPTING_ANNUITY,
    SCULPTING_STRAIGHT_LINE,
    SCULPTING_P50_DSCR,
    SCULPTING_P90_DSCR,
    SCULPTING_LENDER_DSCR,
)

#: Which bound set the principal (D77): the gearing cap, or the case CFADS at the target DSCR.
SIZED_BY_GEARING = "gearing"
SIZED_BY_DSCR_TARGET = "dscr_target"

#: Where the covenant thresholds came from (D76).
COVENANT_SOURCE_LIBRARY = "assumption_library"
COVENANT_SOURCE_LENDER_TERMS = "lender_terms"

#: The reason on a run that reached the debt step with no terms at all (B001, D75). Reader-facing
#: and rendered verbatim, so it carries no em dash.
NO_DEBT_TERMS_REASON = (
    "No lender terms supplied: there is no debt structure on this run, so there is no debt "
    "service to cover and no DSCR. Supply lender terms (gridq run --lender-terms, or the lender "
    "terms block on the new quote form) to measure one; none is assumed."
)

#: N1 (Tier A) — the basis stamp that rides on `DscrResult.basis`, and from there onto
#: `DscrSummary.basis` and every pack that prints it.
#:
#: The review's Tier A requirement was to stamp PRE-TAX on `DscrSummary` **and** `FinancialCase`.
#: Only the `FinancialCase` half had landed: the long `PRE_TAX_CFADS_BASIS` disclosure reached
#: `FinancialCase.warnings`, but `DscrSummary.basis` still read a bare ``"P90"`` — and the
#: underwriting pack's covenant table (`outputs/underwriting.py`) prints exactly that field, right
#: beside the ratio and the verdict. A lender reads the covenant table first, so the one number
#: that most needs the label was the one carrying none.
#:
#: Why it matters: there is no corporation tax line, tax rate, capital allowance or taxable-profit
#: computation anywhere in the tree. The pre-tax EBITDA series is renamed CFADS at the debt boundary
#: and tested against a lender's POST-tax covenant, so coverage is overstated by an unquantified,
#: BACK-LOADED amount: capital allowances push the charge into the late years, which are also the
#: binding DSCR years. Adding the tax layer is Tier B (it needs the SPV structure, the
#: capital-allowance basis, WDA timing and loss carry-forward treatment) and is NOT done here.
#:
#: Deliberately TERSE, and that is a constraint rather than a preference: `outputs/` interpolates
#: this field mid-sentence ("The minimum {basis} DSCR on the record is …", "Minimum DSCR ({basis}
#: basis)"), so a paragraph here would wreck the prose in files this module does not own. The full
#: explanation travels as the `cashflows.PRE_TAX_CFADS_BASIS` warning on the same case. It also
#: carries no em dash: the rendered pack is swept for them (`tests/test_outputs_no_em_dashes.py`)
#: and this string is record-authored text printed verbatim into that artifact.
DSCR_BASIS_P90_PRE_TAX = "P90 PRE-TAX"
#: WP-UW-03: the same N1 stamp for the other two cases, equally terse and dash-free.
DSCR_BASIS_P50_PRE_TAX = "P50 PRE-TAX"
DSCR_BASIS_LENDER_PRE_TAX = "LENDER CASE PRE-TAX"
_BASIS_BY_CASE: dict[str, str] = {
    CASE_P50: DSCR_BASIS_P50_PRE_TAX,
    CASE_P90: DSCR_BASIS_P90_PRE_TAX,
    CASE_LENDER: DSCR_BASIS_LENDER_PRE_TAX,
}


def dscr_basis_for_case(cashflow_case: str) -> str:
    """The N1 basis stamp for a cash-flow case (``"P90"`` gives the historical stamp)."""
    return _BASIS_BY_CASE[cashflow_case]


@dataclass(frozen=True)
class DebtTerms:
    """A fully-specified debt structure. Constructed only when every input is present.

    ``sculpting_basis`` selects the debt-service shape: ``"ANNUITY"`` (default, level payment),
    ``"STRAIGHT_LINE"`` (equal principal), or the sculpt for the case, ``"P90_DSCR"`` (service
    sculpted to the P90 CFADS profile at a flat DSCR) and its P50 / LENDER siblings.

    ``cashflow_case`` (WP-UW-03) names the CFADS series the debt is sized and measured on; the
    default ``"P90"`` is the historical basis. ``sculpting_target_dscr`` (D77) makes a sculpt size
    the debt as well as shape it: principal = min(capex x gearing, PV(CFADS / target)).
    """

    gearing_pct: float
    annual_rate_pct: float
    tenor_years: int
    sculpting_basis: str = SCULPTING_ANNUITY
    cashflow_case: str = CASE_P90
    sculpting_target_dscr: float | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.gearing_pct <= 100:
            raise ValueError(f"gearing_pct must be in [0, 100], got {self.gearing_pct}")
        if self.annual_rate_pct < 0:
            raise ValueError(f"annual_rate_pct must be non-negative, got {self.annual_rate_pct}")
        if self.tenor_years <= 0:
            raise ValueError(f"tenor_years must be positive, got {self.tenor_years}")
        if self.sculpting_basis not in _SCULPTING_BASES:
            raise ValueError(
                f"sculpting_basis must be one of {_SCULPTING_BASES}, got {self.sculpting_basis!r}"
            )
        if self.cashflow_case not in _CASES:
            raise ValueError(f"cashflow_case must be one of {_CASES}, got {self.cashflow_case!r}")
        if (
            self.sculpting_basis in _SCULPTED_BASES
            and self.sculpting_basis != SCULPTED_BASIS_BY_CASE[self.cashflow_case]
        ):
            raise ValueError(
                f"sculpting_basis {self.sculpting_basis!r} sculpts to a different series than the "
                f"{self.cashflow_case} case it is measured on; use "
                f"{SCULPTED_BASIS_BY_CASE[self.cashflow_case]!r}"
            )
        if self.sculpting_target_dscr is not None:
            if self.sculpting_target_dscr <= 0:
                raise ValueError(
                    f"sculpting_target_dscr must be positive, got {self.sculpting_target_dscr}"
                )
            if self.sculpting_basis not in _SCULPTED_BASES:
                raise ValueError("sculpting_target_dscr applies only to a sculpted basis")

    @property
    def is_sculpted(self) -> bool:
        """True when the requested service is sculpted to the case CFADS."""
        return self.sculpting_basis in _SCULPTED_BASES


@dataclass(frozen=True)
class DscrResult:
    """The DSCR outcome. ``available=False`` means it could not be computed (with reasons).

    When unavailable, ``min_dscr`` is ``None`` and ``by_year`` is empty — the model never
    substitutes a guessed ratio.

    ``sculpting_basis`` records the debt-service shape that was **actually produced** (annuity or
    P90-DSCR sculpt); when a requested sculpt degrades to the annuity fallback this says so.
    ``None`` when no schedule was built at all.
    """

    available: bool
    min_dscr: float | None = None
    by_year: list[float] = field(default_factory=list)
    debt_service_by_year_gbp: list[float] = field(default_factory=list)
    principal_gbp: float | None = None
    #: N1 — which series the ratio was measured on AND on what tax basis. See
    #: :data:`DSCR_BASIS_P90_PRE_TAX`: this is the string the underwriting pack's covenant table
    #: prints, so it carries the PRE-TAX stamp rather than a bare "P90".
    basis: str = DSCR_BASIS_P90_PRE_TAX
    sculpting_basis: str | None = None
    # W8: the CONTRACTED-basis measurement (conservative all-costs CFADS ÷ the same debt
    # service). ``None``/empty = not measured (no contracted revenue, no debt, or no series).
    min_contracted_dscr: float | None = None
    contracted_by_year: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: WP-UW-03 (D75): why the ratio is or is not a number, and the reader-facing reason when it is
    #: not. Every return path in this module sets both, so no caller has to infer them.
    status: DscrStatus | None = None
    unavailable_reason: str | None = None
    #: D77: which bound set ``principal_gbp`` (``"gearing"`` / ``"dscr_target"``); None = no debt.
    sized_by: str | None = None


@dataclass(frozen=True)
class CovenantTiers:
    """The covenant thresholds resolved for one run, and where they came from (D76).

    ``default_dscr`` is the event-of-default threshold, ``lock_up_dscr`` the distribution lock-up.
    ``absent_reason`` says why there is no default threshold, when there is none.
    """

    default_dscr: float | None
    lock_up_dscr: float | None = None
    source: str = COVENANT_SOURCE_LIBRARY
    absent_reason: str | None = None


@dataclass(frozen=True)
class CovenantOutcome:
    """A covenant verdict. ``passes=None`` means it could not be evaluated (DSCR unavailable)."""

    covenant: float | None = None
    min_dscr: float | None = None
    passes: bool | None = None
    warnings: list[str] = field(default_factory=list)
    #: D76 (the tiers half of H-05): the lock-up tier, where the thresholds came from, and why
    #: ``passes`` is None when it is.
    lock_up: float | None = None
    lock_up_passes: bool | None = None
    source: str | None = None
    not_evaluated_reason: str | None = None


# --- assumption access -----------------------------------------------------


def _maybe(assumptions: AssumptionLibrary, section: str, name: str) -> Assumption | None:
    sect = getattr(assumptions, section, None)
    if not isinstance(sect, dict):
        return None
    return sect.get(name)


def _num(a: Assumption, where: str) -> float:
    if isinstance(a.value, bool) or not isinstance(a.value, (int, float)):
        raise TypeError(f"assumption {where} must be a number, got {a.value!r}")
    return float(a.value)


def build_debt_terms(
    assumptions: AssumptionLibrary,
) -> tuple[DebtTerms | None, list[str]]:
    """Build `DebtTerms` from the flat library keys, or ``(None, warnings)`` if insufficient.

    This is the honest gate for the flat-key path: the flat rate/tenor keys are deliberately
    absent from every release (rate/tenor come from the M9 credit mapping — see
    ``build_debt_terms_from_credit`` in finance/credit.py, the live path), so this returns
    ``None`` and a warning naming exactly what is missing. Nothing is assumed.
    """
    warnings: list[str] = []
    gearing = _maybe(assumptions, *GEARING_KEY)
    rate = _maybe(assumptions, *RATE_KEY)
    tenor = _maybe(assumptions, *TENOR_KEY)

    missing = [
        ".".join(key)
        for key, a in ((GEARING_KEY, gearing), (RATE_KEY, rate), (TENOR_KEY, tenor))
        if a is None
    ]
    if missing:
        warnings.append(
            "DSCR unavailable: the assumption library has no debt structure ("
            + ", ".join(missing)
            + "). Per-site rate & tenor come from the M9 credit mapping (unresolved, BLOCKERS "
            "B001); gearing is portfolio finance. Debt is not guessed."
        )
        return None, warnings

    # All present — warn for any non-confirmed status (placeholder-driven debt is not bankable).
    for key, a in ((GEARING_KEY, gearing), (RATE_KEY, rate), (TENOR_KEY, tenor)):
        assert a is not None  # narrowed by the `missing` check above
        if a.status != AssumptionStatus.CONFIRMED:
            warnings.append(
                f"Debt assumption '{'.'.join(key)}' is '{a.status.value}' — debt sizing is "
                "indicative, not bankable, until it is confirmed."
            )

    assert gearing is not None and rate is not None and tenor is not None
    terms = DebtTerms(
        gearing_pct=_num(gearing, ".".join(GEARING_KEY)),
        annual_rate_pct=_num(rate, ".".join(RATE_KEY)),
        tenor_years=int(_num(tenor, ".".join(TENOR_KEY))),
    )
    return terms, warnings


def covenant_value(assumptions: AssumptionLibrary, *, warnings: list[str]) -> float | None:
    """Read the P90-DSCR covenant from the library (it *does* exist in gb_v0_1), or ``None``."""
    a = _maybe(assumptions, *COVENANT_KEY)
    if a is None:
        warnings.append("No 'dscr_covenants.min_p90_dscr' in the assumption library.")
        return None
    if a.status != AssumptionStatus.CONFIRMED:
        warnings.append(
            f"Assumption '{'.'.join(COVENANT_KEY)}' is '{a.status.value}'; the covenant check "
            "is indicative, not bankable, until it is confirmed."
        )
    return _num(a, ".".join(COVENANT_KEY))


# --- schedule + DSCR maths -------------------------------------------------


def level_payment_schedule(
    principal_gbp: float, annual_rate_pct: float, tenor_years: int
) -> list[float]:
    """Constant (annuity) annual debt service over ``tenor_years``.

    Deterministic closed-form annuity payment. Handles the zero-rate case (straight-line). The
    returned list has one entry per debt year (length ``tenor_years``).
    """
    if principal_gbp < 0:
        raise ValueError(f"principal must be non-negative, got {principal_gbp}")
    if tenor_years <= 0:
        raise ValueError(f"tenor_years must be positive, got {tenor_years}")
    if annual_rate_pct < 0:
        raise ValueError(f"annual_rate_pct must be non-negative, got {annual_rate_pct}")
    i = annual_rate_pct / 100.0
    if i == 0:
        payment = principal_gbp / tenor_years
    else:
        factor = (1.0 + i) ** tenor_years
        payment = principal_gbp * i * factor / (factor - 1.0)
    return [round(payment, 2) for _ in range(tenor_years)]


def straight_line_schedule(
    principal_gbp: float, annual_rate_pct: float, tenor_years: int
) -> list[float]:
    """Equal-principal (straight-line) annual debt service over ``tenor_years`` (WP-UW-03).

    Each year repays ``principal / tenor`` plus interest on the balance outstanding at the start of
    that year, so service declines linearly. Deterministic closed form; one entry per debt year.
    """
    if principal_gbp < 0:
        raise ValueError(f"principal must be non-negative, got {principal_gbp}")
    if tenor_years <= 0:
        raise ValueError(f"tenor_years must be positive, got {tenor_years}")
    if annual_rate_pct < 0:
        raise ValueError(f"annual_rate_pct must be non-negative, got {annual_rate_pct}")
    i = annual_rate_pct / 100.0
    repayment = principal_gbp / tenor_years
    return [
        round(repayment + i * (principal_gbp - repayment * year), 2) for year in range(tenor_years)
    ]


def _sculpt_floor_note(window: list[float], case_label: str) -> list[str]:
    """The disclosure for a sculpt floored at zero service in non-positive CFADS years."""
    years = [str(y) for y, c in enumerate(window, start=1) if c <= 0]
    if not years:
        return []
    return [
        f"SCULPT FLOORED (D132): sculpted debt service is set to ZERO in operating year(s) "
        f"{', '.join(years)}, where the {case_label} CFADS is non-positive after the lifecycle "
        "capital flows (decommissioning / replacement in-tenor); the debt is sculpted to the "
        "remaining years. Never a negative service, never a negative principal."
    ]


def _sculpt_window_refusal(
    cfads_by_year: list[float], tenor_years: int, case_label: str, *, floor_negative: bool = False
) -> list[str] | None:
    """The two conditions under which no sculpt exists, as the warning to fall back on, or None.

    ``floor_negative`` (WP-UW-17, D132): a non-positive year is floored at zero service
    instead of refusing the sculpt, so long as at least one year in the window is positive.
    """
    window = cfads_by_year[:tenor_years]
    if len(window) < tenor_years:
        return [
            f"{case_label}-DSCR sculpting unavailable: the projection horizon "
            f"({len(cfads_by_year)} operating years) is shorter than the debt tenor "
            f"({tenor_years}) — falling back to a level-payment (annuity) schedule."
        ]
    if floor_negative and any(c > 0 for c in window):
        return None
    if any(c <= 0 for c in window):
        return [
            f"{case_label}-DSCR sculpting unavailable: at least one {case_label} CFADS year within "
            "the tenor is zero or negative, so no positive sculpted service exists — falling back "
            "to a level-payment (annuity) schedule. Never a negative service, never guessed."
        ]
    return None


def target_sculpted_schedule(
    max_principal_gbp: float,
    annual_rate_pct: float,
    tenor_years: int,
    cfads_by_year: list[float],
    target_dscr: float,
    *,
    case_label: str = CASE_P90,
    floor_negative: bool = False,
) -> tuple[list[float] | None, float | None, str | None, list[str]]:
    """Size AND shape the debt to a sculpting target DSCR, capped at the gearing (D77).

    The standard project-finance sizing rule: the debt the case CFADS supports at the target is
    ``PV(CFADS_y / target)`` at the debt rate over the tenor, and the loan is the smaller of that
    and the gearing cap ``max_principal_gbp``.

    * DSCR-target bound (the CFADS supports less than the cap): principal = PV(CFADS / target) and
      each year's service is ``CFADS_y / target``, so every year's DSCR is the target.
    * Gearing bound: principal = the cap and the flat DSCR is solved exactly as `sculpted_schedule`
      does, so it is at or above the target.

    Returns ``(service, principal, sized_by, warnings)``; ``(None, None, None, warnings)`` when no
    sculpt exists (the caller falls back to the annuity at the gearing cap and says so).
    """
    if max_principal_gbp <= 0:
        raise ValueError(f"max principal must be positive to sculpt, got {max_principal_gbp}")
    if target_dscr <= 0:
        raise ValueError(f"target_dscr must be positive, got {target_dscr}")
    refusal = _sculpt_window_refusal(
        cfads_by_year, tenor_years, case_label, floor_negative=floor_negative
    )
    if refusal is not None:
        return None, None, None, refusal
    raw_window = cfads_by_year[:tenor_years]
    floor_note = _sculpt_floor_note(raw_window, case_label) if floor_negative else []
    window = [max(c, 0.0) for c in raw_window] if floor_note else raw_window
    i = annual_rate_pct / 100.0
    pv_cfads = sum(c / (1.0 + i) ** year for year, c in enumerate(window, start=1))
    supported = round(pv_cfads / target_dscr, 2)
    if supported >= max_principal_gbp:
        flat = pv_cfads / max_principal_gbp
        return (
            [round(c / flat, 2) for c in window],
            max_principal_gbp,
            SIZED_BY_GEARING,
            floor_note,
        )
    return (
        [round(c / target_dscr, 2) for c in window],
        supported,
        SIZED_BY_DSCR_TARGET,
        floor_note
        + [
            f"Debt sized by the sculpting target: the {case_label} CFADS supports "
            f"GBP {supported:,.2f} at a {target_dscr:g}x DSCR, below the gearing cap of "
            f"GBP {max_principal_gbp:,.2f}, so the loan is the smaller figure (D77)."
        ],
    )


def sculpted_schedule(
    principal_gbp: float,
    annual_rate_pct: float,
    tenor_years: int,
    p90_cfads_by_year: list[float],
    *,
    case_label: str = CASE_P90,
    floor_negative: bool = False,
) -> tuple[list[float] | None, float | None, list[str]]:
    """Debt service sculpted to the P90 CFADS profile at a flat target DSCR.

    Deterministic closed form: the flat target DSCR ``x`` is chosen so that the present value at
    the debt rate of ``CFADS_y / x`` over the tenor equals the principal, i.e.
    ``x = PV(CFADS, rate, tenor) / principal``; each year's service is then ``CFADS_y / x``. By
    construction ``PV(service) == principal`` and ``DSCR_y == x`` for every debt year. The
    zero-rate case needs no special handling (the discount factor is 1).

    Degrades honestly — returns ``(None, None, warnings)`` and the caller falls back to the
    annuity — when sculpting is impossible: the horizon covers fewer years than the tenor, or any
    P90 CFADS year within the tenor is non-positive (service is never negative, never guessed).
    """
    if principal_gbp <= 0:
        raise ValueError(f"principal must be positive to sculpt, got {principal_gbp}")
    if tenor_years <= 0:
        raise ValueError(f"tenor_years must be positive, got {tenor_years}")
    if annual_rate_pct < 0:
        raise ValueError(f"annual_rate_pct must be non-negative, got {annual_rate_pct}")

    refusal = _sculpt_window_refusal(
        p90_cfads_by_year, tenor_years, case_label, floor_negative=floor_negative
    )
    if refusal is not None:
        return None, None, refusal
    raw_window = p90_cfads_by_year[:tenor_years]
    floor_note = _sculpt_floor_note(raw_window, case_label) if floor_negative else []
    window = [max(c, 0.0) for c in raw_window] if floor_note else raw_window

    i = annual_rate_pct / 100.0
    pv_cfads = sum(c / (1.0 + i) ** year for year, c in enumerate(window, start=1))
    target_dscr = pv_cfads / principal_gbp
    service = [round(c / target_dscr, 2) for c in window]
    return service, round(target_dscr, 6), floor_note


def dscr_by_year(cfads_by_year: list[float], debt_service_by_year: list[float]) -> list[float]:
    """DSCR = CFADS ÷ debt service, year by year (over the debt years only).

    A year with zero debt service is skipped (no division by zero); the DSCR series covers the
    overlapping years where debt service is positive.
    """
    out: list[float] = []
    for cfads, service in zip(cfads_by_year, debt_service_by_year, strict=False):
        if service > 0:
            out.append(round(cfads / service, 4))
    return out


def size_and_measure_dscr(
    capex_gbp: float,
    cfads_by_year: list[float],
    terms: DebtTerms,
    *,
    idc_accumulation_factor: float = 1.0,
    floor_negative_sculpt: bool = False,
) -> DscrResult:
    """Size the loan, build the debt service, and measure DSCR on the case CFADS.

    ``cfads_by_year`` is the operating free cashflow before debt (years 1..N) of the case named by
    ``terms.cashflow_case`` (P90 unless lender terms say otherwise), i.e. the cash available for
    debt service. DSCR is measured over the debt tenor. The debt-service shape follows
    ``terms.sculpting_basis``: an annuity by default, straight-line, or a sculpt to the case CFADS
    (falling back to the annuity, with a warning, when sculpting is impossible); a sculpting target
    also sizes the principal (D77). The result records the basis that was actually applied, which
    bound sized the debt, and a `DscrStatus` with a reason whenever the ratio is unavailable.

    WP-UW-17 (D130): ``idc_accumulation_factor`` ``A`` grows the gearing cap to the
    principal at COD, ``g x C x A`` — debt drawn pro-rata with construction capex, interest rolled
    up to the actual COD. ``A = 1.0`` (the default; no programme, no delay) is the legacy sizing,
    bit-identical (``x * 1.0 == x``). ``floor_negative_sculpt`` floors a sculpt's service at zero in
    a non-positive CFADS year (lifecycle flows in-tenor) instead of refusing it (D132).
    """
    if capex_gbp < 0:
        raise ValueError(f"capex must be non-negative, got {capex_gbp}")
    if idc_accumulation_factor < 1.0:
        raise ValueError(f"idc_accumulation_factor must be >= 1, got {idc_accumulation_factor}")
    case = terms.cashflow_case
    basis = dscr_basis_for_case(case)
    principal = round(capex_gbp * terms.gearing_pct / 100.0 * idc_accumulation_factor, 2)
    warnings: list[str] = []

    if principal == 0:
        warnings.append("Gearing is 0% — the project is all-equity; DSCR is not meaningful.")
        return DscrResult(
            available=False,
            principal_gbp=0.0,
            basis=basis,
            warnings=warnings,
            status=DscrStatus.ALL_EQUITY,
            unavailable_reason=(
                "The debt terms resolve to zero debt (0% gearing): the project is all-equity, so "
                "there is no debt service to cover and no DSCR."
            ),
        )

    applied_basis = SCULPTING_ANNUITY
    sized_by = SIZED_BY_GEARING
    service: list[float] | None = None
    if terms.is_sculpted and terms.sculpting_target_dscr is not None:
        service, sized_principal, bound, sculpt_warnings = target_sculpted_schedule(
            principal,
            terms.annual_rate_pct,
            terms.tenor_years,
            cfads_by_year,
            terms.sculpting_target_dscr,
            case_label=case,
            floor_negative=floor_negative_sculpt,
        )
        warnings.extend(sculpt_warnings)
        if service is not None and sized_principal is not None and bound is not None:
            applied_basis = terms.sculpting_basis
            principal = sized_principal
            sized_by = bound
    elif terms.is_sculpted:
        service, _target, sculpt_warnings = sculpted_schedule(
            principal,
            terms.annual_rate_pct,
            terms.tenor_years,
            cfads_by_year,
            case_label=case,
            floor_negative=floor_negative_sculpt,
        )
        warnings.extend(sculpt_warnings)
        if service is not None:
            applied_basis = terms.sculpting_basis
    elif terms.sculpting_basis == SCULPTING_STRAIGHT_LINE:
        service = straight_line_schedule(principal, terms.annual_rate_pct, terms.tenor_years)
        applied_basis = SCULPTING_STRAIGHT_LINE
    if service is None:
        service = level_payment_schedule(principal, terms.annual_rate_pct, terms.tenor_years)

    cfads_window = cfads_by_year[: terms.tenor_years]
    if len(cfads_window) < terms.tenor_years:
        # N10 (2026-08-10 review): a partial window is NOT a measurable min DSCR. Revenue fades
        # with capacity while costs are held flat, so P90 CFADS falls monotonically and the years
        # the truncation drops are always the WORST ones — precisely the binding years the
        # covenant exists to catch. Returning `available=True` on that window let a covenant the
        # project fails report as passed, and `SizingObjective.feasible` — the hard gate between
        # a sub-covenant spec and a recommendation — admitted it. `sculpted_schedule` already
        # refuses this exact condition; the measurement now refuses it too. Unavailable, never a
        # number measured over the good years only.
        warnings.append(
            f"Min DSCR unavailable: the projection horizon ({len(cfads_by_year)} operating "
            f"years) is shorter than the debt tenor ({terms.tenor_years}), and the unmeasured "
            f"late years are the binding ones ({case} CFADS fades against flat costs) — a minimum "
            "over the measured years only would overstate the covenant headroom. Extend the "
            "horizon to at least the tenor to measure it."
        )
        return DscrResult(
            available=False,
            debt_service_by_year_gbp=service,
            principal_gbp=principal,
            basis=basis,
            sculpting_basis=applied_basis,
            warnings=warnings,
            status=DscrStatus.TENOR_BEYOND_HORIZON,
            unavailable_reason=(
                f"The debt tenor ({terms.tenor_years} years) runs past the projection horizon "
                f"({len(cfads_by_year)} operating years). The unmeasured late years are the "
                "binding ones, so a minimum over the measured years would overstate the covenant "
                "headroom: extend the horizon to at least the tenor to measure it."
            ),
            sized_by=sized_by,
        )
    # D51 REVISED (Olly, 2026-08-18). The first version of this guard REFUSED the ratio outright
    # when every measured year was non-positive. That was wrong, and the repo already had the
    # invariant that says why: `test_a_measured_covenant_failure_is_never_relabelled_as_a_financing
    # _absence`, plus `test_covenant_gate_rejects_on_zero_revenue_with_debt`, whose docstring is
    # explicit that zero revenue with injected debt SHOULD produce a negative DSCR and reject every
    # candidate. A project whose CFADS is negative genuinely cannot service its debt: that is a
    # MEASURED coverage failure, and refusing to report it relabels an economic result as a data
    # absence, which is the exact defect this codebase fixed once already.
    #
    # The real hazard the original guard was aiming at is narrower, and a WARNING is the right tool
    # for it: a reader must not take "the covenant gate rejected every candidate" as evidence that
    # the LOAN was mis-sized, when the cause is that there is no cash at any gearing. So the verdict
    # stands and the cause is stated.
    series = dscr_by_year(cfads_window, service)
    if not series:
        warnings.append("No overlapping debt-service years — DSCR could not be measured.")
        return DscrResult(
            available=False,
            debt_service_by_year_gbp=service,
            principal_gbp=principal,
            basis=basis,
            sculpting_basis=applied_basis,
            warnings=warnings,
            status=DscrStatus.NO_SERVICE_YEARS,
            unavailable_reason=(
                "A debt schedule was built but no year within the tenor carries positive debt "
                "service, so there is no ratio to measure."
            ),
            sized_by=sized_by,
        )
    # A year whose service was floored at zero (WP-UW-17 sculpt floor) carries no ratio, so it is
    # not a negative-DSCR year; on every other path each tenor year carries positive service.
    serviced = [c for c, sv in zip(cfads_window, service, strict=False) if sv > 0]
    if any(c <= 0.0 for c in serviced):
        # Mixed series: the verdict stands (a year that cannot service debt is a real breach), but
        # the reader must know the breach is a CASH ABSENCE in those years, not a sizing error.
        negative_years = sum(1 for c in serviced if c <= 0.0)
        total_years = len(cfads_window)
        if negative_years == total_years:
            warnings.append(
                f"DSCR is negative in EVERY one of the {total_years} measured year(s): {case} "
                "CFADS is non-positive throughout, so there is no cash to cover debt service AT "
                "ANY GEARING. The covenant failure below is real and measured, but do NOT read it "
                "as evidence the loan is mis-sized or that a different structure would pass - "
                "re-gearing cannot create cash. The binding constraint is the project's cashflow, "
                "not its financing."
            )
        else:
            warnings.append(
                f"DSCR is negative in {negative_years} of {total_years} measured year(s) because "
                f"{case} CFADS itself is non-positive there, not because the loan is mis-sized. "
                "The covenant verdict stands, and the binding constraint is the absent cash in "
                "those years."
            )
    return DscrResult(
        available=True,
        min_dscr=round(min(series), 4),
        by_year=series,
        debt_service_by_year_gbp=service,
        principal_gbp=principal,
        basis=basis,
        sculpting_basis=applied_basis,
        warnings=warnings,
        status=DscrStatus.MEASURED,
        sized_by=sized_by,
    )


def check_covenant(
    min_dscr: float | None,
    covenant: float | None,
    *,
    lock_up: float | None = None,
    source: str | None = None,
    absent_reason: str | None = None,
) -> CovenantOutcome:
    """Evaluate the covenant. If either side is missing, the verdict is ``None`` (unavailable).

    ``lock_up`` (D76) is the distribution lock-up tier, tested on the same minimum DSCR and reported
    alongside; ``passes`` stays the default-tier verdict. ``absent_reason`` explains a missing
    default threshold (for example, lender terms that state no covenant) and becomes the
    ``not_evaluated_reason`` in place of the generic one.
    """
    lock_up_passes = None if min_dscr is None or lock_up is None else min_dscr >= lock_up
    if min_dscr is None or covenant is None:
        if min_dscr is None:
            reason = "Not evaluated: there is no measured DSCR to test."
        else:
            reason = absent_reason or "Not evaluated: no covenant threshold is set."
        return CovenantOutcome(
            covenant=covenant,
            min_dscr=min_dscr,
            passes=None,
            warnings=["Covenant not evaluated: DSCR or covenant value unavailable."],
            lock_up=lock_up,
            lock_up_passes=lock_up_passes,
            source=source,
            not_evaluated_reason=reason,
        )
    return CovenantOutcome(
        covenant=covenant,
        min_dscr=min_dscr,
        passes=min_dscr >= covenant,
        lock_up=lock_up,
        lock_up_passes=lock_up_passes,
        source=source,
    )


def library_covenant_tiers(assumptions: AssumptionLibrary, *, warnings: list[str]) -> CovenantTiers:
    """The library covenant as `CovenantTiers` (the legacy single-key path, unchanged in value)."""
    value = covenant_value(assumptions, warnings=warnings)
    return CovenantTiers(
        default_dscr=value,
        source=COVENANT_SOURCE_LIBRARY,
        absent_reason=(
            None
            if value is not None
            else "Not evaluated: the assumption library has no dscr_covenants.min_p90_dscr."
        ),
    )


def assess_debt(
    capex_gbp: float,
    cfads_by_year: list[float],
    assumptions: AssumptionLibrary,
    *,
    debt_terms: DebtTerms | None = None,
    p90_contracted_cfads_by_year: list[float] | None = None,
    covenant_tiers: CovenantTiers | None = None,
    no_terms_reason: str = NO_DEBT_TERMS_REASON,
    idc_accumulation_factor: float = 1.0,
    floor_negative_sculpt: bool = False,
) -> tuple[DscrResult, CovenantOutcome]:
    """End-to-end debt assessment used by the financial model.

    Resolves terms from the library (or accepts injected ``debt_terms``), sizes the loan, measures
    DSCR on the case CFADS (P90 unless the terms name another case), and checks the covenant. When
    terms are unavailable, returns an unavailable `DscrResult` (status ``no_debt_terms``, reason
    ``no_terms_reason``) and an unevaluated covenant, both carrying the explanatory warnings.

    ``covenant_tiers`` (D76) are the thresholds to test. ``None`` keeps the historical behaviour:
    the library's ``dscr_covenants.min_p90_dscr``. Supplied lender terms pass their own tiers, and
    when those terms state no covenant the verdict is *not evaluated*; the library placeholder is
    never substituted for a lender's silence.

    ``p90_contracted_cfads_by_year`` (W8): the conservative contracted-basis series — when
    supplied alongside real debt terms, the contracted-basis DSCR is MEASURED against the same
    debt service and reported (``min_contracted_dscr``). It is **gated only informationally**:
    ``dscr_covenants.min_p90_dscr_contracted`` (absent from current releases) yields a
    meets/breaches note when present; the blended P90 covenant remains the formal gate until
    the lender/Sim debt policy defines per-basis gating.
    """
    warnings: list[str] = []
    tiers = (
        covenant_tiers
        if covenant_tiers is not None
        else library_covenant_tiers(assumptions, warnings=warnings)
    )

    def _verdict(min_dscr: float | None) -> CovenantOutcome:
        return check_covenant(
            min_dscr,
            tiers.default_dscr,
            lock_up=tiers.lock_up_dscr,
            source=tiers.source,
            absent_reason=tiers.absent_reason,
        )

    if debt_terms is None:
        debt_terms, term_warnings = build_debt_terms(assumptions)
        warnings.extend(term_warnings)

    if debt_terms is None:
        dscr = DscrResult(
            available=False,
            warnings=warnings,
            status=DscrStatus.NO_DEBT_TERMS,
            unavailable_reason=no_terms_reason,
        )
        return dscr, _verdict(None)

    dscr = size_and_measure_dscr(
        capex_gbp,
        cfads_by_year,
        debt_terms,
        idc_accumulation_factor=idc_accumulation_factor,
        floor_negative_sculpt=floor_negative_sculpt,
    )

    min_contracted: float | None = None
    contracted_by_year: list[float] = []
    if p90_contracted_cfads_by_year is not None and dscr.available:
        contracted_by_year = dscr_by_year(
            p90_contracted_cfads_by_year[: debt_terms.tenor_years],
            dscr.debt_service_by_year_gbp,
        )
        if contracted_by_year:
            min_contracted = round(min(contracted_by_year), 4)
            contracted_cov = _maybe(assumptions, *CONTRACTED_COVENANT_KEY)
            if contracted_cov is None:
                warnings.append(
                    f"Contracted-basis min P90 DSCR measured at {min_contracted} (conservative: "
                    "ALL operating costs borne by the contracted revenue — no cost allocation "
                    "invented) but NOT gated: dscr_covenants.min_p90_dscr_contracted is absent — "
                    "the per-basis covenant is lender/Sim debt policy for a future release. The "
                    "blended P90 covenant remains the formal gate."
                )
            else:
                cov_val = _num(contracted_cov, ".".join(CONTRACTED_COVENANT_KEY))
                verdict = "meets" if min_contracted >= cov_val else "BREACHES"
                warnings.append(
                    f"Contracted-basis min P90 DSCR {min_contracted} {verdict} the informational "
                    f"per-basis covenant {cov_val} "
                    f"(dscr_covenants.min_p90_dscr_contracted, status "
                    f"'{contracted_cov.status.value}') — measured on the conservative all-costs "
                    "contracted CFADS; the blended P90 covenant remains the formal gate until "
                    "the debt policy defines per-basis gating."
                )

    dscr = DscrResult(
        available=dscr.available,
        min_dscr=dscr.min_dscr,
        by_year=dscr.by_year,
        debt_service_by_year_gbp=dscr.debt_service_by_year_gbp,
        principal_gbp=dscr.principal_gbp,
        basis=dscr.basis,
        # Merge note (north-star): the applied sculpting basis MUST survive this rebuild —
        # dropping it would let a record claim a P90-DSCR sculpt that degraded to annuity.
        sculpting_basis=dscr.sculpting_basis,
        min_contracted_dscr=min_contracted,
        contracted_by_year=contracted_by_year,
        warnings=list(dict.fromkeys([*warnings, *dscr.warnings])),
        # WP-UW-03: the status, reason and sizing bound must survive this rebuild for the same
        # reason the sculpting basis must.
        status=dscr.status,
        unavailable_reason=dscr.unavailable_reason,
        sized_by=dscr.sized_by,
    )
    return dscr, _verdict(dscr.min_dscr)
