"""N1 Tier A, second half: the covenant table itself now says PRE-TAX (2026-08-13).

The requirement was explicit (`docs/reviews/2026-08-10-wp0-review-completion.md`, N1 row): *stamp
PRE-TAX on `DscrSummary` AND `FinancialCase`.* Only the `FinancialCase` half had landed. The long
`cashflows.PRE_TAX_CFADS_BASIS` disclosure reached `FinancialCase.warnings`, but `DscrSummary.basis`
still read a bare ``"P90"`` — and `outputs/underwriting.py` prints exactly that field as the
``Basis`` row of the covenant table, immediately beside the ratio and the pass/fail verdict.

A lender reads the covenant table first. So the single number that most needed the label was the one
carrying none, while the label sat in a warnings list further down the pack.

What the label is about: there is no corporation tax line, tax rate, capital allowance or
taxable-profit computation anywhere in the tree. The pre-tax EBITDA series is renamed CFADS at the
debt boundary and tested against a lender's POST-tax covenant, so coverage is overstated by an
unquantified, BACK-LOADED amount: capital allowances push the charge into the late years, which are
also the binding DSCR years. The tax layer itself is Tier B (it needs the SPV structure, the
capital-allowance basis, WDA timing and loss carry-forward treatment) and is deliberately NOT built
here — a label is not a fix, and this module tests only that the label travels.
"""

from __future__ import annotations

from datetime import date

import pytest

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq import schemas as s
from gridq.assumptions import load_assumptions
from gridq.finance import DebtTerms, DispatchEnergy, DscrResult, run_financial_model
from gridq.finance.cashflows import PRE_TAX_CFADS_BASIS
from gridq.finance.debt import DSCR_BASIS_P90_PRE_TAX, assess_debt

_HORIZON = 15
_TERMS = DebtTerms(gearing_pct=50.0, annual_rate_pct=7.0, tenor_years=10)
_DISPATCH = DispatchEnergy(annual_charge_kwh=600_000.0, annual_discharge_kwh=516_000.0)


def _site() -> s.SiteModel:
    return s.SiteModel(
        site_id="site_basis",
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
            import_rates={"day": 0.2431, "night": 0.1187},
            levies={"cclevy": 0.00775},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


def _result() -> s.OptimisationResult:
    return s.OptimisationResult(
        result_id="res_basis",
        request_id="req_basis",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=1_000.0,
        selected_energy_kwh=2_000.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=220_000.0,
                annual_cost_gbp=12_000.0,
                firmness=s.Firmness.FIRM,
            ),
            s.ValueStreamResult(
                stream=s.MarketStream.CAPACITY_MARKET,
                annual_revenue_gbp=45_000.0,
                firmness=s.Firmness.CONTRACTED,
            ),
        ],
        solver_status=s.SolverStatus.BASELINE,
    )


def _run(debt_terms: DebtTerms | None = _TERMS):  # type: ignore[no-untyped-def]
    return run_financial_model(
        _site(),
        _result(),
        load_assumptions("gb_v1_6"),
        horizon_years=_HORIZON,
        debt_terms=debt_terms,
        dispatch_energy=_DISPATCH,
    )


# --- the stamp itself -----------------------------------------------------


def test_the_dscr_basis_names_both_the_series_and_the_tax_basis() -> None:
    """It must still say WHICH series (a reader needs P90 vs P50) and now also on WHAT tax basis."""
    assert "P90" in DSCR_BASIS_P90_PRE_TAX
    assert "PRE-TAX" in DSCR_BASIS_P90_PRE_TAX
    assert DSCR_BASIS_P90_PRE_TAX != "P90", "the bare label is the defect N1 Tier A named"


def test_the_dscr_result_default_carries_the_stamp() -> None:
    """Defaulted rather than passed, so no construction path can produce an unlabelled ratio."""
    assert DscrResult(available=False).basis == DSCR_BASIS_P90_PRE_TAX


def test_every_dscr_path_carries_the_stamp_measured_or_unavailable() -> None:
    """A ratio that could not be measured still has a basis: the pack shows the covenant
    either way."""
    measured, _ = assess_debt(
        500_000.0, [80_000.0] * _HORIZON, load_assumptions("gb_v1_6"), debt_terms=_TERMS
    )
    absent, _ = assess_debt(
        500_000.0, [80_000.0] * _HORIZON, load_assumptions("gb_v1_6"), debt_terms=None
    )
    assert measured.available is True
    assert absent.available is False
    assert measured.basis == absent.basis == DSCR_BASIS_P90_PRE_TAX


def test_the_stamp_reaches_the_dscr_summary_on_a_real_run() -> None:
    """The seam that had not landed: `financial_model` copies `dscr.basis` onto `DscrSummary`."""
    case = _run().case
    assert case.dscr_summary.basis == DSCR_BASIS_P90_PRE_TAX
    assert case.dscr_summary.min_dscr is not None, "fixture must actually measure a ratio"


def test_the_stamp_reaches_the_dscr_summary_even_with_no_debt_structure() -> None:
    case = _run(debt_terms=None).case
    assert case.dscr_summary.basis == DSCR_BASIS_P90_PRE_TAX


# --- both halves of Tier A, together --------------------------------------


def test_the_long_disclosure_still_travels_alongside_the_short_stamp() -> None:
    """The stamp is terse because `outputs/` interpolates it mid-sentence; the full explanation
    (direction, magnitude, and what unblocks the real fix) must still be on the case."""
    case = _run().case
    assert case.dscr_summary.basis == DSCR_BASIS_P90_PRE_TAX
    w = next(x for x in case.warnings if x.startswith("BASIS — PRE-TAX"))
    assert PRE_TAX_CFADS_BASIS in w
    assert "post-tax covenant" in w
    assert "BACK-LOADED" in w and "binding DSCR years" in w
    assert "not invented here" in w


def test_no_tax_line_was_added_anywhere(  # noqa: D103 - name states the assertion
) -> None:
    """Tier B stays Tier B. A label is not a fix and must not be mistaken for one."""
    model = _run()
    assert not any("tax" in k.lower() for k in model.cashflows.annual_cost_by_line_gbp)
    assert not any("tax" in ref.lower() for ref in model.cashflows.assumptions_used)


# --- it must survive rendering into the artifact a lender reads -----------


def test_the_stamp_is_prose_safe_where_outputs_interpolate_it_mid_sentence() -> None:
    """`outputs/consulting_report.py` writes "The minimum {basis} DSCR on the record is …" and
    "Minimum DSCR ({basis} basis)". A paragraph in this field would wreck that copy, in files this
    module does not own — hence the terse stamp. Pinned so nobody lengthens it by accident."""
    assert len(DSCR_BASIS_P90_PRE_TAX) <= 24, DSCR_BASIS_P90_PRE_TAX
    assert "\n" not in DSCR_BASIS_P90_PRE_TAX
    assert "|" not in DSCR_BASIS_P90_PRE_TAX, "it is printed into a markdown table cell"
    for sentence in (
        f"The minimum {DSCR_BASIS_P90_PRE_TAX} DSCR on the record is 2.20.",
        f"Minimum DSCR ({DSCR_BASIS_P90_PRE_TAX} basis)",
        f"Basis {DSCR_BASIS_P90_PRE_TAX}. Covenant passes.",
    ):
        assert "  " not in sentence


def test_the_stamp_carries_no_em_dash() -> None:
    """`tests/test_outputs_no_em_dashes.py` sweeps the RENDERED pack, and this is record-authored
    text printed verbatim into it — an em dash here fails that sweep from outside its package."""
    assert "—" not in DSCR_BASIS_P90_PRE_TAX


def test_the_covenant_table_in_the_rendered_pack_now_shows_pre_tax(rich_quote) -> None:  # type: ignore[no-untyped-def]
    """The whole point: the row a lender reads first. Rendered, not asserted on the field.

    Uses the shared populated fixture, which builds its case through `run_financial_model` and
    assembles a real `QuoteRecord` — so this is the actual pack a reader would receive.
    """
    from gridq.outputs import build_view_model, render_underwriting_memo

    assert rich_quote.case.dscr_summary.basis == DSCR_BASIS_P90_PRE_TAX
    pack = render_underwriting_memo(build_view_model(rich_quote.quote, rich_quote.site))
    assert f"| Basis | {DSCR_BASIS_P90_PRE_TAX} |" in pack
    covenant_section = pack.split("## DSCR & covenant")[1].split("\n## ")[0]
    assert "PRE-TAX" in covenant_section, (
        "the covenant table a lender reads first still carries no tax basis:\n" + covenant_section
    )
    # It sits beside the ratio and the verdict, not in a warnings list further down.
    assert "Minimum DSCR" in covenant_section and "Covenant verdict" in covenant_section


def test_the_stamp_did_not_disturb_the_measurement() -> None:
    """A labelling change must move no computed number (the WP-0 truth-in-labelling rule).

    The expected ratio is built from the annuity closed form here, NOT copied off the model under
    test: level payment = P x r / (1 - (1+r)^-n) on a 50%-geared 500k capex at 7% over 10 years,
    against a flat 80k CFADS. If the stamp had perturbed the arithmetic, this catches it against
    external maths rather than against the code's own answer.
    """
    principal = 500_000.0 * 0.50
    rate, tenor = 0.07, 10
    payment = principal * rate / (1.0 - (1.0 + rate) ** -tenor)
    expected = round(80_000.0 / payment, 4)

    dscr, _ = assess_debt(
        500_000.0, [80_000.0] * _HORIZON, load_assumptions("gb_v1_6"), debt_terms=_TERMS
    )
    assert dscr.min_dscr == pytest.approx(expected, abs=1e-4)
    assert dscr.by_year == [pytest.approx(expected, abs=1e-4)] * tenor
    assert dscr.basis == DSCR_BASIS_P90_PRE_TAX
