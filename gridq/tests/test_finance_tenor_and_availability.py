"""Contract tenor and fleet availability in the year loop (D-33, D-130, H-17).

Three findings from the 2026-08-10 hostile review, all on the same six lines of the projection:

- **D-33** — CM revenue was shrunk every year by battery capacity fade and never CPI-indexed. The
  payment is FIXED on the awarded de-rated MW for the term; cell fade does not touch it.
- **D-130** — a local-flex award (typically 1–2 years, terminable) sat in `rev_contracted` earning
  for the full 10–25 year horizon in BOTH P50 and P90, inflating multi-year DSCR and debt capacity.
- **H-17** — every revenue line was earned at 100% availability for the whole horizon; no
  availability term existed anywhere in `finance/`.

The first test in this file is the load-bearing one: with **no contract-tenor evidence and no
availability figure in the release** the series must be bit-identical to the pre-change arithmetic.
The in-term/post-term split is not computable without the contract length and no shipped release
carries a fleet-availability %, so nothing may move on evidence that was never supplied. Everything
after it proves the machinery is real once the evidence arrives.
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
_CM = s.MarketStream.CAPACITY_MARKET.value
_FLEX = s.MarketStream.LOCAL_FLEX_PICLO.value


# --- fixtures --------------------------------------------------------------


def _site(levies: dict[str, float] | None = None) -> s.SiteModel:
    load = s.LoadDataSummary(
        annual_kwh=1_800_000.0,
        half_hourly_rows=17520,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        max_import_kw=420.0,
    )
    return s.SiteModel(
        site_id="site_tenor",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=load,
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"day": 0.2431, "night": 0.1187},
            levies=levies if levies is not None else {"cclevy": 0.00775},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


def _result(streams: list[tuple[s.MarketStream, float, s.Firmness]]) -> s.OptimisationResult:
    return s.OptimisationResult(
        result_id="res_tenor",
        request_id="req_tenor",
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


def _mixed() -> s.OptimisationResult:
    """Merchant dispatch revenue + a CM agreement + a local-flex award — the D-130 shape."""
    return _result(
        [
            (s.MarketStream.TARIFF_ARBITRAGE, 40_000.0, s.Firmness.FIRM),
            (s.MarketStream.DUOS_RED_BAND_AVOIDANCE, 10_000.0, s.Firmness.FIRM),
            (s.MarketStream.CAPACITY_MARKET, 9_000.0, s.Firmness.CONTRACTED),
            (s.MarketStream.LOCAL_FLEX_PICLO, 4_000.0, s.Firmness.CONTRACTED),
        ]
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


def _lib(**sections: dict[str, Any]) -> AssumptionLibrary:
    """gb_v1_6 with optional sections overridden — the only way to exercise a key no release has."""
    dumped = load_assumptions("gb_v1_6").model_dump(mode="json")
    for name, content in sections.items():
        dumped[name] = content
    return AssumptionLibrary.model_validate(dumped)


def _lib_with_contracted_haircut(pct: float) -> AssumptionLibrary:
    """gb_v1_6 plus a DIFFERENTIATED contracted P90 haircut.

    gb_v1_6 omits `p90_revenue_haircut_contracted_pct`, so contracted revenue shares the merchant
    haircut and the merchant-vs-contracted distinction is invisible. Injecting it is the only way
    to see post-tenor revenue actually change haircut class.
    """
    dumped = load_assumptions("gb_v1_6").model_dump(mode="json")
    dumped["p50_p90_haircuts"]["p90_revenue_haircut_contracted_pct"] = _assumption(pct)
    return AssumptionLibrary.model_validate(dumped)


def _build(
    result: s.OptimisationResult | None = None,
    library: AssumptionLibrary | None = None,
    **kwargs: Any,
) -> CashflowModel:
    return build_cashflow_model(
        _site(),
        result if result is not None else _mixed(),
        library if library is not None else load_assumptions("gb_v1_6"),
        horizon_years=_HORIZON,
        **kwargs,
    )


def _legacy_series(model: CashflowModel, library: AssumptionLibrary) -> dict[str, list[float]]:
    """The pre-change year loop, transcribed verbatim from `cashflows.py` @ a0b3e07.

    Rebuilt from the model's OWN reported inputs so it cannot drift with the fixture: the point is
    the arithmetic shape (one decay applied to everything, one penalty term), not the £ figures.
    """
    firm = model.annual_revenue_firm_gbp - (model.perfect_foresight_adjustment_gbp or 0.0)
    contracted = model.annual_revenue_contracted_gbp
    haircut = float(library.get("p50_p90_haircuts", "p90_revenue_haircut_pct").value)
    try:
        contracted_haircut = float(
            library.get("p50_p90_haircuts", "p90_revenue_haircut_contracted_pct").value
        )
    except KeyError:
        contracted_haircut = haircut
    fade = float(library.get("degradation", "annual_capacity_fade_pct").value)
    penalty_base = model.p90_penalty_exposure_gbp or 0.0
    dispatch_cost = model.annual_dispatch_cost_gbp
    # The UN-rounded sum of the cost lines, exactly as the year loop uses it. Using the rounded
    # `total_annual_operating_cost_gbp` property here instead differs by ~1e-12 and flips a year
    # whose revenue happens to land on a half-penny boundary — a transcription artefact, not a
    # behaviour change.
    operating_cost = sum(v for v in model.annual_cost_by_line_gbp.values() if v is not None)

    base_revenue = round(firm + contracted, 2)
    p50, p90, p90_contracted = [], [], []
    for year in range(1, model.horizon_years + 1):
        decay = (1.0 - fade / 100.0) ** (year - 1)
        p50_rev = base_revenue * decay
        p90_rev = (
            firm * (1.0 - haircut / 100.0) + contracted * (1.0 - contracted_haircut / 100.0)
        ) * decay
        p50.append(round(p50_rev - dispatch_cost - operating_cost, 2))
        p90.append(round(p90_rev - penalty_base * decay - dispatch_cost - operating_cost, 2))
        p90_contracted_rev = contracted * (1.0 - contracted_haircut / 100.0) * decay
        p90_contracted.append(
            round(
                p90_contracted_rev - penalty_base * decay - dispatch_cost - operating_cost,
                2,
            )
        )
    return {"p50": p50, "p90": p90, "p90_contracted": p90_contracted}


# --- THE dormancy regression ----------------------------------------------


@pytest.mark.parametrize("version", ["gb_v0_1", "gb_v1_0", "gb_v1_6"])
@pytest.mark.parametrize("with_penalties", [False, True])
def test_no_tenor_and_no_availability_key_reproduces_the_legacy_series_exactly(
    version: str, with_penalties: bool
) -> None:
    """No evidence supplied ⇒ not one penny moves. The guard on this whole change set.

    `availability.expected_availability_pct` is `null` on every shipped release, and no release up
    to and including gb_v1_6 — the three parametrised here — carries `fleet_availability_pct`, so
    H-17 applies no de-rate on any of them. (gb_v1_7 DOES carry it, which is the whole point of
    that release; that path is asserted separately by
    `test_the_release_that_carries_the_sourced_figure_activates_the_derate`, which is where the
    number is *meant* to move.) No caller supplies `contract_tenor_years`, so D-33/D-130 apply no
    in-term/post-term split anywhere. Both findings are DISCLOSED (see the warning tests below) and
    neither is silently guessed. If this test ever fails, the change set has moved a number on a
    release that supplied no evidence for it.
    """
    dumped = load_assumptions(version).model_dump(mode="json")
    if with_penalties:
        dumped["penalties"] = {"cm_annual_cap_pct_of_revenue": _assumption(100.0)}
    library = AssumptionLibrary.model_validate(dumped)
    model = _build(library=library)

    expected = _legacy_series(model, library)
    assert model.p50_ebitda_gbp == expected["p50"]
    assert model.p90_ebitda_gbp == expected["p90"]
    assert model.p90_contracted_ebitda_gbp == expected["p90_contracted"]
    assert model.availability_factor_pct is None
    assert model.contracted_tenor_years == ()


def test_the_untenored_case_states_both_findings_as_live_exposures() -> None:
    """Dormant arithmetic is only honest if the exposure is on the record (register §2's lesson)."""
    model = _build()
    over = next(w for w in model.warnings if "projected to the FULL" in w)
    assert "D-130" in over and "OVER-stated" in over
    assert _CM in over and _FLEX in over
    assert "1–2 years" in over and "terminable" in over
    assert "No tenor is assumed here" in over

    under = next(w for w in model.warnings if "capacity fade is applied to the un-tenored" in w)
    assert "D-33" in under and "UNDER-states it in term" in under
    assert "nothing is guessed in its absence" in under


def test_a_merchant_only_run_gets_neither_contracted_warning() -> None:
    model = _build(_result([(s.MarketStream.TARIFF_ARBITRAGE, 50_000.0, s.Firmness.FIRM)]))
    assert not any("projected to the FULL" in w for w in model.warnings)
    assert not any("capacity fade is applied to the un-tenored" in w for w in model.warnings)


# --- D-33: a fixed availability payment does not fade ---------------------


def test_a_tenored_cm_agreement_is_held_flat_on_the_awarded_mw_for_its_term() -> None:
    """The awarded de-rated MW × clearing price does not shrink because the cells aged."""
    untenored = _build()
    tenored = _build(contract_tenor_years={_CM: 15.0})
    fade = float(load_assumptions("gb_v1_6").get("degradation", "annual_capacity_fade_pct").value)

    # The CM line is the only difference, so year-on-year the tenored case is higher by the fade
    # the CM revenue was wrongly bearing.
    cm = 9_000.0
    for year in (1, 5, 15):
        decay = (1.0 - fade / 100.0) ** (year - 1)
        lift = cm - cm * decay
        assert tenored.p50_ebitda_gbp[year - 1] - untenored.p50_ebitda_gbp[
            year - 1
        ] == pytest.approx(lift, abs=0.02)
    # Year 1 is unaffected (decay == 1.0): the correction is purely to the SHAPE.
    assert tenored.p50_ebitda_gbp[0] == pytest.approx(untenored.p50_ebitda_gbp[0], abs=0.02)
    assert tenored.contracted_tenor_years == ((_CM, 15.0),)


def test_the_flat_in_term_treatment_is_disclosed_with_its_cpi_caveat() -> None:
    model = _build(contract_tenor_years={_CM: 15.0})
    w = next(w for w in model.warnings if f"Contracted stream {_CM} is tenored" in w)
    assert "held FLAT on the awarded de-rated MW" in w
    assert "D-33" in w
    # D-33's CPI limb stays Tier B — the rate is not invented, only named.
    assert "NO CPI/RPI indexation" in w and "never invented here" in w
    assert "never expires inside the projection" in w


# --- D-130: contracted revenue ends at the contract term ------------------


def test_revenue_ends_at_the_tenor_and_beyond_it_is_merchant_class() -> None:
    """A 2-year award earns contracted revenue for 2 years; after that it is re-contracting risk.

    In P50 the £ continues at merchant class (faded). In P90 it drops to the MERCHANT haircut,
    because a re-contracting expectation is not a contract — and the CM non-delivery penalty stops
    with it, since an expired agreement leaves no obligation to fail.
    """
    contracted_haircut = 5.0  # a contracted award is less risky than a merchant spread
    library = _lib_with_contracted_haircut(contracted_haircut)
    haircut = float(library.get("p50_p90_haircuts", "p90_revenue_haircut_pct").value)
    fade = float(library.get("degradation", "annual_capacity_fade_pct").value)
    assert contracted_haircut != haircut, "fixture needs the two haircuts to differ"

    tenored = _build(library=library, contract_tenor_years={_CM: 2.0})
    untenored = _build(library=library)
    penalty = untenored.p90_penalty_exposure_gbp
    assert penalty is not None and penalty > 0, "fixture needs a live CM penalty line"

    # Year 3: out of term. Two things change, and only these two.
    decay = (1.0 - fade / 100.0) ** 2
    haircut_class_change = (
        9_000.0 * decay * ((1.0 - haircut / 100.0) - (1.0 - contracted_haircut / 100.0))
    )
    assert haircut_class_change < 0, "the merchant haircut must be the harsher of the two here"
    penalty_release = penalty * decay  # the expired agreement can no longer be breached
    assert tenored.p90_ebitda_gbp[2] - untenored.p90_ebitda_gbp[2] == pytest.approx(
        haircut_class_change + penalty_release, abs=0.05
    )
    # Net of both, the expired year is still WORSE than pretending the award ran forever.
    assert tenored.p90_ebitda_gbp[2] < untenored.p90_ebitda_gbp[2]

    # Year 2: in term. The payment is flat (a lift on P50) and the penalty is flat with it.
    lift = 9_000.0 - 9_000.0 * (1.0 - fade / 100.0)
    assert tenored.p50_ebitda_gbp[1] - untenored.p50_ebitda_gbp[1] == pytest.approx(lift, abs=0.02)


def test_the_lender_basis_contracted_series_stops_at_the_tenor() -> None:
    """D-130's real damage: multi-year DSCR built on revenue no contract obliges anyone to pay.

    Post-expiry revenue never enters `p90_contracted_ebitda_gbp` — a lender does not lend against
    a re-contracting expectation.
    """
    tenored = _build(contract_tenor_years={_CM: 3.0, _FLEX: 1.0})
    untenored = _build()
    # In the expired years the contracted basis carries no contracted revenue at all, so it is
    # exactly the cost base (negative), and strictly worse than the un-tenored case.
    costs = tenored.annual_dispatch_cost_gbp + tenored.total_annual_operating_cost_gbp
    for year in (4, 10, 15):
        assert tenored.p90_contracted_ebitda_gbp[year - 1] == pytest.approx(-costs, abs=0.02)
        assert (
            tenored.p90_contracted_ebitda_gbp[year - 1]
            < untenored.p90_contracted_ebitda_gbp[year - 1]
        )


def test_local_flex_p90_is_forced_to_zero_after_expiry() -> None:
    """A terminable DSO award carries no re-procurement guarantee, so the cautious case gets none.

    Whole NGED constraint zones received zero dispatch in 2024/25; re-contracting that zone is not
    a P90 assumption. CM keeps a post-expiry P90 contribution at the merchant haircut; local flex
    does not keep one at all.
    """
    flex_only = _result(
        [
            (s.MarketStream.TARIFF_ARBITRAGE, 40_000.0, s.Firmness.FIRM),
            (s.MarketStream.LOCAL_FLEX_PICLO, 4_000.0, s.Firmness.CONTRACTED),
        ]
    )
    library = load_assumptions("gb_v1_6")
    haircut = float(library.get("p50_p90_haircuts", "p90_revenue_haircut_pct").value)
    fade = float(library.get("degradation", "annual_capacity_fade_pct").value)
    tenored = _build(flex_only, library=library, contract_tenor_years={_FLEX: 2.0})
    untenored = _build(flex_only, library=library)

    # P50 keeps the £ as merchant-class re-contracting. P90 has NONE of it, so relative to the
    # merchant treatment the whole post-haircut flex line is removed from the cautious case.
    for year in (3, 8, 15):
        decay = (1.0 - fade / 100.0) ** (year - 1)
        flex_p50 = 4_000.0 * decay
        removed_from_p90 = flex_p50 * (1.0 - haircut / 100.0)
        assert removed_from_p90 > 0  # sanity: the merchant haircut is not 100%
        # P50 is unchanged by the tenor (merchant re-contracting is still faded at the same rate).
        assert tenored.p50_ebitda_gbp[year - 1] == pytest.approx(
            untenored.p50_ebitda_gbp[year - 1], abs=0.02
        )
        # P90 loses the whole flex contribution it used to carry at the contracted haircut.
        assert untenored.p90_ebitda_gbp[year - 1] - tenored.p90_ebitda_gbp[year - 1] == (
            pytest.approx(removed_from_p90, abs=0.05)
        )
    w = next(w for w in tenored.warnings if f"Contracted stream {_FLEX} is tenored" in w)
    assert "removed from P90 entirely" in w


def test_a_fractional_tenor_prorates_the_single_boundary_year() -> None:
    """1.5 years = a full year 1 and half of year 2 — deterministic arithmetic, no invented rule."""
    tenored = _build(contract_tenor_years={_CM: 1.5})
    library = load_assumptions("gb_v1_6")
    fade = float(library.get("degradation", "annual_capacity_fade_pct").value)
    decay2 = 1.0 - fade / 100.0
    # Year 2 CM revenue = half flat (in term) + half faded (re-contracted).
    contribution = 9_000.0 * 0.5 + 9_000.0 * 0.5 * decay2
    untenored_contribution = 9_000.0 * decay2
    delta = contribution - untenored_contribution
    untenored = _build()
    assert tenored.p50_ebitda_gbp[1] - untenored.p50_ebitda_gbp[1] == pytest.approx(delta, abs=0.02)


def test_a_tenor_longer_than_the_horizon_never_expires_in_the_projection() -> None:
    model = _build(contract_tenor_years={_CM: 25.0})
    costs = model.annual_dispatch_cost_gbp + model.total_annual_operating_cost_gbp
    # The contracted basis carries CM revenue in the final year — nothing expired.
    assert model.p90_contracted_ebitda_gbp[-1] > -costs


# --- tenor input hygiene --------------------------------------------------


@pytest.mark.parametrize("bad", [0.0, -3.0, float("nan"), float("inf"), True, "2"])
def test_an_unusable_tenor_is_dropped_with_a_warning_never_coerced(bad: Any) -> None:
    model = _build(contract_tenor_years={_CM: bad})
    assert model.contracted_tenor_years == ()
    assert any("ignored" in w and _CM in w for w in model.warnings)
    # And the exposure warning still stands, because nothing was tenored.
    assert any("projected to the FULL" in w for w in model.warnings)


def test_a_tenor_for_a_stream_with_no_contracted_revenue_is_recorded_as_unused() -> None:
    """A mis-keyed tenor must never be applied to a different stream's revenue."""
    model = _build(
        _result([(s.MarketStream.TARIFF_ARBITRAGE, 50_000.0, s.Firmness.FIRM)]),
        contract_tenor_years={_CM: 5.0},
    )
    assert model.contracted_tenor_years == ()
    assert any("prices no CONTRACTED revenue this run" in w for w in model.warnings)


def test_market_stream_members_work_as_keys_as_well_as_their_values() -> None:
    by_member = _build(contract_tenor_years={s.MarketStream.CAPACITY_MARKET: 4.0})
    by_value = _build(contract_tenor_years={_CM: 4.0})
    assert by_member.contracted_tenor_years == ((_CM, 4.0),)
    assert by_member.p50_ebitda_gbp == by_value.p50_ebitda_gbp


# --- H-17: fleet availability --------------------------------------------


def test_no_availability_key_warns_loudly_and_applies_no_derate() -> None:
    """The honest state on gb_v0_1 … gb_v1_6: 100% uptime, said out loud."""
    model = _build()
    assert model.availability_factor_pct is None
    w = next(w for w in model.warnings if w.startswith("NO availability de-rate"))
    assert "100% availability for the whole horizon" in w
    assert "H-17" in w
    assert "availability.fleet_availability_pct" in w
    assert "none is invented" in w
    # It must also name the penalty limb, which is optimistic for the same reason.
    assert "penalty/clawback" in w


def test_the_release_that_carries_the_sourced_figure_activates_the_derate() -> None:
    """gb_v1_7 lands `availability.fleet_availability_pct` — the de-rate is LIVE from that release.

    This is the finding actually closing rather than being disclosed, so it is pinned here: if a
    later release drops or renames the key, this fails rather than the de-rate quietly switching
    off. The `!= None` shape (rather than `== 95.0`) keeps the test about the wiring, not the value.

    The counterfactual is gb_v1_7 with the availability section emptied — NOT gb_v1_6 — so the
    comparison isolates the de-rate instead of every other difference between two releases.
    """
    library = load_assumptions("gb_v1_7")
    model = _build(library=library)
    assert model.availability_factor_pct is not None
    assert 0 < model.availability_factor_pct < 100
    assert "availability.fleet_availability_pct" in model.assumptions_used
    assert any("Revenue is de-rated for fleet availability" in w for w in model.warnings)

    same_release_without_the_key = AssumptionLibrary.model_validate(
        {**library.model_dump(mode="json"), "availability": {}}
    )
    baseline = _build(library=same_release_without_the_key)
    assert baseline.availability_factor_pct is None
    factor = model.availability_factor_pct / 100.0
    costs = baseline.annual_dispatch_cost_gbp + baseline.total_annual_operating_cost_gbp
    for derated, full in zip(model.p50_ebitda_gbp, baseline.p50_ebitda_gbp, strict=True):
        assert derated < full
        # Exactly the revenue de-rate, with costs untouched.
        assert derated == pytest.approx((full + costs) * factor - costs, abs=0.02)


def test_a_sourced_availability_figure_derates_revenue_in_both_series() -> None:
    library = _lib(availability={"fleet_availability_pct": _assumption(96.0)})
    derated = _build(library=library)
    full = _build()
    assert derated.availability_factor_pct == 96.0
    # Revenue is de-rated; costs are NOT (an unavailable battery still costs its fixed opex), so
    # EBITDA falls by 4% of REVENUE, not 4% of EBITDA.
    costs = full.annual_dispatch_cost_gbp + full.total_annual_operating_cost_gbp
    revenue_year1 = full.p50_ebitda_gbp[0] + costs
    assert derated.p50_ebitda_gbp[0] == pytest.approx(revenue_year1 * 0.96 - costs, abs=0.02)
    # And it lands on the P90 series too — availability is an expected-value effect, not a scenario.
    assert derated.p90_ebitda_gbp[0] < full.p90_ebitda_gbp[0]
    assert "availability.fleet_availability_pct" in derated.assumptions_used


def test_the_derate_multiplies_degradation_rather_than_replacing_it() -> None:
    """Distinct effects: fade shrinks the capability, unavailability stops it earning."""
    library = _lib(availability={"fleet_availability_pct": _assumption(95.0)})
    derated = _build(library=library)
    full = _build()
    costs = full.annual_dispatch_cost_gbp + full.total_annual_operating_cost_gbp
    for year in (1, 8, 15):
        full_rev = full.p50_ebitda_gbp[year - 1] + costs
        derated_rev = derated.p50_ebitda_gbp[year - 1] + costs
        assert derated_rev == pytest.approx(full_rev * 0.95, abs=0.02)


def test_the_firm_commitment_key_is_never_substituted_for_the_fleet_factor() -> None:
    """`expected_availability_pct` is a DIFFERENT quantity and must never stand in for the fleet %.

    Its own library notes call it the site-specific firm-commitment factor, an empty home per D30.
    Quietly using it as an annual fleet-wide revenue scalar would be the same class of error H-17
    reports. But the omission must not pass silently either — hence the explicit warning.
    """
    library = _lib(availability={"expected_availability_pct": _assumption(97.5)})
    model = _build(library=library)
    assert model.availability_factor_pct is None
    assert any(w.startswith("NO availability de-rate") for w in model.warnings)
    w = next(w for w in model.warnings if "is NOT used as the fleet" in w)
    assert "site-specific firm-commitment factor" in w
    assert "cannot pass as if it had been applied" in w
    assert "availability.expected_availability_pct" not in model.assumptions_used


def test_the_fleet_key_is_used_even_when_the_firm_commitment_key_is_also_populated() -> None:
    library = _lib(
        availability={
            "fleet_availability_pct": _assumption(94.0),
            "expected_availability_pct": _assumption(99.0),
        }
    )
    model = _build(library=library)
    assert model.availability_factor_pct == 94.0
    assert not any("is NOT used as the fleet" in w for w in model.warnings)


@pytest.mark.parametrize("bad", [None, 0.0, -5.0, 120.0, True, "96"])
def test_an_unusable_availability_value_falls_through_to_the_data_ask(bad: Any) -> None:
    library = _lib(availability={"fleet_availability_pct": _assumption(bad)})
    model = _build(library=library)
    assert model.availability_factor_pct is None
    assert any(w.startswith("NO availability de-rate") for w in model.warnings)


def test_unavailability_also_raises_the_contracted_penalty_exposure() -> None:
    """H-17's second limb: on a contracted availability stream an outage forfeits the payment AND
    triggers the penalty regime, so it hits the line twice, as the service terms do."""
    penalties = {"cm_annual_cap_pct_of_revenue": _assumption(100.0)}
    base = _build(library=_lib(penalties=penalties))
    derated = _build(
        library=_lib(
            penalties=penalties, availability={"fleet_availability_pct": _assumption(90.0)}
        )
    )
    assert base.p90_penalty_exposure_gbp is not None
    assert derated.p90_penalty_exposure_gbp is not None
    # The shortfall driving the penalty is the contracted haircut PLUS the unavailable 10%.
    haircut = float(
        load_assumptions("gb_v1_6").get("p50_p90_haircuts", "p90_revenue_haircut_pct").value
    )
    assert base.p90_penalty_exposure_gbp == pytest.approx(9_000.0 * haircut / 100.0, abs=0.02)
    assert derated.p90_penalty_exposure_gbp == pytest.approx(
        9_000.0 * (haircut / 100.0 + 0.10), abs=0.02
    )


# --- invariants that must survive every new path -------------------------

_PATHS: list[dict[str, Any]] = [
    {},
    {"contract_tenor_years": {_CM: 1.0}},
    {"contract_tenor_years": {_CM: 2.5, _FLEX: 1.0}},
    {"contract_tenor_years": {_CM: 15.0, _FLEX: 25.0}},
]


@pytest.mark.parametrize("kwargs", _PATHS)
@pytest.mark.parametrize("availability_pct", [None, 99.0, 90.0, 50.0])
def test_p90_never_exceeds_p50_in_any_year_on_any_path(
    kwargs: dict[str, Any], availability_pct: float | None
) -> None:
    """The cautious case can never beat the central one — on every new branch, every year."""
    sections: dict[str, Any] = {"penalties": {"cm_annual_cap_pct_of_revenue": _assumption(100.0)}}
    if availability_pct is not None:
        sections["availability"] = {"fleet_availability_pct": _assumption(availability_pct)}
    model = _build(library=_lib(**sections), **kwargs)
    pairs = zip(model.p50_ebitda_gbp, model.p90_ebitda_gbp, strict=True)
    for year, (p50, p90) in enumerate(pairs, start=1):
        assert p90 <= p50 + 0.01, f"P90 beat P50 in year {year}"


@pytest.mark.parametrize("kwargs", _PATHS)
@pytest.mark.parametrize("cap_pct", [100.0, 250.0])
def test_the_n2_bound_holds_per_year_so_the_contracted_contribution_floors_at_zero(
    kwargs: dict[str, Any], cap_pct: float
) -> None:
    """N2's invariant, re-checked per year now that contracted revenue varies by year.

    A contracted stream can never make the P90 contracted basis worse than holding no contract at
    all: the combined (forfeited revenue + penalty) deduction is bounded at that year's contracted
    revenue. A 250% cap is a deliberately pathological library to prove the bound, not the regime.
    """
    library = _lib(
        penalties={"cm_annual_cap_pct_of_revenue": _assumption(cap_pct)},
        availability={"fleet_availability_pct": _assumption(92.0)},
    )
    model = _build(library=library, **kwargs)
    costs = model.annual_dispatch_cost_gbp + model.total_annual_operating_cost_gbp
    for year, value in enumerate(model.p90_contracted_ebitda_gbp, start=1):
        assert value >= -costs - 0.01, (
            f"year {year}: the penalty ate into the cost base, so the contracted contribution "
            f"went below zero ({value} vs the -{costs} cost floor)"
        )


@pytest.mark.parametrize("kwargs", _PATHS)
def test_every_new_path_stays_deterministic(kwargs: dict[str, Any]) -> None:
    library = _lib(availability={"fleet_availability_pct": _assumption(96.5)})
    first = _build(library=library, **kwargs)
    second = _build(library=library, **kwargs)
    assert first.p50_ebitda_gbp == second.p50_ebitda_gbp
    assert first.p90_ebitda_gbp == second.p90_ebitda_gbp
    assert first.p90_contracted_ebitda_gbp == second.p90_contracted_ebitda_gbp
    assert first.warnings == second.warnings
    assert first.contracted_tenor_years == second.contracted_tenor_years


def test_the_tenor_and_availability_evidence_is_recorded_on_the_model() -> None:
    """Traceability: what was applied is on the record, in a deterministic order."""
    library = _lib(availability={"fleet_availability_pct": _assumption(96.0)})
    model = _build(library=library, contract_tenor_years={_FLEX: 1.0, _CM: 15.0})
    assert model.contracted_tenor_years == ((_CM, 15.0), (_FLEX, 1.0))
    assert model.availability_factor_pct == 96.0
