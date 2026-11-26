"""F-02 — the reported priced floor must equal the MILP's own objective value.

Hostile review 2026-08-10, finding F-02 (CONFIRMED against source). `HalfHourlyDispatchSchedule`
previously computed each priced-floor component with an **independent** zero clamp:

    arbitrage_value_gbp      = max(0, Σ energy_price·(discharge − charge)·Δt)
    duos_avoidance_value_gbp = max(0, Σ duos_rate   ·(discharge − charge)·Δt)

but the MILP maximised the **sum**. On a DUoS-driven site the optimum rationally buys energy at a
loss to capture a larger DUoS spread, so the arbitrage component is negative — and clamping it to
zero deleted the loss while keeping the gain. **Reported floor revenue could exceed the LP's own
optimal objective value**, which then flowed into P50/P90, DSCR and both IRRs.

These tests pin the invariant that makes that impossible:

    arbitrage_value_gbp + duos_avoidance_value_gbp == priced_floor_terms_gbp

`priced_floor_terms_gbp` is the unclamped objective term the solver actually maximised.
"""

from __future__ import annotations

import numpy as np
import pytest

from gridq.optimisation.schedule import HalfHourlyDispatchSchedule


def _schedule(
    *,
    charge_kw: np.ndarray,
    discharge_kw: np.ndarray,
    energy_price: np.ndarray | None,
    duos_rate: np.ndarray,
) -> HalfHourlyDispatchSchedule:
    return HalfHourlyDispatchSchedule(
        power_kw=100.0,
        energy_kwh=1000.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=charge_kw,
        discharge_kw=discharge_kw,
        soe_kwh=np.zeros_like(charge_kw),
        site_load_kw=np.full_like(charge_kw, 200.0),
        duos_rate_gbp_per_kwh=duos_rate,
        red_band_mask=duos_rate > 0,
        energy_price_gbp_per_kwh=energy_price,
    )


def _case_negative_arbitrage() -> HalfHourlyDispatchSchedule:
    """A dispatch whose energy-band component is NEGATIVE but whose total is positive.

    Charge in a high-energy-price / zero-DUoS half-hour, discharge in a lower-energy-price but
    high-DUoS half-hour: the DUoS spread pays for the cycle, the energy spread loses money. This is
    the exact shape the old clamp mis-reported.
    """
    charge = np.array([100.0, 0.0, 0.0, 0.0])
    discharge = np.array([0.0, 0.0, 100.0, 0.0])
    # Charge at 30p, discharge at 20p -> energy component = (0-100)*0.30 + (100-0)*0.20 < 0
    energy_price = np.array([0.30, 0.30, 0.20, 0.20])
    # No DUoS on the charge half-hour, high DUoS on the discharge half-hour -> DUoS component > 0
    duos = np.array([0.00, 0.00, 0.25, 0.00])
    return _schedule(
        charge_kw=charge,
        discharge_kw=discharge,
        energy_price=energy_price,
        duos_rate=duos,
    )


def test_negative_arbitrage_component_is_reported_signed() -> None:
    """The energy component really is negative here — the premise of the whole test."""
    s = _case_negative_arbitrage()
    assert s.arbitrage_value_gbp < 0, (
        "test fixture no longer exercises the defect: the energy-band component must be negative"
    )
    assert s.duos_avoidance_value_gbp > 0


def test_floor_components_sum_to_the_objective_when_arbitrage_is_negative() -> None:
    """THE INVARIANT. Under the old clamp this failed: 0 + duos > arbitrage + duos."""
    s = _case_negative_arbitrage()
    assert s.arbitrage_value_gbp + s.duos_avoidance_value_gbp == pytest.approx(
        s.priced_floor_terms_gbp, abs=0.01
    )


def test_clamped_sum_would_have_overstated_the_objective() -> None:
    """Pin the size of the defect, so a regression is recognisable rather than merely red."""
    s = _case_negative_arbitrage()
    clamped = max(0.0, s.arbitrage_value_gbp) + max(0.0, s.duos_avoidance_value_gbp)
    assert clamped > s.priced_floor_terms_gbp, "fixture must exercise the overstatement"
    # The overstatement is exactly the deleted loss.
    assert clamped - s.priced_floor_terms_gbp == pytest.approx(abs(s.arbitrage_value_gbp), abs=0.01)


def test_floor_components_sum_to_the_objective_on_a_conventional_site() -> None:
    """The invariant must also hold in the ordinary case (no negative component)."""
    s = _schedule(
        charge_kw=np.array([100.0, 0.0, 0.0, 0.0]),
        discharge_kw=np.array([0.0, 0.0, 90.0, 0.0]),
        energy_price=np.array([0.10, 0.10, 0.30, 0.30]),
        duos_rate=np.array([0.00, 0.00, 0.20, 0.00]),
    )
    assert s.arbitrage_value_gbp > 0
    assert s.arbitrage_value_gbp + s.duos_avoidance_value_gbp == pytest.approx(
        s.priced_floor_terms_gbp, abs=0.01
    )


def test_invariant_holds_when_no_time_mappable_energy_price_exists() -> None:
    """With no energy price the arbitrage component is 0 and DUoS alone is the objective."""
    s = _schedule(
        charge_kw=np.array([100.0, 0.0, 0.0, 0.0]),
        discharge_kw=np.array([0.0, 0.0, 90.0, 0.0]),
        energy_price=None,
        duos_rate=np.array([0.00, 0.00, 0.20, 0.00]),
    )
    assert s.arbitrage_value_gbp == 0.0
    assert s.arbitrage_value_gbp + s.duos_avoidance_value_gbp == pytest.approx(
        s.priced_floor_terms_gbp, abs=0.01
    )


def test_split_signed_reproduces_the_input_exactly() -> None:
    """`valuation._split_signed` must never lose value: revenue - cost == input."""
    from gridq.optimisation.valuation import _split_signed

    for value in (-1234.56, -0.01, 0.0, 0.01, 9876.54):
        revenue, cost = _split_signed(value)
        assert revenue >= 0 and cost >= 0
        assert revenue - cost == pytest.approx(value, abs=0.01)
