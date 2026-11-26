"""H-17 cost side, and a bias guard that can actually see a cost-line change (2026-08-13).

Two findings, one root cause: a guard that reads the model's own reported figures cannot detect a
change to those figures.

**1. The bias guard was structurally blind.**
`tests/test_finance_tenor_and_availability.py::_legacy_series` rebuilds the pre-change year loop
from ``model.annual_cost_by_line_gbp`` and ``model.annual_dispatch_cost_gbp`` — the outputs of the
model under test. Its docstring says that keeps it from drifting with the fixture; what it actually
does is make the guard invariant to every cost-line change, so the test's headline claim ("no
evidence supplied ⇒ not one penny moves") is **false as written**. The N26 parasitic-levy correction
moved P50, P90 and both FCF series on every levied site, and a model-sourced guard reproduces the
moved series exactly. `test_a_model_sourced_guard_cannot_see_a_cost_line_change` demonstrates that
directly rather than asserting it. This module pins the cost lines and both series as LITERALS
derived from the library values and the site's own tariff and computed here, so a cost-line change
fails here by construction.

**2. Availability de-rated revenue but not the variable cost of cycling.**
The H-17 de-rate multiplied every revenue line by the fleet availability factor while charging 100%
of every cost, justified as "an unavailable battery still costs its fixed opex". True of the fixed
lines, false of the volumetric ones: the dispatch charge-energy cost and the levy on net charge
volume are both produced by a MILP that assumed the unit was available all year, so a 95%-available
unit cycles ~5% less, buys ~5% less charging energy and pays ~5% less levy on it. The tell was that
the EBITDA delta from switching the de-rate on stayed **constant** whatever the dispatch cost, which
is only possible if the cost side is not participating. Conservative and disclosed, but an
over-conservative constraint is still a wrong number, and two fixes in this module have already had
to be corrected for erring opposite to the defect they addressed.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq import schemas as s
from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.finance.cashflows import CashflowModel, build_cashflow_model

_HORIZON = 15

# --- INDEPENDENT literals. Every one is computed here from the library value and the site's own
# tariff, never read off the model under test. That is the whole point of this module: these are the
# numbers a cost-line change has to move, and pinning them is what makes the guard sighted.

_POWER_MW = 1.0
_ENERGY_KWH = 2_000.0
#: gb_v1_6 battery_capex: 150_000/MW power component + 175/kWh energy component.
_CAPEX = 150_000.0 * _POWER_MW + 175.0 * _ENERGY_KWH  # 500_000
#: The site's own tariff, transcribed (NOT read from the SiteModel the test builds).
_IMPORT_RATES = {"day": 0.2431, "night": 0.1187}
_LEVY_RATE = 0.00775
_MEAN_IMPORT = (0.2431 + 0.1187) / 2  # 0.1809
#: N26: auxiliary draw is levied grid import, so the blended rate INCLUDES the levy lines. Dropping
#: the levy term here is exactly the defect N26 fixed, and it changes the literal below.
_BLENDED_IMPORT = _MEAN_IMPORT + _LEVY_RATE  # 0.18865
#: gb_v1_6 battery_opex.parasitic_load_fraction_of_power = 0.01, continuous over 8760 h.
_AUX_KWH = 0.01 * (_POWER_MW * 1000.0) * 8760.0  # 87_600
_CHARGE_KWH, _DISCHARGE_KWH = 600_000.0, 516_000.0

_COST_LINES: dict[str, float | None] = {
    "opex": 12_000.0,  # battery_opex.annual_fixed_opex 12_000/MW
    "insurance": 2_500.0,  # insurance.annual_pct_of_capex 0.5% x 500_000
    "metering_idno": 1_200.0,  # metering_idno.annual_gbp_per_site
    "dno": 1_500.0,  # dno_costs.annual_gbp_per_site
    "business_rates": 8_000.0,  # business_rates.annual_gbp_per_mw 8_000/MW
    "levies_on_charge": round(_LEVY_RATE * (_CHARGE_KWH - _DISCHARGE_KWH), 2),  # 651.00
    # Time-windowed levies (the GB Capacity Market supplier obligation). `None` = UNAVAILABLE on
    # this fixture, because `_site()`'s tariff carries no `windowed_levies` — the honest
    # "omitted, not estimated" state, exactly like `parasitic_load` on a pre-gb_v1_2 release. Pinned
    # as a literal like every other line: if the line ever starts computing a figure on a site with
    # no windowed levy, THIS is where it fails.
    "windowed_levies_avoided": None,
    "parasitic_load": round(_BLENDED_IMPORT * _AUX_KWH, 2),  # 16_525.74
}
_OPERATING_COST = round(sum(v for v in _COST_LINES.values() if v is not None), 2)  # 42_376.74
#: The £/yr the N26 correction added on this site: levy rate x auxiliary kWh. A guard rebuilt from
#: the model's own cost lines cannot see this move; the literal above can.
_N26_LEVY_ON_PARASITIC = round(_LEVY_RATE * _AUX_KWH, 2)  # 678.90

_ARBITRAGE, _DUOS = 220_000.0, 45_000.0
_DISPATCH_COST = 12_000.0
_FADE_PCT = 2.5  # degradation.annual_capacity_fade_pct
_HAIRCUT_PCT = 15.0  # p50_p90_haircuts.p90_revenue_haircut_pct
_PF_PCT = 20.0  # p50_p90_haircuts.perfect_foresight_haircut_pct
#: Both priced streams are dispatch-optimised, so the whole revenue takes the foresight deduction.
_PF_ADJUSTMENT = round((_ARBITRAGE + _DUOS) * _PF_PCT / 100.0, 2)  # 53_000
_FIRM_BASE = _ARBITRAGE + _DUOS - _PF_ADJUSTMENT  # 212_000
_AVAILABILITY_PCT = 95.0  # gb_v1_7 / gb_v1_8 availability.fleet_availability_pct


def _expected_series(
    *,
    availability_pct: float | None = None,
    dispatch_cost: float = _DISPATCH_COST,
    cost_lines: dict[str, float | None] | None = None,
) -> dict[str, list[float]]:
    """The P50/P90 series from first principles: literals in, series out.

    Costs are the LITERAL lines above (or an explicit override), never the model's. The availability
    de-rate multiplies revenue and the VARIABLE cost of cycling (dispatch charge energy + the
    levy on
    net charge volume); every other line is charged in full.
    """
    lines = _COST_LINES if cost_lines is None else cost_lines
    # The UN-rounded sum of the (individually rounded) lines, in `COST_LINE_KEYS` order — exactly
    # what the year loop uses. Substituting the rounded `total_annual_operating_cost_gbp` here
    # differs by ~1e-12 and flips any year whose revenue lands on a half-penny boundary (year 3
    # does, at £116,925.885): a transcription artefact, not a behaviour difference.
    # `None` lines are UNAVAILABLE and contribute nothing, exactly as the year loop's own
    # `sum(v for v in cost_lines.values() if v is not None)` treats them.
    operating = sum(v for v in lines.values() if v is not None)
    factor = 1.0 if availability_pct is None else availability_pct / 100.0
    avoided = (dispatch_cost + (lines["levies_on_charge"] or 0.0)) * (1.0 - factor)
    p50, p90 = [], []
    for year in range(1, _HORIZON + 1):
        decay = (1.0 - _FADE_PCT / 100.0) ** (year - 1)
        p50_rev = _FIRM_BASE * decay * factor
        p90_rev = _FIRM_BASE * (1.0 - _HAIRCUT_PCT / 100.0) * decay * factor
        p50.append(round(p50_rev - dispatch_cost - operating + avoided, 2))
        p90.append(round(p90_rev - dispatch_cost - operating + avoided, 2))
    return {"p50": p50, "p90": p90}


# --- fixtures --------------------------------------------------------------


def _site() -> s.SiteModel:
    return s.SiteModel(
        site_id="site_cost_symmetry",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=s.LoadDataSummary(
            annual_kwh=1_800_000.0,
            half_hourly_rows=17520,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            max_import_kw=420.0,
        ),
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates=dict(_IMPORT_RATES),
            levies={"cclevy": _LEVY_RATE},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


def _result(dispatch_cost: float = _DISPATCH_COST) -> s.OptimisationResult:
    return s.OptimisationResult(
        result_id="res_cs",
        request_id="req_cs",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=_POWER_MW * 1000.0,
        selected_energy_kwh=_ENERGY_KWH,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=_ARBITRAGE,
                annual_cost_gbp=dispatch_cost,
                firmness=s.Firmness.FIRM,
            ),
            s.ValueStreamResult(
                stream=s.MarketStream.DUOS_RED_BAND_AVOIDANCE,
                annual_revenue_gbp=_DUOS,
                firmness=s.Firmness.FIRM,
            ),
        ],
        solver_status=s.SolverStatus.BASELINE,
    )


_DISPATCH_ENERGY_KW = {"annual_charge_kwh": _CHARGE_KWH, "annual_discharge_kwh": _DISCHARGE_KWH}


def _build(
    *,
    version: str = "gb_v1_6",
    dispatch_cost: float = _DISPATCH_COST,
    availability_off: bool = False,
    overrides: dict[str, Any] | None = None,
) -> CashflowModel:
    from gridq.finance.cashflows import DispatchEnergy

    dumped = load_assumptions(version).model_dump(mode="json")
    if availability_off:
        dumped["availability"] = {}
    for section, content in (overrides or {}).items():
        dumped[section] = {**dumped.get(section, {}), **content}
    return build_cashflow_model(
        _site(),
        _result(dispatch_cost),
        AssumptionLibrary.model_validate(dumped),
        horizon_years=_HORIZON,
        dispatch_energy=DispatchEnergy(**_DISPATCH_ENERGY_KW),
    )


def _assumption(value: Any, unit: str = "pct") -> dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "source": "TEST fixture — not a sourced figure",
        "status": "placeholder",
        "owner_or_reviewer": "test",
        "confidence": "low",
        "last_reviewed": "2026-01-01",
        "notes": "TEST fixture.",
    }


# --- 1. the sighted guard -------------------------------------------------


def test_the_cost_lines_are_pinned_as_literals_not_read_off_the_model() -> None:
    """Every line, from the library value and the site's own tariff. Reverting a cost-line fix (or
    landing an unintended one) fails HERE rather than sailing through a model-sourced rebuild."""
    model = _build()
    assert model.capex.total_gbp == _CAPEX
    assert model.annual_cost_by_line_gbp == _COST_LINES
    assert model.total_annual_operating_cost_gbp == _OPERATING_COST
    assert model.annual_dispatch_cost_gbp == _DISPATCH_COST
    assert model.perfect_foresight_adjustment_gbp == _PF_ADJUSTMENT


def test_the_n26_parasitic_levy_component_is_pinned_in_its_own_right() -> None:
    """The exact £ the N26 correction added: levy rate x auxiliary kWh, on a levied site.

    Pinned separately from the line total because this is the component `_legacy_series` cannot
    see.
    A revert to the pre-N26 convention (mean import rate only, levies dropped) lands the line at
    `_BLENDED_IMPORT - _LEVY_RATE` x aux and this fails by exactly `_N26_LEVY_ON_PARASITIC`.
    """
    parasitic = _build().annual_cost_by_line_gbp["parasitic_load"]
    assert parasitic == pytest.approx(_BLENDED_IMPORT * _AUX_KWH, abs=0.01)
    pre_n26 = round(_MEAN_IMPORT * _AUX_KWH, 2)
    assert parasitic is not None
    assert parasitic - pre_n26 == pytest.approx(_N26_LEVY_ON_PARASITIC, abs=0.01)
    assert _N26_LEVY_ON_PARASITIC > 0, "fixture must be a levied site for this to be measurable"


def test_both_series_are_pinned_against_arithmetic_computed_here() -> None:
    """P50 and P90, every year, from the literals. The claim the old guard could not support."""
    model = _build()
    expected = _expected_series()
    assert model.p50_ebitda_gbp == expected["p50"]
    assert model.p90_ebitda_gbp == expected["p90"]
    assert model.p50_fcf_pre_debt_gbp == [-_CAPEX, *expected["p50"]]
    assert model.p90_fcf_pre_debt_gbp == [-_CAPEX, *expected["p90"]]


def test_a_model_sourced_guard_cannot_see_a_cost_line_change() -> None:
    """The demonstration, not the assertion: why "not one penny moves" was false as written.

    Doubling the parasitic-load fraction moves P50, P90 and both FCF series by the full parasitic
    amount every year. A guard rebuilt from ``model.annual_cost_by_line_gbp`` reproduces the MOVED
    series exactly and passes; the literal-pinned guard above fails. This is the structural
    blindness,
    reproduced with a cost-line change instead of argued about.
    """
    base = _build()
    doubled = _build(
        overrides={
            "battery_opex": {"parasitic_load_fraction_of_power": _assumption(0.02, "fraction")}
        }
    )
    moved = doubled.annual_cost_by_line_gbp["parasitic_load"]
    assert moved is not None
    assert moved == pytest.approx(2 * _COST_LINES["parasitic_load"], abs=0.01)

    # The series really did move, in every year and in both cases.
    delta = _COST_LINES["parasitic_load"]
    for year in range(_HORIZON):
        assert base.p50_ebitda_gbp[year] - doubled.p50_ebitda_gbp[year] == pytest.approx(
            delta, abs=0.02
        )
        assert base.p90_ebitda_gbp[year] - doubled.p90_ebitda_gbp[year] == pytest.approx(
            delta, abs=0.02
        )

    # A guard sourced from the model's own reported lines is satisfied by BOTH, so it would have
    # reported "not one penny moved" for a change that moved every penny of that line.
    def model_sourced(model: CashflowModel) -> list[float]:
        operating = sum(v for v in model.annual_cost_by_line_gbp.values() if v is not None)
        firm = model.annual_revenue_firm_gbp - (model.perfect_foresight_adjustment_gbp or 0.0)
        return [
            round(
                firm * (1.0 - _FADE_PCT / 100.0) ** (y - 1)
                - model.annual_dispatch_cost_gbp
                - operating,
                2,
            )
            for y in range(1, model.horizon_years + 1)
        ]

    assert model_sourced(base) == base.p50_ebitda_gbp
    assert model_sourced(doubled) == doubled.p50_ebitda_gbp, (
        "the model-sourced guard is blind: it passes on the moved series too"
    )


# --- 2. availability now de-rates the variable cost of cycling ------------


def test_avoided_variable_cost_is_the_unavailable_share_of_the_volumetric_cost() -> None:
    model = _build(version="gb_v1_7")
    assert model.availability_factor_pct == _AVAILABILITY_PCT
    variable = _DISPATCH_COST + _COST_LINES["levies_on_charge"]
    expected = round(variable * (1.0 - _AVAILABILITY_PCT / 100.0), 2)
    assert model.availability_avoided_variable_cost_gbp == pytest.approx(expected, abs=0.01)
    assert model.p50_ebitda_gbp == _expected_series(availability_pct=_AVAILABILITY_PCT)["p50"]
    assert model.p90_ebitda_gbp == _expected_series(availability_pct=_AVAILABILITY_PCT)["p90"]


@pytest.mark.parametrize("dispatch_cost", [0.0, 12_000.0, 25_000.0])
def test_the_derate_delta_now_scales_with_the_dispatch_cost(dispatch_cost: float) -> None:
    """THE symptom of the defect: the delta used to be identical at £0, £12k and £25k of dispatch
    cost, which is only possible if the cost side is not participating in the de-rate at all."""
    derated = _build(version="gb_v1_7", dispatch_cost=dispatch_cost)
    full = _build(version="gb_v1_7", dispatch_cost=dispatch_cost, availability_off=True)
    unavailable = 1.0 - _AVAILABILITY_PCT / 100.0
    revenue_loss = _FIRM_BASE * unavailable
    cost_saved = (dispatch_cost + _COST_LINES["levies_on_charge"]) * unavailable
    assert derated.p50_ebitda_gbp[0] - full.p50_ebitda_gbp[0] == pytest.approx(
        cost_saved - revenue_loss, abs=0.02
    )


def test_the_delta_is_no_longer_constant_across_dispatch_costs() -> None:
    """Stated as its own regression: the constancy WAS the bug, so a future change that reintroduces
    it fails here even if the arithmetic above is refactored."""
    deltas = []
    for dispatch_cost in (0.0, 12_000.0, 25_000.0):
        derated = _build(version="gb_v1_7", dispatch_cost=dispatch_cost)
        full = _build(version="gb_v1_7", dispatch_cost=dispatch_cost, availability_off=True)
        deltas.append(round(derated.p50_ebitda_gbp[0] - full.p50_ebitda_gbp[0], 2))
    assert len(set(deltas)) == 3, f"the de-rate delta is still cost-blind: {deltas}"
    # And the de-rate is still a NET penalty at every dispatch cost — the fix trims an
    # over-charge, it does not turn unavailability into a benefit.
    assert all(d < 0 for d in deltas), deltas


def test_the_fixed_lines_are_still_charged_in_full() -> None:
    """The original justification holds for these, so the fix must not touch them: a continuous
    auxiliary draw and a contractual opex/insurance/rates bill do not shrink with uptime."""
    derated = _build(version="gb_v1_7")
    doubled_parasitic = _build(
        version="gb_v1_7",
        overrides={
            "battery_opex": {"parasitic_load_fraction_of_power": _assumption(0.02, "fraction")}
        },
    )
    # The whole extra parasitic cost lands on EBITDA, not 95% of it.
    assert derated.p50_ebitda_gbp[0] - doubled_parasitic.p50_ebitda_gbp[0] == pytest.approx(
        _COST_LINES["parasitic_load"], abs=0.02
    )


def test_the_reported_cost_figures_stay_raw_so_the_price_recommendation_is_untouched() -> None:
    """A named series adjustment, not a re-stated cost line. `recommend_price` reads
    `total_annual_operating_cost_gbp`, so de-rating the lines themselves would silently move the
    price recommendation as well as the returns."""
    derated = _build(version="gb_v1_7")
    full = _build(version="gb_v1_7", availability_off=True)
    assert derated.annual_dispatch_cost_gbp == full.annual_dispatch_cost_gbp == _DISPATCH_COST
    assert derated.annual_cost_by_line_gbp == full.annual_cost_by_line_gbp == _COST_LINES
    assert derated.total_annual_operating_cost_gbp == _OPERATING_COST


def test_the_cost_side_split_is_disclosed_with_its_arithmetic() -> None:
    model = _build(version="gb_v1_7")
    w = next(w for w in model.warnings if w.startswith("Availability also reduces the VARIABLE"))
    assert f"£{round(_DISPATCH_COST + _COST_LINES['levies_on_charge'], 2)}" in w
    assert "5% of the" in w
    assert "energy the unit does not cycle is energy it does not buy" in w
    assert "FIXED" in w and "parasitic" in w
    assert "stay RAW year-1" in w
    # The de-rate warning itself must no longer claim costs are held whole.
    derate = next(w for w in model.warnings if w.startswith("Revenue is de-rated for fleet"))
    assert "Operating and dispatch costs are NOT reduced" not in derate
    assert "SPLIT rather than held whole" in derate
    assert "over-conservative constraint is still a wrong number" in derate


# --- dormancy: nothing moves without the evidence ------------------------


@pytest.mark.parametrize("version", ["gb_v0_1", "gb_v1_0", "gb_v1_6"])
def test_no_availability_key_means_no_cost_adjustment_and_no_field(version: str) -> None:
    """The releases with no fleet-availability figure keep the exact pre-change series."""
    model = _build(version=version)
    assert model.availability_factor_pct is None
    assert model.availability_avoided_variable_cost_gbp is None
    assert not any("Availability also reduces the VARIABLE" in w for w in model.warnings)


def test_the_gb_v1_6_series_is_bit_identical_to_the_no_derate_arithmetic() -> None:
    """The dormancy guard, pinned against literals rather than against the model's own outputs: at
    `availability_factor == 1.0` the avoided term is exactly 0.0 and `x + 0.0 == x` in IEEE-754, so
    this is bit-identity and not approximate agreement."""
    model = _build(version="gb_v1_6")
    expected = _expected_series(availability_pct=None)
    assert model.p50_ebitda_gbp == expected["p50"]
    assert model.p90_ebitda_gbp == expected["p90"]


def test_a_derate_with_no_variable_cost_reports_zero_avoided_rather_than_none() -> None:
    """`None` means no de-rate at all; `0.0` means a de-rate with nothing volumetric to avoid. The
    distinction matters because `None` is the un-modelled data-ask state."""
    from gridq.finance.cashflows import DispatchEnergy

    dumped = load_assumptions("gb_v1_7").model_dump(mode="json")
    model = build_cashflow_model(
        _site(),
        _result(dispatch_cost=0.0),
        AssumptionLibrary.model_validate(dumped),
        horizon_years=_HORIZON,
        dispatch_energy=DispatchEnergy(annual_charge_kwh=0.0, annual_discharge_kwh=0.0),
    )
    assert model.annual_cost_by_line_gbp["levies_on_charge"] == 0.0
    assert model.availability_avoided_variable_cost_gbp == 0.0
    assert model.availability_factor_pct == _AVAILABILITY_PCT


def test_the_availability_cost_split_is_deterministic() -> None:
    first, second = _build(version="gb_v1_7"), _build(version="gb_v1_7")
    assert first.p50_ebitda_gbp == second.p50_ebitda_gbp
    assert first.p90_ebitda_gbp == second.p90_ebitda_gbp
    assert (
        first.availability_avoided_variable_cost_gbp
        == second.availability_avoided_variable_cost_gbp
    )
    assert first.warnings == second.warnings
