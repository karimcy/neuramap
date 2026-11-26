"""The three declared bases the underwriting pack never stated (register C-7: N1, N13/H-15, N14).

WP-0 §7 item 1 required these labels and the handoff dropped them; the 2026-08-12 register
confirmed all three ABSENT. They are truth-in-labelling only — none of them changes a computed
number — so what these tests assert is that the disclosure exists, says the right thing, travels
with the object that carries the series, and reaches `FinancialCase.warnings`.

What is deliberately NOT here: a tax layer, an escalation index, a residual value. All three are
Tier B (they need a human input — SPV structure, the index, the residual-value basis) and inventing
any of them on the pricing path is forbidden.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.finance import run_financial_model
from gridq.finance.cashflows import (
    FLAT_NOMINAL_UNESCALATED_BASIS,
    OMITTED_CAPITAL_FLOWS,
    PRE_TAX_CFADS_BASIS,
    build_cashflow_model,
)

# --- helpers ---------------------------------------------------------------


def _result(
    streams: list[tuple[s.MarketStream, float, s.Firmness]] | None = None,
) -> s.OptimisationResult:
    streams = streams or [(s.MarketStream.TARIFF_ARBITRAGE, 48_000.0, s.Firmness.FIRM)]
    return s.OptimisationResult(
        result_id="res_bases",
        request_id="req_bases",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=1_000.0,
        selected_energy_kwh=2_000.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(stream=st, annual_revenue_gbp=rev, firmness=firm)
            for st, rev, firm in streams
        ],
        solver_status=s.SolverStatus.BASELINE,
    )


def _site() -> s.SiteModel:
    load = s.LoadDataSummary(
        annual_kwh=1_800_000.0,
        half_hourly_rows=17520,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        max_import_kw=420.0,
    )
    return s.SiteModel(
        site_id="site_bases",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=load,
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"day": 0.2431, "night": 0.1187},
            levies={"cclevy": 0.00775},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


# --- N1: the pre-tax basis -------------------------------------------------


def test_the_pre_tax_basis_rides_on_the_model_that_carries_the_series() -> None:
    """N1: `p50_fcf_pre_debt_gbp` must never reach `assess_debt` unlabelled.

    The label lives on the same object as the series, so a consumer that has the cashflows has the
    basis — it cannot be dropped in transit between finance modules.
    """
    model = build_cashflow_model(_site(), _result(), load_assumptions("gb_v1_6"))
    assert model.cfads_basis == PRE_TAX_CFADS_BASIS
    # The exact sentence WP-0 §7 item 1 specified, verbatim.
    assert model.cfads_basis == (
        "PRE-TAX — no corporation tax is modelled; this ratio is not comparable to a post-tax "
        "covenant"
    )


def test_the_pre_tax_omission_is_warned_with_its_direction_and_its_blocker() -> None:
    """A label alone is not a disclosure: the warning has to say which way the error runs."""
    model = build_cashflow_model(_site(), _result(), load_assumptions("gb_v1_6"))
    w = next(x for x in model.warnings if x.startswith("BASIS — PRE-TAX"))
    assert "post-tax covenant" in w
    # The error is back-loaded (capital allowances defer the charge into the binding DSCR years) —
    # asserting this stops the warning being softened to "overstated by the tax rate", which the
    # review's own panel correction rejected.
    assert "BACK-LOADED" in w
    assert "capital allowance" in w
    # And it must name the human input, so nobody "fixes" it by inventing a tax rate.
    assert "SPV structure" in w and "not invented here" in w


# --- N13 / H-15: the escalation basis --------------------------------------


def test_the_flat_nominal_unescalated_basis_is_declared() -> None:
    model = build_cashflow_model(_site(), _result(), load_assumptions("gb_v1_6"))
    assert model.cashflow_basis == FLAT_NOMINAL_UNESCALATED_BASIS
    assert "FLAT-NOMINAL, UN-ESCALATED" in model.cashflow_basis
    assert "no CPI/RPI index" in model.cashflow_basis


def test_the_no_escalation_warning_states_the_magnitude_and_that_it_does_not_cancel() -> None:
    """N13's substance: nominal rates on flat cashflows, and the two sides do NOT net out."""
    model = build_cashflow_model(_site(), _result(), load_assumptions("gb_v1_6"))
    w = next(x for x in model.warnings if x.startswith("BASIS — FLAT-NOMINAL"))
    assert "do not cancel" in w
    assert "41%" in w and "2.5% CPI" in w  # the year-15 opex understatement
    assert "binds the DSCR" in w
    assert "no escalation rate is invented here" in w


def test_the_cm_cpi_limb_is_named_only_when_contracted_revenue_is_priced() -> None:
    """D-33's second limb (a CM T-4 agreement is CPI-linked and is modelled flat) rides here.

    It is Tier-B-blocked on the same missing index as N13, so it is disclosed rather than fixed —
    but only on a run that actually prices a contracted stream, so a merchant-only case is not
    given an irrelevant warning.
    """
    lib = load_assumptions("gb_v1_6")
    merchant = build_cashflow_model(_site(), _result(), lib)
    with_cm = build_cashflow_model(
        _site(),
        _result(
            [
                (s.MarketStream.TARIFF_ARBITRAGE, 40_000.0, s.Firmness.FIRM),
                (s.MarketStream.CAPACITY_MARKET, 9_000.0, s.Firmness.CONTRACTED),
            ]
        ),
        lib,
    )
    assert not any("CPI-linked" in x for x in merchant.warnings)
    assert any("CPI-linked" in x and "modelled flat" in x for x in with_cm.warnings)


# --- N14: the omitted capital flows ---------------------------------------


def test_all_three_omitted_capital_flows_are_named_individually() -> None:
    """N14: the warning must name BOTH omissions the register asked for, plus replacement capex.

    The panel correction conceded that the no-augmentation convention disposes of *augmentation*
    capex — it does not dispose of residual value, decommissioning or inverter/BOP replacement.
    """
    model = build_cashflow_model(_site(), _result(), load_assumptions("gb_v1_6"))
    assert model.omitted_capital_flows == OMITTED_CAPITAL_FLOWS
    w = next(x for x in model.warnings if x.startswith("OMITTED from the free-cashflow series"))
    assert "terminal/residual value" in w
    assert "decommissioning" in w
    assert "inverter and balance-of-plant replacement" in w
    # No residual value is chosen here (Tier B) and the net direction is stated as unquantified,
    # not as "small" — two omissions flatter the case and one penalises it.
    assert "unquantified — not small, unquantified" in w
    assert "neither a terminal value nor a replacement cost is invented here" in w


def test_the_fcf_series_still_carries_exactly_one_capital_flow() -> None:
    """The label is a disclosure, NOT a terminal flow: N14's fix stays Tier B.

    If someone later appends a residual value, this test fails and forces the assumption-library
    change to be made deliberately rather than smuggled in behind the warning.
    """
    model = build_cashflow_model(_site(), _result(), load_assumptions("gb_v1_6"))
    assert len(model.p50_fcf_pre_debt_gbp) == model.horizon_years + 1
    assert model.p50_fcf_pre_debt_gbp[0] == -model.capex.total_gbp
    assert model.p50_fcf_pre_debt_gbp[1:] == model.p50_ebitda_gbp
    assert model.p90_fcf_pre_debt_gbp[1:] == model.p90_ebitda_gbp


# --- the bases must reach the pack ----------------------------------------


def test_all_three_bases_reach_the_financial_case_warnings(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """C-7's actual requirement: the pack states that it is pre-tax, un-escalated and TV-free.

    `run_financial_model` extends its warnings with the cashflow model's, so a `FinancialCase`
    cannot be produced, read or rendered without all three disclosures on it.
    """
    case = run_financial_model(make_site(), _result(), load_assumptions("gb_v1_6")).case
    starts = ("BASIS — PRE-TAX", "BASIS — FLAT-NOMINAL", "OMITTED from the free-cashflow series")
    for prefix in starts:
        assert any(w.startswith(prefix) for w in case.warnings), f"missing disclosure: {prefix}"


def test_every_release_gets_the_bases_including_the_frozen_ones() -> None:
    """The omissions are structural, not release-dependent: no library can switch them off."""
    for version in ("gb_v0_1", "gb_v1_0", "gb_v1_6"):
        model = build_cashflow_model(_site(), _result(), load_assumptions(version))
        assert model.cfads_basis == PRE_TAX_CFADS_BASIS
        assert model.cashflow_basis == FLAT_NOMINAL_UNESCALATED_BASIS
        assert model.omitted_capital_flows == OMITTED_CAPITAL_FLOWS
        assert sum(w.startswith(("BASIS — ", "OMITTED from")) for w in model.warnings) == 3
