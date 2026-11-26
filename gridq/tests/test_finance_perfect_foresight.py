"""Tests for the perfect-foresight adjustment (W8, north_star W3 risk shape).

The review's top honesty gap closes: P50 dispatch revenue is no longer the raw
perfect-foresight LP optimum. The adjustment is a NAMED deduction (per-stream £ stays raw and
matches the record's stream table), applies only to dispatch-optimised streams, is loudly
labelled with its assumption status, and degrades to an explicit upper-bound warning when the
key is unusable — never a silent flatter, never an invented percentage.
"""

from __future__ import annotations

import copy
from collections.abc import Callable

import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.finance.cashflows import build_cashflow_model

PF_PCT = 20.0  # gb_v0_1 p50_p90_haircuts.perfect_foresight_haircut_pct (needs_review input)


def _result(streams: list[tuple[s.MarketStream, float, s.Firmness]]) -> s.OptimisationResult:
    return s.OptimisationResult(
        result_id="res_pf",
        request_id="req_pf",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=200.0,
        selected_energy_kwh=400.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(stream=st, annual_revenue_gbp=rev, firmness=firm)
            for st, rev, firm in streams
        ],
        solver_status=s.SolverStatus.BASELINE,
    )


def _mixed_result() -> s.OptimisationResult:
    return _result(
        [
            (s.MarketStream.TARIFF_ARBITRAGE, 40_000.0, s.Firmness.FIRM),
            (s.MarketStream.DUOS_RED_BAND_AVOIDANCE, 10_000.0, s.Firmness.FIRM),
            (s.MarketStream.CAPACITY_MARKET, 9_000.0, s.Firmness.CONTRACTED),
        ]
    )


def test_adjustment_hits_dispatch_optimised_streams_only(
    make_site: Callable[..., s.SiteModel],
) -> None:
    model = build_cashflow_model(make_site(), _mixed_result(), load_assumptions("gb_v0_1"))
    # 20% of (40k arbitrage + 10k DUoS); the CM availability payment carries no premium.
    assert model.perfect_foresight_adjustment_gbp == pytest.approx(0.2 * 50_000.0)
    # Per-stream £ stays RAW — it must match the record's stream table everywhere.
    assert model.annual_revenue_by_stream_gbp[s.MarketStream.TARIFF_ARBITRAGE.value] == 40_000.0
    assert model.total_annual_revenue_gbp == pytest.approx(59_000.0)
    # The EBITDA basis nets the named deduction: (59k − 10k) − dispatch − opex.
    expected_year1 = round(
        59_000.0
        - model.perfect_foresight_adjustment_gbp
        - model.annual_dispatch_cost_gbp
        - model.total_annual_operating_cost_gbp,
        2,
    )
    assert model.p50_ebitda_gbp[0] == pytest.approx(expected_year1, abs=0.02)
    assert "p50_p90_haircuts.perfect_foresight_haircut_pct" in model.assumptions_used
    assert any("INTERIM, uncalibrated" in w for w in model.warnings)
    assert any("M7" in w for w in model.warnings)


def test_p90_compounds_on_the_adjusted_p50(make_site: Callable[..., s.SiteModel]) -> None:
    lib = load_assumptions("gb_v0_1")
    model = build_cashflow_model(make_site(), _mixed_result(), lib)
    p90_haircut = float(lib.get("p50_p90_haircuts", "p90_revenue_haircut_pct").value)
    adjusted_rev = 59_000.0 - model.perfect_foresight_adjustment_gbp
    expected_p90_year1 = round(
        adjusted_rev * (1 - p90_haircut / 100.0)
        - model.annual_dispatch_cost_gbp
        - model.total_annual_operating_cost_gbp,
        2,
    )
    assert model.p90_ebitda_gbp[0] == pytest.approx(expected_p90_year1, abs=0.02)


def test_no_dispatch_optimised_revenue_means_no_adjustment(
    make_site: Callable[..., s.SiteModel],
) -> None:
    cm_only = _result([(s.MarketStream.CAPACITY_MARKET, 9_000.0, s.Firmness.CONTRACTED)])
    model = build_cashflow_model(make_site(), cm_only, load_assumptions("gb_v0_1"))
    assert model.perfect_foresight_adjustment_gbp is None
    assert not any("perfect-foresight" in w.lower() for w in model.warnings)


def test_unusable_key_warns_upper_bound_and_never_invents(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = copy.deepcopy(load_assumptions("gb_v0_1"))
    del lib.p50_p90_haircuts["perfect_foresight_haircut_pct"]
    model = build_cashflow_model(make_site(), _mixed_result(), lib)
    assert model.perfect_foresight_adjustment_gbp is None
    assert any("upper bound" in w for w in model.warnings)
    # Unadjusted: EBITDA basis is the raw sum (the honest, loudly-labelled degrade).
    expected_year1 = round(
        59_000.0 - model.annual_dispatch_cost_gbp - model.total_annual_operating_cost_gbp, 2
    )
    assert model.p50_ebitda_gbp[0] == pytest.approx(expected_year1, abs=0.02)
