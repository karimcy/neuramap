"""One asset, one power budget - and under D58 limb 1 the budget is MEASURED, not enforced here.

The original defect: capacity-shaped streams were priced independently, so the same MW could be
sold to the Capacity Market, the Balancing Mechanism and a frequency-response service
simultaneously, with all three GBP figures added into the contracted floor debt is sized against.

The literature (`docs/bess_modelling_requirements.md` E-01/E-02) gives two constraints:

- shared power budget: ``P_dis + sum r_up <= P_rated``  (Xu et al. eqs 17-21; Engels et al. eq. 1e)
- energy sustainability: ``E >= sum (D_s/60) x r_s``    (Xu et al. eq. 23; NESO product durations)

**WHAT MOVED, AND WHY (2026-08-19, Olly).** Six tests in this module used to assert that
`allocate_capacity` CUT a claim back to the shared rating. Under the owner's standing instruction
that is no longer what the allocator does, and the tests asserting the cut were the stale half.

* **D57 limb 3** names "stacking rules would forbid earning two such streams on one meter point"
  among the conditions ASSUMED SATISFIED, and says such a condition "must not reduce a figure".
* **D58 limb 1** records the implementation: `allocate_capacity` "pays every claim IN FULL and
  DISCLOSES the shortfall the constraint would have imposed, per stream, with its multiple. What is
  NOT waived is the physics, and it is enforced where physics belongs - in the dispatch".

So the two constraints above did not go away; they moved. The dispatch enforces the physics (one
discharge variable, one state of energy, one annual warranty throughput budget), and this module
now computes the same allocation as before and reports it as a COUNTERFACTUAL on
:attr:`CapacityBudget.constrained_awards`, read through :meth:`CapacityBudget.d57_uncut`.

**The tests below therefore pin the DISCLOSURE, which is the thing that can now silently rot.** An
award paid in full is trivially satisfied by deleting the allocator; a counterfactual that still
respects the rating, still runs in priority order and still applies the sustain durations is not.
Every rewritten test asserts BOTH halves: the full award (the D57 assumption) and the measured cut
(the size of that assumption). Delete `constrained_awards` and six tests here go red.
"""

from __future__ import annotations

import pytest

from gridq.schemas.enums import MarketStream
from gridq.value_stack.capacity_budget import (
    ALLOCATION_PRIORITY,
    SERVICE_SUSTAIN_MINUTES,
    CapacityClaim,
    allocate_capacity,
)

CM = MarketStream.CAPACITY_MARKET
BM = MarketStream.BALANCING_MECHANISM
ANC = MarketStream.ANCILLARY_DC_DM_DR
FLEX = MarketStream.LOCAL_FLEX_PICLO


def test_claims_within_the_rating_are_all_awarded_in_full() -> None:
    budget = allocate_capacity(
        [CapacityClaim(CM, 0.3), CapacityClaim(BM, 0.3)],
        rated_power_mw=1.0,
        usable_energy_mwh=2.0,
    )
    assert not budget.was_oversold
    assert budget.scale_for(CM) == pytest.approx(1.0)
    assert budget.scale_for(BM) == pytest.approx(1.0)
    assert not budget.warnings
    # Nothing was over budget, so no stream reports a shortfall the assumption had to buy.
    assert budget.d57_uncut(CM) is None
    assert budget.d57_uncut(BM) is None


def test_the_same_mw_is_paid_three_times_and_the_shortfall_is_measured() -> None:
    """WAS ``test_the_same_mw_cannot_be_sold_three_times``, which asserted the cut.

    It pinned ``total_awarded_mw <= 1.0``. D58 limb 1 pays the claims in full instead, so that
    assertion is the stale premise; what replaces it is the counterfactual, which must still be
    computed against the rating or the disclosure states nothing. Uses the three CONTINUOUS
    services: the Capacity Market is deliberately not among them, because its de-rating already
    prices the availability conflict (see `capacity_budget` docstring).
    """
    budget = allocate_capacity(
        [CapacityClaim(FLEX, 1.0), CapacityClaim(BM, 1.0), CapacityClaim(ANC, 1.0)],
        rated_power_mw=1.0,
        usable_energy_mwh=4.0,
    )
    assert budget.was_oversold
    assert budget.total_claimed_mw == pytest.approx(3.0)
    # THE D57 ASSUMPTION: every claim is paid in full, so the same MW IS sold three times.
    assert budget.total_awarded_mw == pytest.approx(3.0)
    for stream in (FLEX, BM, ANC):
        assert budget.awards[stream].awarded_mw == pytest.approx(1.0)
        assert not budget.awards[stream].was_cut

    # THE SIZE OF THAT ASSUMPTION, measured rather than asserted in prose.
    assert sum(a.awarded_mw for a in budget.constrained_awards.values()) <= 1.0 + 1e-9, (
        "the counterfactual must still respect the asset's rating, or the disclosure is worthless"
    )
    assert [s for s in (FLEX, BM, ANC) if budget.d57_uncut(s) is not None], (
        "no stream reports the cut the D57 assumption bought it, so the pack cannot state it"
    )
    assert any("OVERSOLD" in w for w in budget.warnings)
    assert any("D57 ASSUMED APPROVED" in w for w in budget.warnings)


def test_priority_still_orders_the_counterfactual_though_both_are_paid_in_full() -> None:
    """WAS ``test_priority_serves_the_contracted_obligation_first`` (BM awarded 0.0).

    The priority order is no longer what a stream is PAID on, but it is still what the disclosed
    shortfall is computed from, so it has to keep working: a contracted DNO flex window outranks an
    optional BM bid in the counterfactual.
    """
    budget = allocate_capacity(
        [CapacityClaim(BM, 1.0), CapacityClaim(FLEX, 1.0)],
        rated_power_mw=1.0,
        usable_energy_mwh=4.0,
    )
    assert budget.awards[FLEX].awarded_mw == pytest.approx(1.0)
    assert budget.awards[BM].awarded_mw == pytest.approx(1.0), "D58 limb 1: paid in full"
    assert budget.constrained_awards[FLEX].awarded_mw == pytest.approx(1.0)
    assert budget.constrained_awards[BM].awarded_mw == pytest.approx(0.0)
    assert ALLOCATION_PRIORITY.index(FLEX) < ALLOCATION_PRIORITY.index(BM)


def test_energy_sustainability_is_measured_on_the_counterfactual() -> None:
    """WAS ``test_energy_sustainability_limits_a_short_duration_asset`` (awarded 0.4).

    Xu et al.: power alone is not deliverability - the energy must sustain the service. Dynamic
    Containment needs 15 minutes, so a 0.1 MWh asset could only offer 0.1 / (15/60) = 0.4 MW
    however much inverter it has. D58 limb 1 pays the 2.0 MW claim anyway and enforces the physics
    in the dispatch; the 0.4 MW is what the reader is shown as the cost of the assumption.
    """
    budget = allocate_capacity([CapacityClaim(ANC, 2.0)], rated_power_mw=2.0, usable_energy_mwh=0.1)
    assert budget.awards[ANC].awarded_mw == pytest.approx(2.0)
    counterfactual = budget.d57_uncut(ANC)
    assert counterfactual is not None
    assert counterfactual.awarded_mw == pytest.approx(0.4, abs=1e-6)
    assert counterfactual.limited_by == "energy sustainability"
    assert counterfactual.scale == pytest.approx(0.2, abs=1e-6)
    assert any("sustain" in w for w in budget.warnings)


def test_the_xu_worked_example_shape_holds_on_the_counterfactual() -> None:
    """WAS ``test_the_xu_worked_example_shape_holds`` (asserted on the award).

    Reserve scheduled under a sustain requirement eats into what else can be delivered. Both arms
    are now paid in full, so the literature's shape lives where the shortfall is recorded: 36 MW /
    3 MWh sustains 6.0 MW of BM alone and only 5.5 MW once 1 MW of DC reserve is committed.
    """
    unreserved = allocate_capacity(
        [CapacityClaim(BM, 36.0)], rated_power_mw=36.0, usable_energy_mwh=3.0
    )
    reserved = allocate_capacity(
        [CapacityClaim(ANC, 1.0), CapacityClaim(BM, 36.0)],
        rated_power_mw=36.0,
        usable_energy_mwh=3.0,
    )
    assert reserved.awards[BM].awarded_mw == pytest.approx(36.0), "D58 limb 1: paid in full"
    assert unreserved.constrained_awards[BM].awarded_mw == pytest.approx(6.0)
    assert reserved.constrained_awards[BM].awarded_mw == pytest.approx(5.5)
    assert reserved.constrained_awards[BM].awarded_mw < unreserved.constrained_awards[BM].awarded_mw


def test_scale_is_one_on_the_award_and_the_cut_multiple_is_on_the_counterfactual() -> None:
    """WAS ``test_scale_is_the_multiplier_to_apply_to_a_streams_revenue`` (BM scale 0.5).

    `scale_for` is what `valuation._apply_capacity_budget` multiplies a stream's GBP by, so under
    D58 limb 1 it must be 1.0 on every stream. The 0.5 it used to be is the multiple the
    assumptions register now publishes, read off `d57_uncut`.
    """
    budget = allocate_capacity(
        [CapacityClaim(FLEX, 0.6), CapacityClaim(BM, 0.8)],
        rated_power_mw=1.0,
        usable_energy_mwh=4.0,
    )
    assert budget.scale_for(FLEX) == pytest.approx(1.0)
    assert budget.scale_for(BM) == pytest.approx(1.0), "no stream's revenue may be scaled down"
    # BM would have got only the residual 0.4 of its 0.8 claim, and that is the published multiple.
    uncut = budget.d57_uncut(BM)
    assert uncut is not None and uncut.scale == pytest.approx(0.5, abs=1e-6)
    assert uncut.limited_by == "power budget"
    assert budget.d57_uncut(FLEX) is None, "the priority stream was never cut"


def test_a_stream_with_no_claim_is_unaffected() -> None:
    budget = allocate_capacity(
        [CapacityClaim(FLEX, 1.0)], rated_power_mw=1.0, usable_energy_mwh=2.0
    )
    assert budget.scale_for(BM) == pytest.approx(1.0), (
        "a stream that made no capacity claim must not be scaled"
    )


def test_a_zero_rating_still_pays_in_full_and_records_a_zero_counterfactual() -> None:
    """WAS ``test_zero_rating_awards_nothing``.

    The degenerate guard. A 0 MW / 0 MWh asset can sustain nothing, so the counterfactual is 0.00
    MW; D58 limb 1 pays the claim regardless, because the shared budget is not enforced here at
    all. Kept as a test of the DISCLOSURE on the extreme case: if `constrained_awards` ever stopped
    being computed, this is where the omission is loudest.
    """
    budget = allocate_capacity(
        [CapacityClaim(FLEX, 1.0)], rated_power_mw=0.0, usable_energy_mwh=0.0
    )
    assert budget.total_awarded_mw == pytest.approx(1.0)
    uncut = budget.d57_uncut(FLEX)
    assert uncut is not None
    assert uncut.awarded_mw == pytest.approx(0.0)
    assert uncut.scale == pytest.approx(0.0)
    assert any("OVERSOLD" in w for w in budget.warnings)
    assert any("D57 ASSUMED APPROVED" in w for w in budget.warnings)


def test_every_priority_stream_has_a_sustain_duration() -> None:
    """A service with no declared sustain duration would silently escape the energy constraint."""
    for stream in ALLOCATION_PRIORITY:
        if stream is MarketStream.CAPACITY_MARKET:
            continue  # excluded from the continuous budget; de-rating prices its availability
        assert stream in SERVICE_SUSTAIN_MINUTES, f"{stream} has no sustain duration"
        assert SERVICE_SUSTAIN_MINUTES[stream] > 0


def test_allocation_is_deterministic_regardless_of_claim_order() -> None:
    claims = [CapacityClaim(BM, 0.5), CapacityClaim(ANC, 0.5), CapacityClaim(FLEX, 0.5)]
    a = allocate_capacity(claims, rated_power_mw=1.0, usable_energy_mwh=4.0)
    b = allocate_capacity(list(reversed(claims)), rated_power_mw=1.0, usable_energy_mwh=4.0)
    assert {s: aw.awarded_mw for s, aw in a.awards.items()} == {
        s: aw.awarded_mw for s, aw in b.awards.items()
    }
    # The counterfactual is order-independent too, or the published multiple would depend on the
    # order the caller happened to build its claim list in.
    assert {s: aw.awarded_mw for s, aw in a.constrained_awards.items()} == {
        s: aw.awarded_mw for s, aw in b.constrained_awards.items()
    }


def test_the_capacity_market_does_not_reserve_continuous_headroom() -> None:
    """A CM agreement must not starve every other service for 8,760 hours.

    The obligation binds during stress events (~hours per year) and the de-rating factor already
    prices that availability conflict. An earlier version of this module DID charge CM against the
    continuous budget, and it zeroed a real site's local-flex and BM revenue entirely. Still worth
    a test under D58 limb 1: CM sitting outside `POWER_BUDGET_STREAMS` is what keeps it out of the
    COUNTERFACTUAL too, so a regression here would overstate every disclosed shortfall.
    """
    from gridq.value_stack.capacity_budget import POWER_BUDGET_STREAMS

    assert CM not in POWER_BUDGET_STREAMS
    budget = allocate_capacity(
        [CapacityClaim(CM, 1.0), CapacityClaim(BM, 1.0)],
        rated_power_mw=1.0,
        usable_energy_mwh=4.0,
    )
    assert budget.awards[BM].awarded_mw == pytest.approx(1.0), (
        "a CM claim must not consume the continuous power budget"
    )
    assert budget.d57_uncut(BM) is None, (
        "a CM claim must not appear in the counterfactual either, or the disclosed shortfall "
        "double-counts the de-rating the Capacity Market already applied"
    )
