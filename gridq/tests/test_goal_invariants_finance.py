"""Goal invariants for finance (M5/M9) — the vacuous-logic workstream (F-005/F-006).

Locks the behaviour that turned hollow deferred capabilities into real, versioned ones:

- **Honest degradation** (INV-M5-08): P90 <= P50 every year, and with a positive capacity fade
  the revenue actually decreases year over year (the ``(1-fade)^(year-1)`` compounding).
- **Haircut sign**: a negative P90 haircut is clamped to zero with a warning — the cautious case
  can never sit above the central one.
- **Real sculpting** (was INV-M5-12's "not implemented"): a P90-DSCR sculpted schedule whose
  present value at the debt rate equals the principal, a flat DSCR at the computed target, an
  honest annuity fallback when a P90 CFADS year is non-positive, and a ``DebtTerms`` default of
  ANNUITY so every pre-existing construction behaves exactly as before.
- **The DSCR covenant gate fires in real mode** on gb_v1_2 (no injected debt terms): the library
  gearing placeholder + the M9 credit mapping assemble DebtTerms, the DSCR is available, the
  ``SizingObjective.feasible`` gate genuinely rejects a low-CFADS candidate — and everything
  stays not bankable on the placeholder assumptions.
- **Degradation marginal cost** is release-gated: the cost line prices value x discharge MWh only
  when the library carries the key (gb_v1_2+); old releases are byte-for-byte unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.finance import (
    SCULPTING_ANNUITY,
    SCULPTING_P90_DSCR,
    DebtTerms,
    DispatchEnergy,
    run_financial_model,
    sculpted_schedule,
    size_and_measure_dscr,
)
from gridq.finance.cashflows import build_cashflow_model
from gridq.schemas.enums import CreditStatus
from gridq.sizing.objective import SizingObjective
from gridq.sizing.search_space import SizingCandidate

# --- fixtures ---------------------------------------------------------------


def _site(
    *,
    levies: dict[str, float] | None = None,
    credit: CreditStatus = CreditStatus.ADEQUATE,
) -> s.SiteModel:
    load = s.LoadDataSummary(
        annual_kwh=1_800_000.0,
        half_hourly_rows=17520,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        max_import_kw=420.0,
    )
    return s.SiteModel(
        site_id="site_goal_fin",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=load,
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"single": 0.24},
            levies=levies or {},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(
            legal_name="Acme Ltd", company_number="01234567", credit_status=credit
        ),
    )


def _result(*, revenue: float, power_kw: float = 500.0) -> s.OptimisationResult:
    """A synthetic result — clearly a TEST fixture, never real site data."""
    return s.OptimisationResult(
        result_id="res_goal_fin",
        request_id="req_goal_fin",
        implementation_level=s.ImplementationLevel.IN_PROGRESS,
        bankable_status=s.BankableStatus.REVIEW_REQUIRED,
        selected_power_kw=power_kw,
        selected_energy_kwh=power_kw * 2,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=revenue,
                firmness=s.Firmness.FIRM,
            )
        ],
        solver_status=s.SolverStatus.MOCK,
    )


def _lib(version: str = "gb_v1_2") -> AssumptionLibrary:
    return load_assumptions(version)


# --- P90 <= P50 + honest degradation (INV-M5-08) -----------------------------


def test_p90_at_or_below_p50_every_year_with_positive_revenue() -> None:
    model = build_cashflow_model(_site(), _result(revenue=150000.0), _lib(), horizon_years=15)
    # year 0 (capex) identical; every operating year: P90 <= P50 (strictly below with revenue).
    assert model.p90_fcf_pre_debt_gbp[0] == model.p50_fcf_pre_debt_gbp[0]
    for p50, p90 in zip(
        model.p50_fcf_pre_debt_gbp[1:], model.p90_fcf_pre_debt_gbp[1:], strict=True
    ):
        assert p90 < p50


def test_degradation_compounds_so_revenue_actually_decreases_year_over_year() -> None:
    # The honest-degradation invariant: with fade > 0 and positive revenue, the (1-fade)^(year-1)
    # compounding means each operating year earns strictly less than the one before — in BOTH the
    # P50 and P90 views (costs are held flat, so EBITDA falls exactly with revenue).
    lib = _lib()
    fade = float(lib.get("degradation", "annual_capacity_fade_pct").value)
    assert fade > 0, "fixture requires a positive capacity fade"
    model = build_cashflow_model(_site(), _result(revenue=150000.0), lib, horizon_years=12)
    for series in (model.p50_ebitda_gbp, model.p90_ebitda_gbp):
        for earlier, later in zip(series, series[1:], strict=False):
            assert later < earlier


def test_no_degradation_when_fade_zero() -> None:
    # The compounding is driven by the library fade, not a hidden constant: zero fade => flat.
    lib = _lib()
    lib.degradation["annual_capacity_fade_pct"].value = 0.0
    model = build_cashflow_model(_site(), _result(revenue=150000.0), lib, horizon_years=8)
    assert len(set(model.p50_ebitda_gbp)) == 1


def test_negative_haircut_is_clamped_with_warning_so_p90_never_exceeds_p50() -> None:
    # A negative haircut would put the cautious case ABOVE the central one. The honest option:
    # clamp to zero (P90 == P50) and warn loudly — never a silently flattered downside case.
    lib = _lib()
    lib.p50_p90_haircuts["p90_revenue_haircut_pct"].value = -10.0
    model = build_cashflow_model(_site(), _result(revenue=150000.0), lib, horizon_years=10)
    assert model.p90_fcf_pre_debt_gbp == model.p50_fcf_pre_debt_gbp
    assert any("clamped to 0" in w for w in model.warnings)


# --- sculpting (the P90-DSCR schedule is real) --------------------------------


def test_debt_terms_default_basis_is_annuity() -> None:
    # The default flips to the honest name for the historical behaviour: any DebtTerms built
    # without an explicit basis produces exactly the level-payment schedule it always did.
    terms = DebtTerms(gearing_pct=60.0, annual_rate_pct=7.5, tenor_years=8)
    assert terms.sculpting_basis == SCULPTING_ANNUITY


def test_debt_terms_reject_unknown_sculpting_basis() -> None:
    with pytest.raises(ValueError, match="sculpting_basis"):
        DebtTerms(gearing_pct=60.0, annual_rate_pct=7.5, tenor_years=8, sculpting_basis="MAGIC")


_CFADS = [50000.0, 48000.0, 46000.0, 44000.0, 42000.0, 40000.0, 38000.0, 36000.0, 34000.0]


def test_sculpted_schedule_pv_equals_principal() -> None:
    principal, rate, tenor = 200000.0, 7.5, 8
    service, target, warnings = sculpted_schedule(principal, rate, tenor, _CFADS)
    assert service is not None and target is not None and warnings == []
    i = rate / 100.0
    pv = sum(p / (1.0 + i) ** year for year, p in enumerate(service, start=1))
    assert pv == pytest.approx(principal, rel=1e-6)
    assert all(p > 0 for p in service)  # never a negative service


def test_sculpted_schedule_zero_rate_pv_is_plain_sum() -> None:
    service, target, _ = sculpted_schedule(100000.0, 0.0, 5, [30000.0] * 5)
    assert service is not None
    assert sum(service) == pytest.approx(100000.0, rel=1e-6)
    assert target == pytest.approx(1.5)


def test_sculpted_dscr_is_flat_at_the_computed_target() -> None:
    principal, rate, tenor = 200000.0, 7.5, 8
    _, target, _ = sculpted_schedule(principal, rate, tenor, _CFADS)
    terms = DebtTerms(
        gearing_pct=60.0,
        annual_rate_pct=rate,
        tenor_years=tenor,
        sculpting_basis=SCULPTING_P90_DSCR,
    )
    res = size_and_measure_dscr(principal / 0.60, _CFADS, terms)
    assert res.available is True
    assert res.sculpting_basis == SCULPTING_P90_DSCR
    assert len(res.by_year) == tenor
    for dscr in res.by_year:
        assert dscr == pytest.approx(target, rel=1e-3)
    assert res.min_dscr == pytest.approx(target, rel=1e-3)


def test_sculpting_falls_back_to_annuity_when_a_p90_year_is_non_positive() -> None:
    bad = list(_CFADS)
    bad[3] = -1.0
    terms = DebtTerms(
        gearing_pct=60.0,
        annual_rate_pct=7.5,
        tenor_years=8,
        sculpting_basis=SCULPTING_P90_DSCR,
    )
    res = size_and_measure_dscr(333333.33, bad, terms)
    # honest degrade: the schedule that was actually produced is the annuity, with a warning.
    assert res.sculpting_basis == SCULPTING_ANNUITY
    assert any("sculpting unavailable" in w for w in res.warnings)
    assert all(p == res.debt_service_by_year_gbp[0] for p in res.debt_service_by_year_gbp)
    assert all(p > 0 for p in res.debt_service_by_year_gbp)


def test_sculpting_basis_recorded_on_the_case_is_what_actually_happened() -> None:
    lib = _lib()
    sculpt_terms = DebtTerms(
        gearing_pct=60.0,
        annual_rate_pct=7.5,
        tenor_years=8,
        sculpting_basis=SCULPTING_P90_DSCR,
    )
    # Positive P90 CFADS across the tenor => the sculpt happens and is recorded.
    sculpted = run_financial_model(
        _site(), _result(revenue=150000.0), lib, horizon_years=15, debt_terms=sculpt_terms
    )
    assert sculpted.case.debt_assumptions.sculpting_basis == SCULPTING_P90_DSCR
    assert len(set(sculpted.case.dscr_summary.by_year)) == 1  # flat by construction
    # Zero revenue => negative P90 CFADS => the sculpt degrades and the record says ANNUITY.
    fallback = run_financial_model(
        _site(), _result(revenue=0.0), lib, horizon_years=15, debt_terms=sculpt_terms
    )
    assert fallback.case.debt_assumptions.sculpting_basis == SCULPTING_ANNUITY
    assert any("sculpting unavailable" in w for w in fallback.case.warnings)


def test_annuity_and_sculpt_bases_produce_different_schedules() -> None:
    # The basis is load-bearing, not decorative: on a decaying CFADS profile the two schedules
    # genuinely differ (the sculpt tracks the profile; the annuity is level).
    annuity = DebtTerms(gearing_pct=60.0, annual_rate_pct=7.5, tenor_years=8)
    sculpt = DebtTerms(
        gearing_pct=60.0,
        annual_rate_pct=7.5,
        tenor_years=8,
        sculpting_basis=SCULPTING_P90_DSCR,
    )
    res_a = size_and_measure_dscr(333333.33, _CFADS, annuity)
    res_s = size_and_measure_dscr(333333.33, _CFADS, sculpt)
    assert res_a.debt_service_by_year_gbp != res_s.debt_service_by_year_gbp
    assert res_a.sculpting_basis == SCULPTING_ANNUITY
    # Same principal borrowed either way.
    assert res_a.principal_gbp == res_s.principal_gbp


# --- the DSCR covenant gate fires in real mode on gb_v1_2 (F-005) -------------


def test_gb_v1_2_real_mode_produces_an_available_dscr_without_injected_debt() -> None:
    # No debt_terms injected: gearing comes from the gb_v1_2 placeholder and rate/tenor from the
    # M9 credit mapping. This is exactly the path that was vacuous on gb_v0_1/gb_v1_0.
    out = run_financial_model(_site(), _result(revenue=150000.0), _lib(), horizon_years=15)
    assert out.dscr.available is True
    assert out.dscr.min_dscr is not None
    assert out.case.debt_assumptions.gearing_pct == pytest.approx(60.0)
    assert (out.case.debt_assumptions.rate_pct, out.case.debt_assumptions.tenor_years) == (
        7.5,
        10.0,
    )  # the ADEQUATE band from the folded-in credit policy


def test_gb_v1_2_old_releases_stay_unavailable() -> None:
    # Regression pin: the older frozen releases still have no debt structure, so the gate stays
    # honestly unfireable there — the capability is versioned, not retro-fitted.
    for version in ("gb_v1_0",):
        out = run_financial_model(
            _site(), _result(revenue=150000.0), _lib(version), horizon_years=15
        )
        assert out.dscr.available is False


@dataclass(frozen=True)
class _Eval:
    """A minimal Evaluation double for the SizingObjective (structural Protocol match)."""

    candidate: SizingCandidate
    min_p90_dscr: float | None
    levered_return_pct: float | None = None
    project_irr_pct: float | None = None
    end_of_life_deliverable: bool | None = None


def test_covenant_gate_rejects_a_low_cfads_candidate_in_real_mode() -> None:
    # Both DSCRs come from REAL gb_v1_2 finance runs (no injected debt). The strong-revenue
    # candidate clears the 1.30 covenant; the weak one measurably fails; the objective's
    # feasibility gate — vacuous before gb_v1_2 — genuinely admits one and rejects the other.
    lib = _lib()
    covenant = float(lib.get("dscr_covenants", "min_p90_dscr").value)
    objective = SizingObjective(min_p90_dscr_covenant=covenant)

    strong = run_financial_model(_site(), _result(revenue=150000.0), lib, horizon_years=15)
    weak = run_financial_model(_site(), _result(revenue=40000.0), lib, horizon_years=15)
    assert strong.dscr.available and weak.dscr.available
    assert strong.dscr.min_dscr is not None and strong.dscr.min_dscr >= covenant
    assert weak.dscr.min_dscr is not None and weak.dscr.min_dscr < covenant

    candidate = SizingCandidate(power_kw=500.0, duration_hours=2.0)
    assert objective.feasible(_Eval(candidate, strong.dscr.min_dscr)) is True
    assert objective.feasible(_Eval(candidate, weak.dscr.min_dscr)) is False
    # And the covenant verdict on the weak case itself is an explicit fail, not "unavailable".
    assert weak.case.covenant_status.passes is False


def test_gb_v1_2_placeholder_debt_is_never_bankable() -> None:
    # The gate can fire, but nothing on gb_v1_2 may present as bankable: the gearing, credit
    # policy and covenant are all labelled placeholders.
    out = run_financial_model(_site(), _result(revenue=150000.0), _lib(), horizon_years=15)
    assert out.case.covenant_status.passes is True  # the maths passes...
    assert out.bankable is False  # ...but the case never claims bankable
    assert any("debt_gearing_pct" in w and "placeholder" in w for w in out.case.warnings)


# --- degradation charged once + net levy convention (D36) ---------------------


def test_degradation_is_charged_once_no_proxy_or_marginal_cost_lines() -> None:
    """D36: the capacity fade is charged ONCE - as the revenue decay the P50/P90 series apply,
    with the warranty throughput cap as the hard cycling limit. The former replacement-proxy
    (fade% x capex) and per-MWh wear lines are gone from the cost dict entirely (they charged
    the same physical fade a second and third time under the no-augmentation convention)."""
    model = build_cashflow_model(
        _site(),
        _result(revenue=100000.0),
        _lib("gb_v1_3"),
        horizon_years=10,
        dispatch_energy=DispatchEnergy(annual_charge_kwh=100000.0, annual_discharge_kwh=88000.0),
    )
    assert "degradation_warranty_proxy" not in model.annual_cost_by_line_gbp
    assert "degradation_marginal_cost" not in model.annual_cost_by_line_gbp
    # The fade still bites exactly once: revenue decays year on year.
    assert model.p50_ebitda_gbp[1] < model.p50_ebitda_gbp[0]


def test_levies_price_the_net_import_increase_never_gross_charge() -> None:
    """D36: on the no-export floor a discharge displaces levied site import, so the battery's
    levy cost is levy x (charge - discharge) - the round-trip losses - matching the symmetric
    convention of the dispatch objective and the customer bill. Never levy x gross charge."""
    levies = {"RO": 0.025, "CfD": 0.008}
    model = build_cashflow_model(
        _site(levies=levies),
        _result(revenue=100000.0),
        _lib("gb_v1_3"),
        horizon_years=10,
        dispatch_energy=DispatchEnergy(annual_charge_kwh=157000.0, annual_discharge_kwh=138000.0),
    )
    rate = sum(levies.values())
    expected = round(rate * (157000.0 - 138000.0), 2)
    assert model.annual_cost_by_line_gbp["levies_on_charge"] == expected
    assert model.annual_cost_by_line_gbp["levies_on_charge"] < rate * 157000.0  # not gross


def test_levies_net_derives_the_missing_volume_via_rte() -> None:
    """Only one volume known: the other derives via the RTE convention, so the net (losses)
    levy cost is still computable - and clamped at zero, never negative."""
    levies = {"RO": 0.03}
    model = build_cashflow_model(
        _site(levies=levies),
        _result(revenue=100000.0),
        _lib("gb_v1_3"),
        horizon_years=10,
        dispatch_energy=DispatchEnergy(annual_discharge_kwh=88000.0),
    )
    line = model.annual_cost_by_line_gbp["levies_on_charge"]
    assert line is not None and line > 0  # losses exist because RTE < 1
    rte = float(_lib("gb_v1_3").get("round_trip_efficiency", "ac_round_trip").value)
    expected = round(0.03 * (88000.0 / rte - 88000.0), 2)
    assert line == expected


def _strip_parasitic(lib: AssumptionLibrary) -> AssumptionLibrary:
    copy = lib.model_copy(deep=True)
    copy.battery_opex.pop("parasitic_load_fraction_of_power", None)
    return copy


# --- parasitic (auxiliary) load cost: release-gated (D33 pattern, dispatch half in the MILP) --


def test_parasitic_load_line_absent_without_the_key() -> None:
    # gb_v1_0 carries no parasitic key: the line stays unavailable (None, not zero) and no
    # parasitic warning appears — old releases behave exactly as before.
    model = build_cashflow_model(
        _site(),
        _result(revenue=100000.0),
        _lib("gb_v1_0"),
        horizon_years=10,
        dispatch_energy=DispatchEnergy(annual_discharge_kwh=400000.0),
    )
    assert model.annual_cost_by_line_gbp["parasitic_load"] is None
    assert not any("parasitic_load" in w for w in model.warnings)


def test_parasitic_load_prices_fraction_of_power_at_the_blended_import_rate() -> None:
    lib = _lib()
    fraction = float(lib.get("battery_opex", "parasitic_load_fraction_of_power").value)
    power_kw = 500.0
    with_key = build_cashflow_model(
        _site(),
        _result(revenue=100000.0, power_kw=power_kw),
        lib,
        horizon_years=10,
        dispatch_energy=DispatchEnergy(annual_discharge_kwh=400000.0),
    )
    without_key = build_cashflow_model(
        _site(),
        _result(revenue=100000.0, power_kw=power_kw),
        _strip_parasitic(_lib()),
        horizon_years=10,
        dispatch_energy=DispatchEnergy(annual_discharge_kwh=400000.0),
    )
    # The site's single import rate IS the blended rate; draw is continuous over 8760 h.
    expected = 0.24 * (fraction * power_kw * 8760.0)
    assert with_key.annual_cost_by_line_gbp["parasitic_load"] == pytest.approx(expected)
    # Total opex rises by EXACTLY the auxiliary energy cost - nothing else moves.
    assert (
        with_key.total_annual_operating_cost_gbp - without_key.total_annual_operating_cost_gbp
    ) == pytest.approx(expected)
    # Applied from a placeholder and disclosed as a blended-rate approximation.
    assert any(
        "parasitic_load_fraction_of_power" in w and "placeholder" in w for w in with_key.warnings
    )
    assert any("blended-rate approximation" in w for w in with_key.warnings)
    assert with_key.uses_unconfirmed_assumptions is True


def test_parasitic_load_unavailable_without_import_rates() -> None:
    site = _site()
    site = site.model_copy(
        update={
            "tariff_stack": s.TariffStack(
                basis=SYNTHETIC_BASIS,
                provenance=SYNTHETIC_PROVENANCE,
                supplier="X",
                import_rates={},
                levies={},
            )
        }
    )
    model = build_cashflow_model(
        site,
        _result(revenue=100000.0),
        _lib(),
        horizon_years=10,
        dispatch_energy=DispatchEnergy(annual_discharge_kwh=400000.0),
    )
    assert model.annual_cost_by_line_gbp["parasitic_load"] is None
    assert any("parasitic_load" in w and "omitted, not estimated" in w for w in model.warnings)


# --- C025: the capex energy component (gb_v1_3) ------------------------------


def test_gb_v1_3_capex_decomposition_reconciles_to_the_sourced_anchor() -> None:
    """C025: on gb_v1_3, capex = power component x MW + GBP/kWh x kWh, and the decomposition
    reconciles EXACTLY to the sourced all-in anchor at the 2-hour reference; added duration
    carries real marginal capital cost (the band-edge artifact is gone)."""
    from gridq.finance.cashflows import estimate_capex

    lib = load_assumptions("gb_v1_3")
    c2h = estimate_capex(1.0, lib, warnings=[], used=[], energy_kwh=2000.0)
    c4h = estimate_capex(1.0, lib, warnings=[], used=[], energy_kwh=4000.0)
    assert c2h.total_gbp == 500000.0  # 150,000 + 2,000 x 175 == the Neura-plan all-in at 2h
    assert c2h.per_kwh_gbp == 175.0 and c2h.power_component_per_mw_gbp == 150000.0
    assert c4h.total_gbp == 850000.0  # duration is no longer capital-free
    assert c4h.total_gbp > c2h.total_gbp


def test_releases_without_the_energy_key_price_capex_byte_identically() -> None:
    """C025 release gating: gb_v1_2 (no energy key) ignores a supplied energy and prices the
    legacy all-in per-MW shape exactly as before."""
    from gridq.finance.cashflows import estimate_capex

    lib = load_assumptions("gb_v1_2")
    with_energy = estimate_capex(1.0, lib, warnings=[], used=[], energy_kwh=4000.0)
    without = estimate_capex(1.0, lib, warnings=[], used=[])
    assert with_energy.total_gbp == without.total_gbp == 500000.0
    assert with_energy.per_kwh_gbp is None


def test_gb_v1_3_capex_without_energy_falls_back_with_a_warning() -> None:
    """C025 honesty: the decomposed shape without a sized energy falls back to the all-in
    per-MW figure and says so - never silently mispriced."""
    from gridq.finance.cashflows import estimate_capex

    lib = load_assumptions("gb_v1_3")
    warnings: list[str] = []
    est = estimate_capex(1.0, lib, warnings=warnings, used=[], energy_kwh=None)
    assert est.total_gbp == 500000.0 and est.per_kwh_gbp is None
    assert any("fell back to the all-in per-MW" in w for w in warnings)


# --- W8-2 (adoption review): P90 <= P50 element-wise, worst-composition sweep ---


def test_p90_never_exceeds_p50_element_wise_across_firmness_compositions() -> None:
    """The one ordering a lender relies on, pinned element-wise across the compositions the
    adoption review flagged (W8-1/W8-2): pure FIRM, mixed FIRM+CONTRACTED, and the inversion
    trigger - a schedule-derived stream tagged CONTRACTED (the perfect-foresight deduction
    keys on stream id but comes off the FIRM base; the clamp keeps the ordering)."""

    def mixed(firmness_map: dict[s.MarketStream, s.Firmness]) -> s.OptimisationResult:
        streams = [
            s.ValueStreamResult(stream=stream, annual_revenue_gbp=60000.0, firmness=firm)
            for stream, firm in firmness_map.items()
        ]
        return s.OptimisationResult(
            result_id="res_w82",
            request_id="req_w82",
            implementation_level=s.ImplementationLevel.IN_PROGRESS,
            bankable_status=s.BankableStatus.REVIEW_REQUIRED,
            selected_power_kw=500.0,
            selected_energy_kwh=1000.0,
            duration_hours=2.0,
            value_streams=streams,
            solver_status=s.SolverStatus.MOCK,
        )

    compositions = [
        {s.MarketStream.TARIFF_ARBITRAGE: s.Firmness.FIRM},
        {
            s.MarketStream.TARIFF_ARBITRAGE: s.Firmness.FIRM,
            s.MarketStream.CAPACITY_MARKET: s.Firmness.CONTRACTED,
        },
        # The W8-1 inversion trigger: a foresight-deducted stream moved OUT of the FIRM base.
        {
            s.MarketStream.TARIFF_ARBITRAGE: s.Firmness.CONTRACTED,
            s.MarketStream.CAPACITY_MARKET: s.Firmness.CONTRACTED,
        },
    ]
    for release in ("gb_v1_2", "gb_v1_3"):
        lib = load_assumptions(release)
        for firmness_map in compositions:
            model = build_cashflow_model(_site(), mixed(firmness_map), lib, horizon_years=15)
            for p90, p50 in zip(model.p90_ebitda_gbp, model.p50_ebitda_gbp, strict=True):
                assert p90 <= p50 + 1e-9, (release, firmness_map)
            for p90, p50 in zip(
                model.p90_fcf_pre_debt_gbp, model.p50_fcf_pre_debt_gbp, strict=True
            ):
                assert p90 <= p50 + 1e-9, (release, firmness_map)
