"""Per-stream CONTRACTED P90 haircuts *inside* the award term (D-139 / D-1410 / E-06).

The post-expiry half of D-130 has been live since 2026-08-13: `_NO_P90_AFTER_EXPIRY` forces
LOCAL_FLEX_PICLO and DFS P90 revenue to zero once the award has run out. The IN-TERM half was not.
While the award was live, every CONTRACTED stream shared ONE scalar — and because no assumption
release carries `p50_p90_haircuts.p90_revenue_haircut_contracted_pct` (gb_v1_9 adds it as a declared
null with the failed search recorded on the key), that scalar is in practice the MERCHANT 15%
fallback. So:

  * a Capacity Market availability payment, a regulated fee with statutory penalties, was haircut
    15% — CONSERVATIVE, and left alone here; and
  * a DNO flexibility award terminable on 90 days' notice by either party (ENA Standard Agreement
    §7.4) was ALSO haircut only 15%, paying 85% of it into the P90 and therefore into the DSCR —
    against D-139, which requires that line's P90 "at or near zero" and says it must "never carry
    the DSCR".

These tests pin the number, per stream, on both sides of the split: the flex/DFS in-term P90
contribution is now ZERO, and a CM (or LOAD_SERVICE) payment still takes the class-wide haircut. The
delta collapses to zero if `_NO_P90_IN_TERM` is emptied, which is the revert.

Everything goes through `run_financial_model` — the entry point `quotes/pipeline.py:469` uses — for
the same reason `test_finance_tenor_threading.py` does: this repo has shipped built-tested-never-
called ten times, and the tenor that makes "in term" meaningful is assembled at
`financial_model.py:107` and passed at `:118`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq import schemas as s
from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.finance import DebtTerms, DispatchEnergy, run_financial_model
from gridq.finance.cashflows import (
    _NO_P90_IN_TERM,
    _NO_P90_IN_TERM_REASON,
    _stream_contracted_haircut_pct,
)

_HORIZON = 15
_CM = s.MarketStream.CAPACITY_MARKET.value
_FLEX = s.MarketStream.LOCAL_FLEX_PICLO.value
_DFS = s.MarketStream.DFS.value
_LS = s.MarketStream.LOAD_SERVICE.value

#: gb_v1_6: carries neither `p90_revenue_haircut_contracted_pct` (so the contracted class shares the
#: merchant haircut — the production fallback this whole finding is about) nor
#: `availability.fleet_availability_pct` (so the H-17 factor is exactly 1.0 and cannot blur a
#: delta).
_RELEASE = "gb_v1_6"

#: Real debt terms, so the DSCR the D-139 "never carry the DSCR" limb is about is measured.
_TERMS = DebtTerms(gearing_pct=50.0, annual_rate_pct=7.0, tenor_years=10)
_DISPATCH = DispatchEnergy(annual_charge_kwh=600_000.0, annual_discharge_kwh=516_000.0)

#: Year-1 £ of the contracted award under test, and the merchant dispatch revenue carrying the case.
_AWARD = 15_000.0
_ARBITRAGE = 220_000.0


# --- fixtures --------------------------------------------------------------


def _site() -> s.SiteModel:
    return s.SiteModel(
        site_id="site_in_term_p90",
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


def _result(streams: list[tuple[s.MarketStream, float, s.Firmness]]) -> s.OptimisationResult:
    return s.OptimisationResult(
        result_id="res_in_term_p90",
        request_id="req_in_term_p90",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=1_000.0,
        selected_energy_kwh=2_000.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=st,
                annual_revenue_gbp=rev,
                annual_cost_gbp=12_000.0 if st is s.MarketStream.TARIFF_ARBITRAGE else 0.0,
                firmness=firm,
            )
            for st, rev, firm in streams
        ],
        solver_status=s.SolverStatus.BASELINE,
    )


def _one_award(stream: s.MarketStream, revenue: float = _AWARD) -> s.OptimisationResult:
    """Merchant dispatch revenue plus exactly ONE contracted award, so the delta is unambiguous."""
    return _result(
        [
            (s.MarketStream.TARIFF_ARBITRAGE, _ARBITRAGE, s.Firmness.FIRM),
            (stream, revenue, s.Firmness.CONTRACTED),
        ]
    )


def _library() -> AssumptionLibrary:
    return load_assumptions(_RELEASE)


def _merchant_haircut() -> float:
    return float(_library().get("p50_p90_haircuts", "p90_revenue_haircut_pct").value)


def _fade() -> float:
    return float(_library().get("degradation", "annual_capacity_fade_pct").value)


def _run(result: s.OptimisationResult, **kwargs: Any) -> Any:
    return run_financial_model(
        _site(),
        result,
        _library(),
        horizon_years=_HORIZON,
        debt_terms=_TERMS,
        dispatch_energy=_DISPATCH,
        **kwargs,
    )


# --- THE number: an in-term DNO-flex award contributes nothing to P90 ------


def test_an_in_term_local_flex_award_contributes_zero_to_p90_not_85_percent() -> None:
    """The defect, pinned to the £.

    Un-tenored, the flex line rides the class-wide (merchant-fallback) haircut and hands 85% of
    itself to the cautious case. Tenored — i.e. once the model KNOWS the award is live — D-139 says
    the P90 case for that line is at or near zero, and D-1410 supplies the derivation: GridQ prices
    local flex as utilisation revenue only, and the published P90 utilisation rate is 0.00–0.02
    against a P50 of 0.05 (NGED) / 0.22 (UKPN). So the whole post-haircut line leaves P90.

    Empty `_NO_P90_IN_TERM` and both runs treat the line identically: the delta collapses to 0 and
    this fails. That is the revert.
    """
    result = _one_award(s.MarketStream.LOCAL_FLEX_PICLO)
    untenored = _run(result)
    tenored = _run(result, contract_tenor_years={_FLEX: float(_HORIZON)})

    # Vacuity guard: the case must actually PRICE contracted local flex, or the deltas below are
    # differences between two zeros. (The demo prices LOCAL_FLEX at £0, so this is a real risk.)
    assert untenored.cashflows.annual_revenue_contracted_gbp == pytest.approx(_AWARD)
    assert untenored.cashflows.annual_revenue_by_stream_gbp[_FLEX] == pytest.approx(_AWARD)
    assert tenored.cashflows.contracted_tenor_years == ((_FLEX, float(_HORIZON)),)

    haircut, fade = _merchant_haircut(), _fade()
    for year in (1, 5, 15):
        decay = (1.0 - fade / 100.0) ** (year - 1)
        # What the un-tenored (pre-fix) treatment paid into P90 in that year, and now does not.
        removed = _AWARD * decay * (1.0 - haircut / 100.0)
        assert removed > 0, "fixture needs a non-100% class haircut for the delta to be visible"
        assert untenored.cashflows.p90_ebitda_gbp[year - 1] - tenored.cashflows.p90_ebitda_gbp[
            year - 1
        ] == pytest.approx(removed, abs=0.05)
        # The lender basis moves by the same amount: this is the series the DSCR is measured on,
        # and D-139's operative words are "never allow it to carry the DSCR".
        assert untenored.cashflows.p90_contracted_ebitda_gbp[
            year - 1
        ] - tenored.cashflows.p90_contracted_ebitda_gbp[year - 1] == pytest.approx(
            removed, abs=0.05
        )


def test_the_flex_line_is_removed_from_p90_only_and_p50_is_untouched() -> None:
    """A cautious-case treatment, not a re-pricing. If P50 moved, the stream would have been
    silently written down instead of excluded from the downside, which is a different claim."""
    result = _one_award(s.MarketStream.LOCAL_FLEX_PICLO)
    untenored = _run(result)
    tenored = _run(result, contract_tenor_years={_FLEX: float(_HORIZON)})
    # D-33 holds the in-term payment FLAT (no capacity fade), so P50 is >= the faded case and equal
    # in year 1. The point of the assertion is that the P90 change did not leak into P50.
    assert tenored.cashflows.p50_ebitda_gbp[0] == pytest.approx(
        untenored.cashflows.p50_ebitda_gbp[0], abs=0.02
    )
    assert all(
        t >= u - 0.02
        for t, u in zip(
            tenored.cashflows.p50_ebitda_gbp, untenored.cashflows.p50_ebitda_gbp, strict=True
        )
    )
    assert tenored.case.irr is not None and untenored.case.irr is not None
    assert tenored.case.irr >= untenored.case.irr


def test_the_minimum_dscr_falls_when_the_flex_line_leaves_the_cautious_case() -> None:
    """D-139's operative limb is about the covenant, not the revenue table."""
    result = _one_award(s.MarketStream.LOCAL_FLEX_PICLO)
    untenored = _run(result)
    tenored = _run(result, contract_tenor_years={_FLEX: float(_HORIZON)})
    assert untenored.case.dscr_summary.min_dscr is not None
    assert tenored.case.dscr_summary.min_dscr is not None
    assert tenored.case.dscr_summary.min_dscr < untenored.case.dscr_summary.min_dscr


def test_dfs_in_term_p90_is_zero_on_its_own_authority() -> None:
    """DFS is the only direct NESO route below 1 MW, so its cautious case matters most there.

    Its basis is NOT D-139 (a DNO-flexibility requirement): no realised acceptance rates exist for
    the post-9-April-2026 zonal design, and under E-06 acceptance is binary against a Zonal Cap
    that may be zero MW.
    """
    result = _one_award(s.MarketStream.DFS, 20_000.0)
    untenored = _run(result)
    tenored = _run(result, contract_tenor_years={_DFS: 3.0})
    assert untenored.cashflows.annual_revenue_by_stream_gbp[_DFS] == pytest.approx(20_000.0)

    haircut, fade = _merchant_haircut(), _fade()
    for year in (1, 2, 3):  # in term for all three; year 4+ is the already-live post-expiry rule
        decay = (1.0 - fade / 100.0) ** (year - 1)
        removed = 20_000.0 * decay * (1.0 - haircut / 100.0)
        assert untenored.cashflows.p90_ebitda_gbp[year - 1] - tenored.cashflows.p90_ebitda_gbp[
            year - 1
        ] == pytest.approx(removed, abs=0.05)


# --- the other side of the split: streams that KEEP the class-wide haircut --


@pytest.mark.parametrize("stream", [s.MarketStream.CAPACITY_MARKET, s.MarketStream.LOAD_SERVICE])
def test_a_contracted_stream_off_the_list_keeps_the_class_wide_haircut_in_term(
    stream: s.MarketStream,
) -> None:
    """The control, and the actual "per stream" claim.

    A CM availability payment is a regulated fee with statutory non-delivery penalties (already
    modelled as a separate line): 15% is CONSERVATIVE for it, and nothing here touches it. In year 1
    decay and the in-term fraction are both 1.0, so tenoring such a stream must move the cautious
    case by exactly nothing. If the fix had zeroed the whole CONTRACTED class instead of the two
    streams the requirements name, this fails.
    """
    result = _one_award(stream)
    untenored = _run(result)
    tenored = _run(result, contract_tenor_years={stream.value: float(_HORIZON)})
    assert untenored.cashflows.annual_revenue_contracted_gbp == pytest.approx(_AWARD)
    assert tenored.cashflows.p90_ebitda_gbp[0] == pytest.approx(
        untenored.cashflows.p90_ebitda_gbp[0], abs=0.02
    )
    # And the P90 contribution it keeps is the class-wide haircut, not zero: the £ is really there.
    merchant_only = _run(_result([(s.MarketStream.TARIFF_ARBITRAGE, _ARBITRAGE, s.Firmness.FIRM)]))
    penalty = tenored.cashflows.p90_penalty_exposure_gbp or 0.0
    kept = _AWARD * (1.0 - _merchant_haircut() / 100.0) - penalty
    assert kept > 0
    assert tenored.cashflows.p90_ebitda_gbp[0] - merchant_only.cashflows.p90_ebitda_gbp[
        0
    ] == pytest.approx(kept, abs=0.05)


def test_the_two_classes_are_haircut_differently_in_the_same_run() -> None:
    """Both awards, one run, one library: the scalar is genuinely no longer shared."""
    result = _result(
        [
            (s.MarketStream.TARIFF_ARBITRAGE, _ARBITRAGE, s.Firmness.FIRM),
            (s.MarketStream.CAPACITY_MARKET, 45_000.0, s.Firmness.CONTRACTED),
            (s.MarketStream.LOCAL_FLEX_PICLO, _AWARD, s.Firmness.CONTRACTED),
        ]
    )
    both = _run(result, contract_tenor_years={_CM: float(_HORIZON), _FLEX: float(_HORIZON)})
    cm_only = _run(result, contract_tenor_years={_CM: float(_HORIZON)})
    # Year 1: the ONLY difference between the two runs is which class the flex line is haircut in.
    assert cm_only.cashflows.p90_ebitda_gbp[0] - both.cashflows.p90_ebitda_gbp[0] == pytest.approx(
        _AWARD * (1.0 - _merchant_haircut() / 100.0), abs=0.05
    )
    assert both.cashflows.p50_ebitda_gbp[0] == pytest.approx(
        cm_only.cashflows.p50_ebitda_gbp[0], abs=0.02
    )


def test_the_helper_resolves_the_haircut_per_stream() -> None:
    """The seam itself, so a caller cannot re-introduce the single scalar without failing here."""
    assert _stream_contracted_haircut_pct(_FLEX, 15.0) == 100.0
    assert _stream_contracted_haircut_pct(_DFS, 15.0) == 100.0
    assert _stream_contracted_haircut_pct(_CM, 15.0) == 15.0
    assert _stream_contracted_haircut_pct(_LS, 5.0) == 5.0


# --- disclosure ------------------------------------------------------------


def test_the_treatment_is_disclosed_with_its_stream_specific_reason_and_direction() -> None:
    """A treatment that lowers a P90 and a DSCR without saying so is the same defect class as one
    that raises them. The disclosure must name the stream, the £, and the direction."""
    tenored = _run(
        _one_award(s.MarketStream.LOCAL_FLEX_PICLO),
        contract_tenor_years={_FLEX: float(_HORIZON)},
    )
    w = next(
        w
        for w in tenored.case.warnings
        if f"Contracted stream {_FLEX} earns £" in w and "ZERO" in w
    )
    assert "IN-TERM" in w and "not only after the award expires" in w
    assert "D-139" in w
    assert "utilisation" in w  # the derivation, not just the instruction
    assert "DIRECTION: this LOWERS P90 EBITDA and the minimum DSCR" in w
    assert "P50 is UNCHANGED" in w
    assert str(round(_AWARD * (1.0 - _merchant_haircut() / 100.0), 2)) in w


def test_dfs_is_disclosed_on_its_own_authority_not_the_dno_one() -> None:
    """A wrong reason attached to a right treatment is still a defect in a pack a lender reads."""
    tenored = _run(_one_award(s.MarketStream.DFS, 20_000.0), contract_tenor_years={_DFS: 3.0})
    w = next(w for w in tenored.case.warnings if f"Contracted stream {_DFS} earns £" in w)
    assert "Zonal Cap" in w and "E-06" in w
    assert "DSO" not in w, "the DSO-zone reason is local flex's, not DFS's"
    assert "D-1410" not in w, "D-1410 is a DNO utilisation figure and says nothing about DFS"
    # The whole disclosure, prefix included: no DNO-flexibility requirement id may attach to DFS.
    assert "D-139" not in w and "D-130" not in w, "DNO-flex requirement ids cited at a NESO line"


def test_every_in_term_zero_stream_carries_a_reason_in_its_own_terms() -> None:
    """Adding a stream to the set without a reason must fail here rather than emit a misdescribed
    disclosure into an underwriting pack (the guard `_NO_P90_REASON` already has, for its pair)."""
    assert set(_NO_P90_IN_TERM) == set(_NO_P90_IN_TERM_REASON)
    assert _NO_P90_IN_TERM >= {_FLEX, _DFS}
    for stream, reason in _NO_P90_IN_TERM_REASON.items():
        assert reason and (reason[0].islower() or reason.startswith(stream)), reason
        assert "—" not in reason, f"{stream}: em dash in text rendered into the pack"
    # The two rest on DIFFERENT authorities and must never be cross-cited.
    assert "D-139" in _NO_P90_IN_TERM_REASON[_FLEX]
    assert "D-139" not in _NO_P90_IN_TERM_REASON[_DFS]


# --- dormancy + determinism -----------------------------------------------


def test_a_run_with_no_such_award_carries_neither_the_warning_nor_a_moved_number() -> None:
    """No tenored flex/DFS award ⇒ nothing moves and nothing new is said. The change is scoped to
    the in-term case; the un-tenored over-statement stays the D-130 exposure it already was."""
    result = _one_award(s.MarketStream.CAPACITY_MARKET)
    base = _run(result)
    tenored_cm = _run(result, contract_tenor_years={_CM: 5.0})
    for model in (base, tenored_cm):
        assert not any(
            "its P90 contribution is ZERO for the whole award term" in w
            for w in model.case.warnings
        )
    # The un-tenored flex case is unchanged too: it still rides the class haircut, loudly warned.
    flex = _run(_one_award(s.MarketStream.LOCAL_FLEX_PICLO))
    assert not any(
        "its P90 contribution is ZERO for the whole award term" in w for w in flex.case.warnings
    )
    assert any("projected to the FULL" in w and "D-130" in w for w in flex.case.warnings)


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"contract_tenor_years": {_FLEX: 1.0}}, {"contract_tenor_years": {_CM: 2.0, _FLEX: 3.0}}],
)
def test_the_in_term_treatment_is_deterministic(kwargs: dict[str, Any]) -> None:
    result = _result(
        [
            (s.MarketStream.TARIFF_ARBITRAGE, _ARBITRAGE, s.Firmness.FIRM),
            (s.MarketStream.CAPACITY_MARKET, 45_000.0, s.Firmness.CONTRACTED),
            (s.MarketStream.LOCAL_FLEX_PICLO, _AWARD, s.Firmness.CONTRACTED),
        ]
    )
    first, second = _run(result, **kwargs), _run(result, **kwargs)
    assert first.case.model_dump(mode="json") == second.case.model_dump(mode="json")
