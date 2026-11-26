"""Tests for the scenario engine — deterministic Low/Central/High over a base case."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.finance.financial_model import capital_recovery_account
from gridq.scenarios import (
    DEFAULT_POLICY_BRANCH,
    ForecastReference,
    Scenario,
    ScenarioEngine,
    ScenarioName,
    ScenarioSet,
    build_forecast_reference,
    build_standard_scenario_set,
    run_scenario_set,
)


def _financial_case(
    p50: list[float] | None = None, p90: list[float] | None = None
) -> s.FinancialCase:
    """Minimal valid FinancialCase: year-0 capex outflow + operating cashflows."""
    return s.FinancialCase(
        case_id="fc_test",
        p50_cashflows=p50 if p50 is not None else [-1000.0, 200.0, 200.0, 200.0],
        p90_cashflows=p90 if p90 is not None else [-1000.0, 170.0, 170.0, 170.0],
        dscr_summary=s.DscrSummary(),
        capital_recovery=capital_recovery_account(
            p50 if p50 is not None else [-1000.0, 200.0, 200.0, 200.0]
        ),
        debt_assumptions=s.DebtAssumptions(),
        price_recommendation=s.PriceRecommendation(),
        covenant_status=s.CovenantStatus(),
    )


def _set(*scenarios: Scenario, set_id: str = "t") -> ScenarioSet:
    return ScenarioSet(
        scenario_set_id=set_id, assumption_version="gb_v0_1", scenarios=list(scenarios)
    )


def test_runs_with_minimal_financial_case() -> None:
    base = _financial_case()
    scn = _set(
        Scenario(
            scenario_id="low", name=ScenarioName.LOW, probability_weight=0.25, price_multiplier=0.9
        ),
        Scenario(scenario_id="central", name=ScenarioName.CENTRAL, probability_weight=0.5),
        Scenario(
            scenario_id="high",
            name=ScenarioName.HIGH,
            probability_weight=0.25,
            price_multiplier=1.1,
        ),
    )
    out = run_scenario_set(base, scn)
    assert [r.scenario_id for r in out.results] == ["low", "central", "high"]
    assert out.assumption_version == "gb_v0_1"


def test_central_identity_reproduces_base() -> None:
    base = _financial_case()
    scn = _set(Scenario(scenario_id="central", name=ScenarioName.CENTRAL, probability_weight=1.0))
    out = ScenarioEngine().run(base, scn)
    assert out.results[0].p50_cashflows == base.p50_cashflows
    assert out.results[0].p90_cashflows == base.p90_cashflows


def test_price_multiplier_scales_operating_and_cost_scales_capex() -> None:
    base = _financial_case(p50=[-1000.0, 200.0, 200.0], p90=[-1000.0, 200.0, 200.0])
    scn = _set(
        Scenario(
            scenario_id="x",
            name=ScenarioName.CUSTOM,
            probability_weight=1.0,
            price_multiplier=1.1,
            cost_multiplier=1.2,
        )
    )
    r = ScenarioEngine().run(base, scn).results[0]
    # year 0 (capex) scaled by cost_multiplier; operating years by price_multiplier
    assert r.p50_cashflows[0] == -1000.0 * 1.2
    assert r.p50_cashflows[1] == 200.0 * 1.1
    assert r.p50_cashflows[2] == 200.0 * 1.1


def test_weighted_summary_is_deterministic() -> None:
    base = _financial_case()
    scn = _set(
        Scenario(
            scenario_id="low", name=ScenarioName.LOW, probability_weight=0.25, price_multiplier=0.9
        ),
        Scenario(scenario_id="central", name=ScenarioName.CENTRAL, probability_weight=0.5),
        Scenario(
            scenario_id="high",
            name=ScenarioName.HIGH,
            probability_weight=0.25,
            price_multiplier=1.1,
        ),
    )
    a = run_scenario_set(base, scn).model_dump(mode="json")
    b = run_scenario_set(base, scn).model_dump(mode="json")
    assert a == b
    # weighted central = 0.25*0.9 + 0.5*1.0 + 0.25*1.1 = 1.0 → equals base operating cashflow
    summary = run_scenario_set(base, scn).weighted_summary
    assert summary.weights_normalised is False
    assert abs(summary.weighted_p50_cashflows[1] - 200.0) < 1e-9


def test_off_sum_weights_are_normalised_with_warning() -> None:
    base = _financial_case()
    scn = _set(
        Scenario(scenario_id="low", name=ScenarioName.LOW, probability_weight=0.5),
        Scenario(scenario_id="central", name=ScenarioName.CENTRAL, probability_weight=0.5),
        Scenario(scenario_id="high", name=ScenarioName.HIGH, probability_weight=0.5),  # sums to 1.5
    )
    out = run_scenario_set(base, scn)
    assert out.weighted_summary.weights_normalised is True
    assert any("normalised" in w for w in out.weighted_summary.warnings)
    assert abs(sum(r.applied_weight for r in out.results) - 1.0) < 1e-9


def test_cod_delay_whole_year_shifts_timing_only() -> None:
    base = _financial_case(p50=[-1000.0, 200.0, 200.0, 200.0], p90=[-1000.0, 200.0, 200.0, 200.0])
    scn = _set(
        Scenario(
            scenario_id="delayed",
            name=ScenarioName.CUSTOM,
            probability_weight=1.0,
            cod_delay_months=12,
        )
    )
    r = ScenarioEngine().run(base, scn).results[0]
    # capex stays at year 0; operating shifted one year later; tail beyond horizon dropped
    assert r.p50_cashflows == [-1000.0, 0.0, 200.0, 200.0]
    assert any("COD delayed" in w for w in r.warnings)


def test_cod_delay_partial_year_warns_and_leaves_timing_unchanged() -> None:
    base = _financial_case(p50=[-1000.0, 200.0, 200.0], p90=[-1000.0, 200.0, 200.0])
    scn = _set(
        Scenario(
            scenario_id="part",
            name=ScenarioName.CUSTOM,
            probability_weight=1.0,
            cod_delay_months=6,
        )
    )
    r = ScenarioEngine().run(base, scn).results[0]
    assert r.p50_cashflows == [-1000.0, 200.0, 200.0]  # unchanged
    assert any("not a whole number of years" in w for w in r.warnings)


def test_no_fake_precision_from_placeholder_policy_branch() -> None:
    base = _financial_case()
    plain = Scenario(scenario_id="a", name=ScenarioName.CENTRAL, probability_weight=1.0)
    branded = Scenario(
        scenario_id="b",
        name=ScenarioName.CENTRAL,
        probability_weight=1.0,
        policy_branch="rema_zonal_pricing_placeholder",
    )
    r_plain = ScenarioEngine().run(base, _set(plain, set_id="p")).results[0]
    r_branded = ScenarioEngine().run(base, _set(branded, set_id="b")).results[0]
    # the policy-branch label changes NO number ...
    assert r_branded.p50_cashflows == r_plain.p50_cashflows
    assert r_branded.total_p50_gbp == r_plain.total_p50_gbp
    # ... but it is flagged as descriptive-only so it can't masquerade as calibrated
    assert r_branded.policy_branch == "rema_zonal_pricing_placeholder"
    assert any("descriptive only" in w for w in r_branded.warnings)
    assert all("descriptive only" not in w for w in r_plain.warnings)


def test_build_standard_set_reads_weights_from_library_and_flags_placeholders() -> None:
    lib = load_assumptions("gb_v0_1")
    scn = build_standard_scenario_set(lib)
    by_id = {sc.scenario_id: sc for sc in scn.scenarios}
    # weights come from scenario_weights.central_low_high (0.25 / 0.50 / 0.25)
    assert by_id["low"].probability_weight == 0.25
    assert by_id["central"].probability_weight == 0.50
    assert by_id["high"].probability_weight == 0.25
    # placeholder weights assumption → set flagged not bankable
    assert any("not bankable" in w for w in scn.validation_warnings)
    # no band supplied → identity multipliers, but warned (no fabricated spread)
    assert by_id["low"].price_multiplier == 1.0
    assert any("ranked data ask" in w for w in by_id["low"].warnings)


def test_build_standard_set_with_explicit_price_band_notes_missing_cost_band() -> None:
    lib = load_assumptions("gb_v0_1")
    scn = build_standard_scenario_set(lib, low_price_multiplier=0.85, high_price_multiplier=1.15)
    by_id = {sc.scenario_id: sc for sc in scn.scenarios}
    assert by_id["low"].price_multiplier == 0.85
    assert by_id["high"].price_multiplier == 1.15
    # a real price spread exists → not flagged "equals Central"
    assert all("equals Central" not in w for w in by_id["low"].warnings)
    # but the un-supplied cost dimension is noted per-dimension (not silent)
    assert any("COST band" in w for w in by_id["low"].warnings)
    # central is always identity (the base case)
    assert by_id["central"].price_multiplier == 1.0


def test_cost_only_band_is_not_mislabelled_equals_central() -> None:
    lib = load_assumptions("gb_v0_1")
    scn = build_standard_scenario_set(lib, low_cost_multiplier=1.2)
    low = next(sc for sc in scn.scenarios if sc.scenario_id == "low")
    assert low.cost_multiplier == 1.2  # a genuine spread
    assert all("equals Central" not in w for w in low.warnings)
    assert any("PRICE band" in w for w in low.warnings)  # missing price dim noted


def test_run_result_carries_coarseness_note() -> None:
    base = _financial_case()
    scn = _set(Scenario(scenario_id="central", name=ScenarioName.CENTRAL, probability_weight=1.0))
    out = run_scenario_set(base, scn)
    assert any("not a valuation" in w for w in out.warnings)
    assert out.results[0].policy_branch == DEFAULT_POLICY_BRANCH


def test_standard_set_run_is_not_bankable_on_placeholder_weights() -> None:
    lib = load_assumptions("gb_v0_1")
    scn = build_standard_scenario_set(lib)
    assert scn.bankable_status == s.BankableStatus.NOT_BANKABLE  # placeholder weights
    out = run_scenario_set(_financial_case(), scn)
    # machine-readable, not just prose
    assert out.bankable_status == s.BankableStatus.NOT_BANKABLE


def test_unverified_forecast_ref_forces_not_bankable_with_warning() -> None:
    ref = ForecastReference(
        source="Modo Energy",
        file_ref="data/forecasts/modo.csv",
        forecast_version="modo-2026-q2",
        content_hash="sha256:" + "ab" * 32,  # well-formed but not verified against a file
        created_at=datetime(2026, 4, 1),
        price_unit="GBP/MWh",
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2040, 12, 31),
    )
    assert ref.verified is False
    scn = ScenarioSet(
        scenario_set_id="fc",
        assumption_version="gb_v0_1",
        bankable_status=s.BankableStatus.REVIEW_REQUIRED,
        scenarios=[
            Scenario(
                scenario_id="high",
                name=ScenarioName.HIGH,
                probability_weight=1.0,
                price_multiplier=1.1,
                forecast_ref=ref,
            )
        ],
    )
    out = run_scenario_set(_financial_case(), scn)
    assert out.bankable_status == s.BankableStatus.NOT_BANKABLE
    assert any("unverified" in w for w in out.results[0].warnings)


def test_verified_forecast_ref_does_not_force_not_bankable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # the positive branch: a VERIFIED forecast_ref must NOT trip the unverified downgrade
    p = tmp_path / "modo.csv"
    p.write_text("ts,price\n2026-01-01T00:00,80.0\n", encoding="utf-8")
    ref = build_forecast_reference(
        file_path=p,
        source="Modo Energy",
        forecast_version="modo-2026-q2",
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2040, 12, 31),
        created_at=datetime(2026, 4, 1),
    )
    assert ref.verified is True
    scn = ScenarioSet(
        scenario_set_id="fc",
        assumption_version="gb_v0_1",
        bankable_status=s.BankableStatus.REVIEW_REQUIRED,
        scenarios=[
            Scenario(
                scenario_id="high",
                name=ScenarioName.HIGH,
                probability_weight=1.0,
                price_multiplier=1.1,
                forecast_ref=ref,
            )
        ],
    )
    out = run_scenario_set(_financial_case(), scn)
    assert out.bankable_status == s.BankableStatus.REVIEW_REQUIRED  # not downgraded
    assert all("unverified" not in w for w in out.results[0].warnings)


def test_high_central_low_spread_is_monotonic() -> None:
    base = _financial_case()
    scn = _set(
        Scenario(
            scenario_id="low", name=ScenarioName.LOW, probability_weight=0.25, price_multiplier=0.8
        ),
        Scenario(scenario_id="central", name=ScenarioName.CENTRAL, probability_weight=0.5),
        Scenario(
            scenario_id="high",
            name=ScenarioName.HIGH,
            probability_weight=0.25,
            price_multiplier=1.2,
        ),
    )
    by_id = {r.scenario_id: r for r in run_scenario_set(base, scn).results}
    # operating cashflows are positive, so a higher price multiplier ⇒ higher total
    assert by_id["high"].total_p50_gbp > by_id["central"].total_p50_gbp > by_id["low"].total_p50_gbp


def test_cod_delay_multi_year_and_beyond_horizon() -> None:
    base = _financial_case(p50=[-1000.0, 200.0, 200.0, 200.0], p90=[-1000.0, 200.0, 200.0, 200.0])
    # 24 months → shift operating cashflows two years later; tail dropped
    two_yr = (
        ScenarioEngine()
        .run(
            base,
            _set(
                Scenario(
                    scenario_id="d2",
                    name=ScenarioName.CUSTOM,
                    probability_weight=1.0,
                    cod_delay_months=24,
                )
            ),
        )
        .results[0]
    )
    assert two_yr.p50_cashflows == [-1000.0, 0.0, 0.0, 200.0]
    # a delay beyond the horizon zeroes all operating years (capex remains)
    huge = (
        ScenarioEngine()
        .run(
            base,
            _set(
                Scenario(
                    scenario_id="d99",
                    name=ScenarioName.CUSTOM,
                    probability_weight=1.0,
                    cod_delay_months=120,
                )
            ),
        )
        .results[0]
    )
    assert huge.p50_cashflows == [-1000.0, 0.0, 0.0, 0.0]


def test_all_zero_weights_fall_back_to_equal_at_engine_level() -> None:
    base = _financial_case()
    scn = _set(
        Scenario(scenario_id="a", name=ScenarioName.CUSTOM, probability_weight=0.0),
        Scenario(scenario_id="b", name=ScenarioName.CUSTOM, probability_weight=0.0),
    )
    out = run_scenario_set(base, scn)
    assert [r.applied_weight for r in out.results] == [0.5, 0.5]
    assert any("equal weights" in w for w in out.weighted_summary.warnings)


def test_non_finite_multiplier_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Scenario(
            scenario_id="x",
            name=ScenarioName.CUSTOM,
            probability_weight=1.0,
            price_multiplier=float("inf"),
        )


def test_non_finite_base_cashflow_is_warned_not_silent() -> None:
    base = _financial_case(p50=[-1000.0, float("nan"), 200.0], p90=[-1000.0, 200.0, 200.0])
    out = run_scenario_set(
        base, _set(Scenario(scenario_id="c", name=ScenarioName.CENTRAL, probability_weight=1.0))
    )
    assert any("non-finite" in w for w in out.results[0].warnings)
