"""LenderTerms: the typed lender-terms input object (WP-UW-03, D75).

Before this module a run could only reach a DSCR through two routes, and neither carried real
terms: the M9 credit mapping (``finance/credit.py``), which needs a counterparty credit band that
no real site has (every site is ``credit_status: unknown``), and a bare ``DebtTerms`` dataclass that
only the demo and tests construct. So the DSCR was ``None`` on every real run (BLOCKERS B001), and
nothing on the record said whose terms would have to be supplied for it to exist.

`LenderTerms` is that input, stated the way a term sheet states it:

* ``tenor_years``, ``interest_rate_pct`` (all-in, percent) and ``gearing_pct`` (the maximum debt
  as a percentage of capex, percent);
* ``amortisation``: annuity, straight-line, or sculpted to the chosen cash-flow case, with an
  optional ``sculpting_target_dscr`` that sizes the debt as well as shaping it (D77);
* ``cashflow_case``: which series the lender sizes and tests on (P50, P90, or the contracted
  lender case);
* ``covenant``: the default (event-of-default) and lock-up DSCR thresholds, either or both (D76);
* ``calculation_period``: the test period the covenant is measured over;
* ``provenance``: where the figures came from. ``synthetic`` is a first-class source so a test or
  a demonstration can never be mistaken for a lender's offer (the record says so wherever the DSCR
  is shown).

**Nothing defaults to a figure.** Rate, tenor, gearing, amortisation, case and period are all
required: a model that filled any of them in would be inventing a lender. The covenant is optional
because a term sheet may genuinely state none, and then the covenant verdict is *not evaluated*,
never tested against the library placeholder (D76).

The model is pure data and frozen, like `DerivedAssumption`: a validated lender-terms record is part
of the run's identity, so it must not change after construction. The sizing and measurement logic
lives in ``gridq.finance.lender_terms`` and ``gridq.finance.debt``.

`DscrStatus` lives here too. It is the closed vocabulary for *why* a DSCR is or is not on the
record, and is persisted on `DscrSummary.status` so no reader has to infer the reason from a
``None``.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import ConfigDict, Field, model_validator

from gridq.schemas.provenance import GridQModel

__all__ = [
    "AmortisationProfile",
    "CalculationPeriod",
    "CashflowCase",
    "DscrCovenantTerms",
    "DscrStatus",
    "LenderTerms",
    "LenderTermsProvenance",
    "LenderTermsSource",
]


class CashflowCase(StrEnum):
    """The cash-flow series a lender sizes the debt on and tests the covenant against."""

    #: The P50 operating free cashflow before debt (the equity base case).
    P50 = "P50"
    #: The P90 operating free cashflow before debt (the historical GridQ DSCR basis).
    P90 = "P90"
    #: The contracted P90 case: contracted P90 revenue bearing EVERY operating cost (W8). The
    #: conservative series a lender lending against contracted revenue only would size on.
    LENDER = "LENDER"


class AmortisationProfile(StrEnum):
    """The debt-service shape."""

    #: Level payment (interest + principal constant each year).
    ANNUITY = "annuity"
    #: Equal principal each year, interest on the outstanding balance (declining service).
    STRAIGHT_LINE = "straight_line"
    #: Service sculpted to the chosen case's CFADS at a flat DSCR.
    SCULPTED = "sculpted"


class CalculationPeriod(StrEnum):
    """The period the covenant DSCR is tested over."""

    ANNUAL = "annual"
    SEMI_ANNUAL = "semi_annual"
    QUARTERLY = "quarterly"


class LenderTermsSource(StrEnum):
    """Where the lender terms came from. Only a term sheet can support a bankable verdict."""

    #: An executed or issued term sheet / facility agreement.
    TERM_SHEET = "term_sheet"
    #: An indicative lender quote (email, call note): real, but not committed.
    LENDER_INDICATION = "lender_indication"
    #: Made up for a test or a demonstration. Never a lender's offer, and labelled as such.
    SYNTHETIC = "synthetic"


class DscrStatus(StrEnum):
    """Why the DSCR on a record is, or is not, a number. Persisted on `DscrSummary.status`."""

    #: A DSCR was measured over the full tenor against stated debt terms.
    MEASURED = "measured"
    #: No debt terms reached the run (no lender terms supplied, no usable credit mapping).
    NO_DEBT_TERMS = "no_debt_terms"
    #: The terms resolve to zero debt (0% gearing, or a DSCR target that sizes no debt).
    ALL_EQUITY = "all_equity"
    #: The debt tenor runs past the projection horizon, so the binding late years are unmeasured.
    TENOR_BEYOND_HORIZON = "tenor_beyond_horizon"
    #: The terms test the covenant on a period the annual model does not compute.
    PERIOD_NOT_MODELLED = "period_not_modelled"
    #: A schedule was built but no year carries positive debt service to divide by.
    NO_SERVICE_YEARS = "no_service_years"


_FROZEN = ConfigDict(extra="forbid", frozen=True)


def _reject_fraction(name: str, value: float | None) -> None:
    """The F-52 percent-not-fraction guard, applied to a lender-terms percent field.

    Same rule as `DebtAssumptions._percent_not_fraction`: no real loan has a gearing or an all-in
    rate strictly between 0% and 1%, so a value there is a fraction typed into a percent field,
    which would size the debt 100 times too small or price it 100 times too cheaply.
    """
    if value is not None and 0.0 < value < 1.0:
        raise ValueError(
            f"{name}={value} looks like a FRACTION written into a percent field. Lender-term "
            f"percentages are percent: {value * 100:g}% is '{name}={value * 100:g}'. A fraction "
            "here mis-sizes or mis-prices the debt by a factor of 100."
        )


class LenderTermsProvenance(GridQModel):
    """Who stated these terms, and on what. Travels with the terms onto the record."""

    model_config = _FROZEN

    source: LenderTermsSource
    reference: str = Field(
        min_length=1,
        description="Term-sheet id, lender and date, or for synthetic terms what they are for.",
    )
    as_of: date | None = Field(default=None, description="Date the terms were stated, if known.")
    evidence_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 of the source document, when one is held.",
    )


class DscrCovenantTerms(GridQModel):
    """The DSCR thresholds a lender tests (H-05: one key could not express the tiers).

    ``default_dscr`` is the event-of-default threshold; ``lock_up_dscr`` the distribution lock-up,
    which sits at or above it. Either may be stated alone, but at least one must be: an empty
    covenant block is not a covenant, and the record would claim a test that was never set.
    """

    model_config = _FROZEN

    default_dscr: float | None = Field(default=None, gt=0)
    lock_up_dscr: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _tiers_coherent(self) -> DscrCovenantTerms:
        if self.default_dscr is None and self.lock_up_dscr is None:
            raise ValueError(
                "a covenant block must state default_dscr, lock_up_dscr or both; omit the block "
                "when the terms state no DSCR covenant"
            )
        if (
            self.default_dscr is not None
            and self.lock_up_dscr is not None
            and self.lock_up_dscr < self.default_dscr
        ):
            raise ValueError(
                f"lock_up_dscr ({self.lock_up_dscr}) is below default_dscr ({self.default_dscr}); "
                "a lock-up triggers before default, so it cannot be the lower threshold"
            )
        return self


class LenderTerms(GridQModel):
    """A lender's debt terms for one site, as stated. Every figure is required: none is defaulted.

    See the module docstring. ``gearing_pct`` is the maximum debt as a percentage of capex; with a
    sculpted profile and a ``sculpting_target_dscr`` the debt is the smaller of that cap and what
    the case CFADS supports at the target (D77).
    """

    model_config = _FROZEN

    provenance: LenderTermsProvenance
    tenor_years: int = Field(gt=0, description="Debt tenor in whole years.")
    interest_rate_pct: float = Field(ge=0, description="All-in annual interest rate, percent.")
    gearing_pct: float = Field(
        gt=0, le=100, description="Maximum debt as a percentage of capex, percent."
    )
    amortisation: AmortisationProfile
    cashflow_case: CashflowCase
    calculation_period: CalculationPeriod
    sculpting_target_dscr: float | None = Field(
        default=None,
        gt=0,
        description="Flat DSCR the sculpted service is sized to. Sculpted amortisation only.",
    )
    covenant: DscrCovenantTerms | None = Field(
        default=None,
        description="Default and lock-up DSCR thresholds. None when the terms state no covenant.",
    )

    @model_validator(mode="after")
    def _coherent(self) -> LenderTerms:
        _reject_fraction("interest_rate_pct", self.interest_rate_pct)
        _reject_fraction("gearing_pct", self.gearing_pct)
        if (
            self.sculpting_target_dscr is not None
            and self.amortisation is not AmortisationProfile.SCULPTED
        ):
            raise ValueError(
                "sculpting_target_dscr applies only to amortisation='sculpted'; an "
                f"{self.amortisation.value} schedule has no sculpting target"
            )
        return self

    @property
    def is_synthetic(self) -> bool:
        """True when these terms were made up for a test or demonstration."""
        return self.provenance.source is LenderTermsSource.SYNTHETIC

    def label(self) -> str:
        """One terse, dash-free line naming the terms and their source (rendered verbatim)."""
        parts = [
            f"{self.tenor_years}y",
            f"{self.interest_rate_pct:g}%",
            f"{self.gearing_pct:g}% max gearing",
            self.amortisation.value.replace("_", " "),
            f"{self.cashflow_case.value} case",
        ]
        if self.sculpting_target_dscr is not None:
            parts.append(f"target DSCR {self.sculpting_target_dscr:g}x")
        source = self.provenance.source.value.replace("_", " ")
        return f"{', '.join(parts)} ({source}: {self.provenance.reference})"
