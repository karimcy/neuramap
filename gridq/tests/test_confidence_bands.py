"""Confidence bands replace the withdrawn 20% IRR target.

`docs/validation_requirements.md` §2. A band expresses **evidential strength**, is assigned from
evidence rather than chosen, and is **capped by the weakest input the result depends on**. It is
not a probability, not P50/P90, and not a recommendation.

These tests also pin the fix for hostile-review F-03: the data tier used to be a label that changed
no number and blocked nothing. It now sets a hard ceiling.
"""

from __future__ import annotations

import pytest

from gridq.ingestion.hh_mandate import HalfHourlyAdmission, HalfHourlyBasis
from gridq.quotes.confidence import ConfidenceBand, assess_confidence
from gridq.schemas import DataTier

METERED = HalfHourlyAdmission(basis=HalfHourlyBasis.METERED, file_ref="real.csv")
MODELLED = HalfHourlyAdmission(
    basis=HalfHourlyBasis.MODELLED,
    file_ref="generated.csv",
    scaled_to="12 monthly invoice totals, 2025",
    reference_profile="waste_mrf (archetype_pending_source)",
)


def _reconciled_bill():
    """A Gate 3b PASS, produced by RUNNING the real gate on figures that agree.

    Built rather than hand-rolled so a test can never assert a pass the gate itself would not give.

    Why it is needed at all: Gate 3b caps an un-run run at MEDIUM, exactly as Gate 3 does for
    ``reconciles=None``. Never having compared the modelled delivered cost to a real invoice is not
    evidence that it is right, and the delivered cost is what every avoided-cost revenue line is a
    fraction of. So a HIGH band now requires this alongside ``reconciles=True`` — the two gates are
    symmetric, and "everything strong" has to include the bill.
    """
    from gridq.ingestion.reconciliation import InvoiceEvidence, reconcile_bill_against_invoice

    evidence = InvoiceEvidence(
        site_id="site_test",
        invoice_available=True,
        period="calendar 2025",
        invoiced_kwh=1_000_000.0,
        invoiced_cost_gbp=200_000.0,
        source="test-fixture",
    )
    return reconcile_bill_against_invoice(
        evidence,
        modelled_kwh=1_000_000.0,
        modelled_cost_gbp=200_000.0,
        modelled_fixed_gbp=0.0,
        modelled_monthly_cost_gbp=None,
    )


RECONCILED_BILL = _reconciled_bill()


def _strong_site(make_site):
    """A site with every evidential box ticked, so individual factors can be knocked down."""
    site = make_site().model_copy(deep=True)
    site.data_tier = DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY
    site.tariff_stack = site.tariff_stack.model_copy(
        update={
            "import_rates": {"red": 0.42, "amber": 0.21, "green": 0.14},
            "duos_red_rate": 0.21,
            "duos_amber_rate": 0.03,
            "duos_green_rate": 0.006,
            "levies": {"RO": 0.025, "CfD": 0.008},
        }
    )
    return site


class _Quote:
    """Minimal stand-in carrying only what the assessment reads."""

    def __init__(self, *, unconfirmed=False, streams=()):
        self.uses_unconfirmed_assumptions = unconfirmed
        self.value_streams = list(streams)


class _Stream:
    def __init__(self, firmness, revenue):
        self.firmness = firmness
        self.annual_revenue_gbp = revenue


def _contracted_quote():
    from gridq.schemas import Firmness

    return _Quote(
        streams=[_Stream(Firmness.CONTRACTED, 50_000.0), _Stream(Firmness.FIRM, 20_000.0)]
    )


# --- the cap is the point --------------------------------------------------------------------


def test_everything_strong_reaches_high(make_site) -> None:
    a = assess_confidence(
        _strong_site(make_site),
        _contracted_quote(),
        admission=METERED,
        reconciles=True,
        bill_reconciliation=RECONCILED_BILL,
    )
    assert a.band is ConfidenceBand.HIGH


def test_modelled_load_caps_at_medium_however_good_everything_else_is(make_site) -> None:
    """The headline rule: a High band is impossible on modelled load data."""
    a = assess_confidence(
        _strong_site(make_site), _contracted_quote(), admission=MODELLED, reconciles=True
    )
    assert a.band is ConfidenceBand.MEDIUM
    assert any(f.name == "half-hourly basis" for f in a.binding_factors)


def test_a_bill_never_compared_to_an_invoice_cannot_reach_high(make_site) -> None:
    """Gate 3b, symmetric with Gate 3: not having checked is not evidence of agreement.

    Gate 3 proves the modelled LOAD matches the measured series. It says nothing about whether the
    tariff stack turns that load into the right number of pounds, and the delivered cost is the base
    every avoided-cost revenue line is a fraction of. So a run whose bill has never met an invoice
    is capped at MEDIUM even with every other box ticked.
    """
    a = assess_confidence(
        _strong_site(make_site), _contracted_quote(), admission=METERED, reconciles=True
    )
    assert a.band is ConfidenceBand.MEDIUM
    assert any(f.name == "bill reconciliation" for f in a.binding_factors)


def test_a_failed_reconciliation_drops_to_low(make_site) -> None:
    a = assess_confidence(
        _strong_site(make_site), _contracted_quote(), admission=METERED, reconciles=False
    )
    assert a.band is ConfidenceBand.LOW
    assert any(f.name == "load reconciliation" for f in a.binding_factors)


def test_placeholder_assumptions_drop_to_low(make_site) -> None:
    from gridq.schemas import Firmness

    quote = _Quote(unconfirmed=True, streams=[_Stream(Firmness.CONTRACTED, 50_000.0)])
    a = assess_confidence(_strong_site(make_site), quote, admission=METERED, reconciles=True)
    assert a.band is ConfidenceBand.LOW
    assert any(f.name == "assumptions" for f in a.binding_factors)


# --- F-03: the data tier is finally load-bearing ---------------------------------------------


@pytest.mark.parametrize(
    ("tier", "expected"),
    [
        (DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY, ConfidenceBand.HIGH),
        (DataTier.T1_PARTIAL_HALF_HOURLY, ConfidenceBand.MEDIUM),
        (DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE, ConfidenceBand.LOW),
    ],
)
def test_data_tier_sets_a_hard_ceiling(make_site, tier, expected) -> None:
    """F-03: before this, T0/T1/T2 changed no number and blocked nothing."""
    site = _strong_site(make_site)
    site.data_tier = tier
    a = assess_confidence(
        site,
        _contracted_quote(),
        admission=METERED,
        reconciles=True,
        bill_reconciliation=RECONCILED_BILL,
    )
    assert a.band is expected


# --- tariff completeness ---------------------------------------------------------------------


def test_commodity_only_tariff_caps_at_medium(make_site) -> None:
    """Most BTM value sits in the non-commodity stack; without it, avoided cost is understated."""
    site = _strong_site(make_site)
    site.tariff_stack = site.tariff_stack.model_copy(
        update={
            "duos_red_rate": None,
            "duos_amber_rate": None,
            "duos_green_rate": None,
            "levies": {},
        }
    )
    a = assess_confidence(site, _contracted_quote(), admission=METERED, reconciles=True)
    assert a.band is ConfidenceBand.MEDIUM
    assert any(f.name == "tariff" for f in a.binding_factors)


def test_no_tariff_at_all_is_low(make_site) -> None:
    """The Harwell case: excellent metered load, no tariff of any kind."""
    site = _strong_site(make_site)
    site.tariff_stack = site.tariff_stack.model_copy(update={"import_rates": {}})
    a = assess_confidence(site, _contracted_quote(), admission=METERED, reconciles=True)
    assert a.band is ConfidenceBand.LOW


# --- what a band is not ----------------------------------------------------------------------


def test_a_band_reports_what_is_holding_it_down(make_site) -> None:
    """The useful question is 'what do I fix to raise it', so binding factors must be named."""
    a = assess_confidence(
        _strong_site(make_site), _contracted_quote(), admission=MODELLED, reconciles=True
    )
    assert a.binding_factors
    explanation = a.explain()
    assert "MEDIUM" in explanation
    assert "MODELLED" in explanation
    assert "not a probability" in explanation


def test_a_high_confidence_poor_result_is_still_high_confidence(make_site) -> None:
    """A band is evidential strength, not a verdict on the economics.

    A well-evidenced negative result is a SUCCESSFUL validation — the band must not fall just
    because the answer is unwelcome.
    """
    from gridq.schemas import Firmness

    poor = _Quote(streams=[_Stream(Firmness.CONTRACTED, 1.0), _Stream(Firmness.FIRM, 0.5)])
    a = assess_confidence(
        _strong_site(make_site),
        poor,
        admission=METERED,
        reconciles=True,
        bill_reconciliation=RECONCILED_BILL,
    )
    assert a.band is ConfidenceBand.HIGH


def test_weakest_picks_the_minimum() -> None:
    assert ConfidenceBand.weakest([ConfidenceBand.HIGH, ConfidenceBand.LOW]) is ConfidenceBand.LOW
    assert ConfidenceBand.weakest([ConfidenceBand.HIGH]) is ConfidenceBand.HIGH
    assert ConfidenceBand.weakest([]) is ConfidenceBand.LOW


def test_merchant_heavy_floor_lowers_the_band(make_site) -> None:
    from gridq.schemas import Firmness

    merchant = _Quote(streams=[_Stream(Firmness.FIRM, 100_000.0)])
    a = assess_confidence(_strong_site(make_site), merchant, admission=METERED, reconciles=True)
    assert a.band is ConfidenceBand.LOW
    assert any(f.name == "floor composition" for f in a.binding_factors)
