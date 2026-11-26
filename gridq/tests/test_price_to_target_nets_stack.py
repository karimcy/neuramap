"""N3 — the price-to-target must not charge the customer for capex the value stack already funds."""

import pytest

from gridq.finance.pricing import annual_revenue_requirement


def test_booked_revenue_is_netted_off_the_customer_requirement():
    gross = annual_revenue_requirement(500_000.0, 10_000.0, 12.0, 15)
    netted = annual_revenue_requirement(500_000.0, 10_000.0, 12.0, 15, 30_000.0)
    assert netted == pytest.approx(gross - 30_000.0, abs=0.01)


def test_default_is_the_old_conservative_behaviour():
    assert annual_revenue_requirement(500_000.0, 10_000.0, 12.0, 15) == annual_revenue_requirement(
        500_000.0, 10_000.0, 12.0, 15, 0.0
    )


def test_requirement_floors_at_zero_never_negative():
    """Where the stack alone clears the target, the customer contributes nothing — not less."""
    assert annual_revenue_requirement(100_000.0, 1_000.0, 12.0, 15, 10_000_000.0) == 0.0


def test_negative_revenue_is_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        annual_revenue_requirement(500_000.0, 10_000.0, 12.0, 15, -1.0)


def test_the_viability_flip_is_material():
    """The defect ran in the damaging direction: viable sites read as unviable.

    Figures representative of the Grundon/Harwell sites — a ~£500k asset delivering ~400 MWh/yr to
    a customer whose blended delivered import cost is ~19p/kWh.
    """
    capex, opex, energy = 500_000.0, 10_000.0, 400_000.0
    counterfactual = 0.19  # the customer's current blended delivered cost, £/kWh

    gross_price = annual_revenue_requirement(capex, opex, 12.0, 15) / energy
    net_price = annual_revenue_requirement(capex, opex, 12.0, 15, 45_000.0) / energy

    assert gross_price > counterfactual, "fixture must show the spurious 'not viable' verdict"
    assert net_price < counterfactual, "netting the stack must restore the viable verdict"
    # The whole £45k of booked stack revenue was being charged to the customer a second time.
    assert (gross_price - net_price) * energy == pytest.approx(45_000.0, abs=1.0)
