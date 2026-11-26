"""The D-33 / D-130 CALL PATH: contract tenor actually reaches the projection (2026-08-13).

`tests/test_finance_tenor_and_availability.py` proves the tenor *arithmetic* is correct, and it has
proved that since the WP-0 batch. It could not prove the arithmetic ever ran, because it calls
`build_cashflow_model` directly and `contract_tenor_years` had **zero production callers**:
`run_financial_model` passed no tenor, so on every real run the defect behaviour persisted. A 1-2
year terminable award earned for the full 10-25 year horizon in P90 and in the lender basis, and a
fixed CM availability payment was still shrunk by battery capacity fade.

That was the FOURTH instance of one pattern in this repo (Gate 3 reconciliation, the F-19 local-flex
call rate, the availability-reservation compiler): built, tested, never called. So this module tests
the *seam*, deliberately from the outside — every assertion goes through `run_financial_model`, the
entry point every production caller uses, and never through `build_cashflow_model`. If the threading
is ever removed, these fail while the arithmetic tests stay green.

Three evidence sources are covered, in descending order of how live they are:

  1. ``site.provisioning_requirements[].tenor_years`` — needs NO caller argument at all, so a site
     whose own JSON declares a customer provisioning term gets the term applied today.
  2. ``commitments`` — typed `AvailabilityCommitment` payloads read via ``tenor_years``.
  3. ``contract_tenor_years`` — an explicit per-stream mapping; wins, and warns when it contradicts.

Plus the half that makes the fix safe: the applied tenor joins ``_hash_input``, so two runs whose
P90 series differ by a contract term cannot share a ``case_id`` (defect N25/MD-F-14).
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
    _NO_P90_AFTER_EXPIRY,
    _NO_P90_REASON,
    resolve_contract_tenor_evidence,
    tenors_from_commitments,
    tenors_from_provisioning_requirements,
)
from gridq.finance.financial_model import _hash_input

_HORIZON = 15
_CM = s.MarketStream.CAPACITY_MARKET.value
_FLEX = s.MarketStream.LOCAL_FLEX_PICLO.value
_DFS = s.MarketStream.DFS.value
_LS = s.MarketStream.LOAD_SERVICE.value

#: Real debt terms so the DSCR is measured rather than unavailable: the whole point of D-130 is what
#: it does to multi-year DSCR and debt capacity, which is invisible without a debt structure.
_TERMS = DebtTerms(gearing_pct=50.0, annual_rate_pct=7.0, tenor_years=10)
#: Dispatch volumes so the volumetric levy line activates (it is unavailable without them).
_DISPATCH = DispatchEnergy(annual_charge_kwh=600_000.0, annual_discharge_kwh=516_000.0)


# --- fixtures --------------------------------------------------------------


def _requirement(
    requirement_id: str = "pr_1",
    *,
    tenor_years: float | None = 3.0,
    counterfactual: float | None = 60_000.0,
) -> s.ProvisioningRequirement:
    return s.ProvisioningRequirement(
        requirement_id=requirement_id,
        windows=[s.AvailabilityWindow(start_time="08:00", end_time="18:00")],
        additional_load_kw=150.0,
        tenor_years=tenor_years,
        reference="CUSTOMER-CONTRACT-001",
        declared_counterfactual_gbp_per_year=counterfactual,
        # The schema refuses a basis without the figure it describes, so the basis follows the £.
        counterfactual_basis=(
            None
            if counterfactual is None
            else "NGED reinforcement quote ref R-42, annualised over the term"
        ),
    )


def _site(provisioning: list[s.ProvisioningRequirement] | None = None) -> s.SiteModel:
    return s.SiteModel(
        site_id="site_tenor_threading",
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
        provisioning_requirements=provisioning,
    )


def _commitment(
    stream: s.MarketStream,
    tenor_years: float | None,
    commitment_id: str = "c1",
) -> s.AvailabilityCommitment:
    return s.AvailabilityCommitment(
        commitment_id=commitment_id,
        stream=stream,
        committed_power_kw=500.0,
        windows=[s.AvailabilityWindow(start_time="16:00", end_time="19:00")],
        tenor_years=tenor_years,
        counterparty="NESO",
        reference="AGREEMENT-XYZ",
    )


def _result(
    streams: list[tuple[s.MarketStream, float, float, s.Firmness]],
) -> s.OptimisationResult:
    return s.OptimisationResult(
        result_id="res_threading",
        request_id="req_threading",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=1_000.0,
        selected_energy_kwh=2_000.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=st, annual_revenue_gbp=rev, annual_cost_gbp=cost, firmness=firm
            )
            for st, rev, cost, firm in streams
        ],
        solver_status=s.SolverStatus.BASELINE,
    )


#: A stack that actually returns an IRR, so the *direction* of the tenor fixes is measurable on the
#: headline metrics rather than on an unavailable one.
_MIXED = _result(
    [
        (s.MarketStream.TARIFF_ARBITRAGE, 220_000.0, 12_000.0, s.Firmness.FIRM),
        (s.MarketStream.DUOS_RED_BAND_AVOIDANCE, 45_000.0, 0.0, s.Firmness.FIRM),
        (s.MarketStream.CAPACITY_MARKET, 45_000.0, 0.0, s.Firmness.CONTRACTED),
        (s.MarketStream.LOCAL_FLEX_PICLO, 15_000.0, 0.0, s.Firmness.CONTRACTED),
    ]
)


def _run(
    result: s.OptimisationResult | None = None,
    *,
    site: s.SiteModel | None = None,
    library: AssumptionLibrary | None = None,
    **kwargs: Any,
) -> Any:
    """Always through `run_financial_model`: the seam under test is the call path, not the maths."""
    return run_financial_model(
        site if site is not None else _site(),
        result if result is not None else _MIXED,
        library if library is not None else load_assumptions("gb_v1_6"),
        horizon_years=_HORIZON,
        debt_terms=_TERMS,
        dispatch_energy=_DISPATCH,
        **kwargs,
    )


# --- THE regression: the seam exists at all -------------------------------


def test_an_explicit_tenor_reaches_the_projection_through_run_financial_model() -> None:
    """The load-bearing test. Before 2026-08-13 no argument here could reach the year loop."""
    model = _run(contract_tenor_years={_CM: 2.0, _FLEX: 1.0})
    assert model.cashflows.contracted_tenor_years == ((_CM, 2.0), (_FLEX, 1.0))


def test_market_stream_members_survive_the_extra_hop() -> None:
    """`MarketStream` keys work through the orchestrator, not only at `build_cashflow_model`."""
    by_member = _run(contract_tenor_years={s.MarketStream.CAPACITY_MARKET: 4.0})
    by_value = _run(contract_tenor_years={_CM: 4.0})
    assert by_member.cashflows.contracted_tenor_years == ((_CM, 4.0),)
    assert by_member.cashflows.p50_ebitda_gbp == by_value.cashflows.p50_ebitda_gbp


def test_with_no_evidence_nothing_is_tenored_and_the_exposure_is_still_stated() -> None:
    """Threading the parameter must not start GUESSING a term: no evidence, no tenor, loud
    warning."""
    model = _run()
    assert model.cashflows.contracted_tenor_years == ()
    assert any("projected to the FULL" in w and "D-130" in w for w in model.case.warnings)
    assert any("capacity fade is applied to the un-tenored" in w for w in model.case.warnings)


# --- source 1: the site's own contract records (live with no caller involved) ---------------


def test_load_service_tenor_comes_from_the_site_data_with_no_caller_argument() -> None:
    """The only tenor path that is live TODAY: no kwarg passed, term read off the site.

    `SiteModel.provisioning_requirements[].tenor_years` is the customer provisioning contract's own
    length, which is exactly the "from the award itself" evidence class D-130 asks for.
    """
    result = _result(
        [
            (s.MarketStream.TARIFF_ARBITRAGE, 220_000.0, 12_000.0, s.Firmness.FIRM),
            (s.MarketStream.LOAD_SERVICE, 60_000.0, 0.0, s.Firmness.CONTRACTED),
        ]
    )
    with_record = _run(result, site=_site([_requirement(tenor_years=3.0)]))
    without = _run(result, site=_site())
    assert with_record.cashflows.contracted_tenor_years == ((_LS, 3.0),)
    assert without.cashflows.contracted_tenor_years == ()
    # The lender basis is where a 3-year obligation projected over 15 years does its damage.
    assert sum(with_record.cashflows.p90_contracted_ebitda_gbp) < sum(
        without.cashflows.p90_contracted_ebitda_gbp
    )


def test_a_requirement_with_no_declared_counterfactual_supplies_no_tenor() -> None:
    """`LoadServiceStream` prices the declared counterfactual and nothing else, so a requirement
    without one carries no revenue for a tenor to apply to."""
    tenors, warnings = tenors_from_provisioning_requirements(
        _site([_requirement(counterfactual=None)])
    )
    assert tenors == {}
    assert warnings == []


def test_one_untenored_priced_requirement_leaves_the_whole_stream_untenored() -> None:
    """Refusing is the honest option: one requirement's term must not cut another's revenue."""
    site = _site(
        [
            _requirement("pr_1", tenor_years=3.0),
            _requirement("pr_2", tenor_years=None),
        ]
    )
    tenors, warnings = tenors_from_provisioning_requirements(site)
    assert tenors == {}
    w = next(w for w in warnings if "pr_2" in w)
    assert "UN-tenored" in w and "D-130" in w
    assert "say nothing about" in w
    # And it must survive the trip through the orchestrator, not just the helper.
    model = _run(
        _result(
            [
                (s.MarketStream.TARIFF_ARBITRAGE, 220_000.0, 12_000.0, s.Firmness.FIRM),
                (s.MarketStream.LOAD_SERVICE, 60_000.0, 0.0, s.Firmness.CONTRACTED),
            ]
        ),
        site=site,
    )
    assert model.cashflows.contracted_tenor_years == ()
    assert any("pr_2" in w for w in model.case.warnings)


def test_differing_requirement_terms_collapse_to_the_shortest_and_say_so() -> None:
    """Bounded, never averaged: the tenor applies to the stream total, so only the shortest term is
    one that every part of that revenue is contracted for."""
    tenors, warnings = tenors_from_provisioning_requirements(
        _site([_requirement("pr_1", tenor_years=7.0), _requirement("pr_2", tenor_years=2.0)])
    )
    assert tenors == {_LS: 2.0}
    w = next(w for w in warnings if "differing terms" in w)
    assert "SHORTEST" in w and "never averaged" in w


# --- source 2: the typed AvailabilityCommitment payload -------------------


def test_a_commitment_payload_supplies_the_tenor_through_the_orchestrator() -> None:
    model = _run(commitments=[_commitment(s.MarketStream.CAPACITY_MARKET, 3.0)])
    assert model.cashflows.contracted_tenor_years == ((_CM, 3.0),)


def test_a_commitment_without_a_tenor_is_not_evidence_of_a_term() -> None:
    tenors, warnings = tenors_from_commitments([_commitment(s.MarketStream.CAPACITY_MARKET, None)])
    assert tenors == {}
    w = next(iter(warnings))
    assert "carries no tenor_years" in w and "D-130" in w
    assert "not evidence of one" in w


def test_two_commitments_on_one_stream_collapse_to_the_shortest_with_a_warning() -> None:
    tenors, warnings = tenors_from_commitments(
        [
            _commitment(s.MarketStream.CAPACITY_MARKET, 15.0, "cm_long"),
            _commitment(s.MarketStream.CAPACITY_MARKET, 3.0, "cm_short"),
        ]
    )
    assert tenors == {_CM: 3.0}
    w = next(w for w in warnings if "differing length" in w)
    assert "SHORTEST" in w and "never averaged" in w
    assert "cm_short" in w and "cm_long" in w


def test_no_commitments_is_silent_rather_than_warned() -> None:
    """Absence of a payload is the normal case and must not add noise to every quote."""
    assert tenors_from_commitments(None) == ({}, [])
    assert tenors_from_commitments([]) == ({}, [])


# --- precedence -----------------------------------------------------------


def test_an_explicit_tenor_overrides_derived_evidence_and_the_contradiction_is_warned() -> None:
    """A silent override would hide the fact that two records disagree about the term."""
    site = _site([_requirement(tenor_years=3.0)])
    tenors, warnings = resolve_contract_tenor_evidence(site, explicit={_LS: 10.0})
    assert tenors == {_LS: 10.0}
    w = next(w for w in warnings if "overriding" in w)
    assert "3y read from the site's own provisioning requirements" in w
    assert "one of them is wrong about the term" in w


def test_the_override_warning_names_the_source_it_actually_overrode() -> None:
    """A commitment-payload tenor must not be attributed to "the site's own records": a wrong
    provenance claim in a warning a lender reads is the same defect class as a misdescribed
    disclosure, which is why the DFS expiry reason had to be split out too."""
    _, warnings = resolve_contract_tenor_evidence(
        _site(),
        commitments=[_commitment(s.MarketStream.CAPACITY_MARKET, 15.0)],
        explicit={_CM: 3.0},
    )
    w = next(w for w in warnings if "overriding" in w)
    assert "15y read from a typed AvailabilityCommitment payload" in w
    assert "provisioning" not in w, "the commitment tenor did not come from the site's requirements"


def test_an_explicit_tenor_agreeing_with_the_record_adds_no_contradiction_warning() -> None:
    tenors, warnings = resolve_contract_tenor_evidence(
        _site([_requirement(tenor_years=3.0)]), explicit={_LS: 3.0}
    )
    assert tenors == {_LS: 3.0}
    assert not any("overriding" in w for w in warnings)


def test_evidence_from_different_sources_composes_rather_than_replacing() -> None:
    tenors, _ = resolve_contract_tenor_evidence(
        _site([_requirement(tenor_years=3.0)]),
        commitments=[_commitment(s.MarketStream.CAPACITY_MARKET, 15.0)],
        explicit={_FLEX: 1.0},
    )
    assert tenors == {_LS: 3.0, _CM: 15.0, _FLEX: 1.0}


# --- N25 / MD-F-14: the applied tenor joins the run identity --------------


def test_two_tenor_treatments_cannot_share_a_case_id() -> None:
    """A fixed identity over a moved number is worse than the number never having moved."""
    none = _run()
    long_award = _run(contract_tenor_years={_CM: 15.0})
    short_award = _run(contract_tenor_years={_CM: 2.0})
    ids = {none.case.case_id, long_award.case.case_id, short_award.case.case_id}
    assert len(ids) == 3, "two different tenor treatments collided on one case_id (N25/MD-F-14)"
    assert none.run_context.input_hash != long_award.run_context.input_hash


def test_a_tenor_that_was_dropped_as_unused_does_not_split_case_identity() -> None:
    """It changed no arithmetic, so it must not change the identity: the hash pins the APPLIED
    tuple, not the request. A mis-keyed tenor for a stream with no contracted revenue is dropped."""
    merchant = _result([(s.MarketStream.TARIFF_ARBITRAGE, 220_000.0, 12_000.0, s.Firmness.FIRM)])
    plain = _run(merchant)
    with_unused = _run(merchant, contract_tenor_years={_CM: 5.0})
    assert with_unused.cashflows.contracted_tenor_years == ()
    assert plain.case.case_id == with_unused.case.case_id
    assert any("prices no CONTRACTED revenue this run" in w for w in with_unused.case.warnings)


def test_the_evidence_free_hash_input_is_byte_identical_to_the_pre_change_shape() -> None:
    """The new key is OMITTED when no tenor was applied, so no historical run is re-identified."""
    site, result = _site(), _MIXED
    library = load_assumptions("gb_v1_6")
    without = _hash_input(site, result, library, _HORIZON, None, _DISPATCH)
    assert "contract_tenor_years" not in without
    with_tenor = _hash_input(site, result, library, _HORIZON, None, _DISPATCH, ((_CM, 2.0),))
    assert with_tenor["contract_tenor_years"] == [[_CM, 2.0]]
    assert {k: v for k, v in with_tenor.items() if k != "contract_tenor_years"} == without


def test_the_hash_key_is_json_safe_so_the_run_id_can_actually_be_derived() -> None:
    """A tuple key would raise inside `hash_json`; the payload uses lists. Pinned because the
    failure mode is a crash on exactly the path this change set exists to enable."""
    from gridq.provenance import hash_json

    payload = _hash_input(
        _site(), _MIXED, load_assumptions("gb_v1_6"), _HORIZON, None, _DISPATCH, ((_CM, 2.0),)
    )
    assert hash_json(payload).startswith("sha256:")


# --- direction of travel: what a supplied tenor does to the headline -----


def test_d33_in_term_flat_treatment_raises_p50_and_the_project_irr() -> None:
    """D-33 makes the case BETTER in term: a fixed availability payment stops being faded.

    Stated separately from D-130 because the two move opposite ways and the same parameter carries
    both. The lift is exactly the fade the CM line was wrongly bearing, so it is zero in year 1
    (decay == 1) and grows every year of the term.
    """
    base, tenored = _run(), _run(contract_tenor_years={_CM: 15.0})
    fade = float(load_assumptions("gb_v1_6").get("degradation", "annual_capacity_fade_pct").value)
    cm = 45_000.0
    assert tenored.cashflows.p50_ebitda_gbp[0] == pytest.approx(
        base.cashflows.p50_ebitda_gbp[0], abs=0.02
    )
    for year in (2, 8, 15):
        lift = cm - cm * (1.0 - fade / 100.0) ** (year - 1)
        assert tenored.cashflows.p50_ebitda_gbp[year - 1] - base.cashflows.p50_ebitda_gbp[
            year - 1
        ] == pytest.approx(lift, abs=0.02)
    assert base.case.irr is not None and tenored.case.irr is not None
    assert tenored.case.irr > base.case.irr
    assert tenored.case.payback_years < base.case.payback_years
    assert tenored.case.equity_irr > base.case.equity_irr


def test_d130_is_p50_neutral_after_expiry_and_bites_only_the_cautious_cases() -> None:
    """D-130 RECLASSIFIES post-expiry revenue rather than removing it from P50.

    Post-expiry the £ continues in P50 as merchant-class re-contracting at the same fade, so the
    headline project IRR / equity IRR / payback do not move on D-130's account at all. What moves is
    the lender basis, which excludes a re-contracting expectation outright.
    """
    base = _run()
    tenored = _run(contract_tenor_years={_CM: 2.0})
    for year in (3, 8, 15):  # every post-expiry year
        assert tenored.cashflows.p50_ebitda_gbp[year - 1] == pytest.approx(
            base.cashflows.p50_ebitda_gbp[year - 1], abs=0.02
        )
        assert (
            tenored.cashflows.p90_contracted_ebitda_gbp[year - 1]
            < base.cashflows.p90_contracted_ebitda_gbp[year - 1]
        )
    assert sum(tenored.cashflows.p90_contracted_ebitda_gbp) < sum(
        base.cashflows.p90_contracted_ebitda_gbp
    )


def test_a_terminable_flex_award_makes_p90_and_the_min_dscr_strictly_worse() -> None:
    """The clean D-130 case: local flex is on the no-P90-after-expiry allowlist, so the cautious
    case loses the whole post-expiry line and the covenant measurement follows it down."""
    base = _run()
    tenored = _run(contract_tenor_years={_FLEX: 1.0})
    assert sum(tenored.cashflows.p50_ebitda_gbp) == pytest.approx(
        sum(base.cashflows.p50_ebitda_gbp), abs=0.05
    )
    assert sum(tenored.cashflows.p90_ebitda_gbp) < sum(base.cashflows.p90_ebitda_gbp)
    assert base.case.dscr_summary.min_dscr is not None
    assert tenored.case.dscr_summary.min_dscr is not None
    assert tenored.case.dscr_summary.min_dscr < base.case.dscr_summary.min_dscr
    # And the headline P50 metrics are untouched, so the two directions cannot be conflated.
    assert tenored.case.irr == base.case.irr


# --- DFS on the no-P90-after-expiry allowlist ----------------------------


def test_dfs_post_expiry_p90_is_removed_like_local_flex() -> None:
    """DFS is procured season by season with no guarantee the service runs in a future winter, so
    post-expiry DFS revenue would assume both the scheme's continuation and a re-award. Neither is
    contracted, and DFS is the only sub-1 MW NESO route, so it is the line this matters most for."""
    dfs = _result(
        [
            (s.MarketStream.TARIFF_ARBITRAGE, 220_000.0, 12_000.0, s.Firmness.FIRM),
            (s.MarketStream.DFS, 20_000.0, 0.0, s.Firmness.CONTRACTED),
        ]
    )
    base, tenored = _run(dfs), _run(dfs, contract_tenor_years={_DFS: 1.0})
    haircut = float(
        load_assumptions("gb_v1_6").get("p50_p90_haircuts", "p90_revenue_haircut_pct").value
    )
    fade = float(load_assumptions("gb_v1_6").get("degradation", "annual_capacity_fade_pct").value)
    for year in (2, 8, 15):
        decay = (1.0 - fade / 100.0) ** (year - 1)
        removed = 20_000.0 * decay * (1.0 - haircut / 100.0)
        assert base.cashflows.p90_ebitda_gbp[year - 1] - tenored.cashflows.p90_ebitda_gbp[
            year - 1
        ] == pytest.approx(removed, abs=0.05)
        # P50 keeps the £ as merchant-class re-contracting: this is a cautious-case change only.
        assert tenored.cashflows.p50_ebitda_gbp[year - 1] == pytest.approx(
            base.cashflows.p50_ebitda_gbp[year - 1], abs=0.02
        )
    assert tenored.case.dscr_summary.min_dscr < base.case.dscr_summary.min_dscr


def test_dfs_gets_its_own_expiry_reason_rather_than_the_dso_zone_one() -> None:
    """A wrong reason attached to a right treatment is still a defect in a pack a lender reads:
    DFS is not a DSO award and has no constraint zone."""
    dfs = _result(
        [
            (s.MarketStream.TARIFF_ARBITRAGE, 220_000.0, 12_000.0, s.Firmness.FIRM),
            (s.MarketStream.DFS, 20_000.0, 0.0, s.Firmness.CONTRACTED),
        ]
    )
    w = next(
        w
        for w in _run(dfs, contract_tenor_years={_DFS: 1.0}).case.warnings
        if f"Contracted stream {_DFS} is tenored" in w
    )
    assert "removed from P90 entirely" in w
    assert "season by season" in w
    assert "DSO award" not in w, "the DSO-zone reason is local flex's, not DFS's"


def test_every_no_p90_stream_carries_a_reason_in_its_own_terms() -> None:
    """Adding a stream to the allowlist without a reason must fail here rather than emit a
    misdescribed disclosure into an underwriting pack."""
    assert set(_NO_P90_AFTER_EXPIRY) == set(_NO_P90_REASON)
    assert _NO_P90_AFTER_EXPIRY >= {_FLEX, _DFS}
    for stream, reason in _NO_P90_REASON.items():
        assert reason and reason[0].islower() or reason.startswith(stream), reason
        assert "—" not in reason, f"{stream}: em dash in text rendered into the pack"


# --- determinism ---------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"contract_tenor_years": {_CM: 2.0, _FLEX: 1.0}},
        {"commitments": [_commitment(s.MarketStream.CAPACITY_MARKET, 3.0)]},
    ],
)
def test_the_threaded_path_is_deterministic(kwargs: dict[str, Any]) -> None:
    first, second = _run(**kwargs), _run(**kwargs)
    assert first.case.model_dump(mode="json") == second.case.model_dump(mode="json")
    assert first.cashflows.contracted_tenor_years == second.cashflows.contracted_tenor_years


# --- D-130 (2026-11-23): the site's OWN award tenor reaches finance on every call path ---------
#
# Before D-130 no production caller passed ``commitments=``: the quote, the scenario sensitivities
# and the sizing loop's finance runner all omitted it, so a one-year award earned contracted
# revenue for the whole horizon. `run_financial_model` now reads ``site.local_flex_awards`` itself
# (ahead of any caller-supplied commitments). Owner decision D66 (option A): once the term is
# known, the in-term award is absent from P90 and from the lender series in EVERY year, year 1
# included (`_NO_P90_IN_TERM`, D-139). P50 keeps the faded re-contracting expectation (D65).

from tests._milp_support import ssen_awarded_site, write_hh_csv  # noqa: E402

import gridq.quotes.pipeline as _pipeline  # noqa: E402
from gridq.optimisation.engine import MilpEngine  # noqa: E402
from gridq.optimisation.milp_solver import MilpDispatchSolver  # noqa: E402
from gridq.sizing.objective import SizingObjective  # noqa: E402
from gridq.sizing.search_space import SearchSpace  # noqa: E402
from gridq.sizing.sizer import enumerate_sizing  # noqa: E402

_IN_TERM_PREFIX = f"Contracted stream {_FLEX} earns £"
_IN_TERM_ZERO = "ZERO for the whole award term"
_DROPPED = "prices no CONTRACTED revenue"


@pytest.fixture(scope="module")
def awarded_site(tmp_path_factory: pytest.TempPathFactory) -> s.SiteModel:
    """A 7-day January synthetic series carrying a SYNTHETIC attested award (200 kW, tenor 1 yr)
    in the SSEN SEPD licence area: the cheapest site on which the local-flex leg is actually
    PRICED as CONTRACTED by a real MILP solve (a stream priced at £0 would have its tenor dropped,
    which is the STOP condition these tests exist to catch). It used to copy Harwell's own award
    out of a quarantined customer file (WP-GRC-05)."""
    path = tmp_path_factory.mktemp("d130") / "january.csv"
    rows, _ts = write_hh_csv(path, days=7)
    site = ssen_awarded_site(path, rows=rows)
    assert site.local_flex_awards and site.local_flex_awards[0].tenor_years == 1.0
    return site


def test_site_award_tenor_reaches_finance_from_generate_quote(
    awarded_site: s.SiteModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T10 (D-130): `generate_quote` on an awarded site applies the award's own 1-year term.

    The pipeline still passes no ``commitments=`` (asserted), so the tenor can only have come from
    the site, read inside `run_financial_model`. Dies if that line is deleted (M-T10).
    """
    recorded: list[tuple[dict[str, Any], Any]] = []
    real = _pipeline.run_financial_model

    def _record(*args: Any, **kwargs: Any) -> Any:
        model = real(*args, **kwargs)
        recorded.append((dict(kwargs), model))
        return model

    monkeypatch.setattr(_pipeline, "run_financial_model", _record)
    run = _pipeline.generate_quote(
        awarded_site,
        load_assumptions("gb_v1_12"),
        engine=MilpEngine(use_two_stage_search=False),
    )

    assert len(recorded) == 1, "the quote's own finance run was not reached"
    kwargs, model = recorded[0]
    assert "commitments" not in kwargs, "the tenor must come from the site, not a caller"
    # Vacuity guard: the leg is priced as CONTRACTED revenue, so a tenor has something to bind.
    flex = next(v for v in run.result.value_streams if v.stream == s.MarketStream.LOCAL_FLEX_PICLO)
    assert flex.firmness == s.Firmness.CONTRACTED and flex.annual_revenue_gbp > 0.0
    assert (_FLEX, 1.0) in model.cashflows.contracted_tenor_years
    warnings = run.quote.warnings
    assert any(f"Contracted stream {_FLEX} is tenored at 1 year(s)" in w for w in warnings)
    assert any(_IN_TERM_PREFIX in w and _IN_TERM_ZERO in w for w in warnings), (
        "the in-term P90 disclosure must reach the quote once the award's term is known"
    )
    assert not any(_DROPPED in w for w in warnings), "the award's tenor was dropped as unused"


def test_sizing_finance_runner_sees_the_award_tenor(awarded_site: s.SiteModel) -> None:
    """T11 (D-130): the sizing loop ranks candidates on the same award term the quote prices.

    The recording runner refuses a ``commitments`` keyword, so the 1-year tenor on every returned
    model can only have come from the site. Dies if the D-130 line is moved to `pipeline.py` only
    (M-T10).
    """
    models: list[Any] = []

    def _recording_runner(*args: Any, **kwargs: Any) -> Any:
        assert "commitments" not in kwargs, "the sizer must not need to pass the award"
        model = run_financial_model(*args, **kwargs)
        models.append(model)
        return model

    enumerate_sizing(
        awarded_site,
        load_assumptions("gb_v1_12"),
        SearchSpace(power_kw_candidates=[100.0], duration_hour_candidates=[2.0]),
        dispatch_solver=MilpDispatchSolver(),
        objective=SizingObjective(min_p90_dscr_covenant=1.3),
        finance_runner=_recording_runner,
    )
    assert models, "no candidate reached the finance runner"
    for model in models:
        assert model.cashflows.annual_revenue_by_stream_gbp.get(_FLEX, 0.0) > 0.0
        assert (_FLEX, 1.0) in model.cashflows.contracted_tenor_years


def test_one_year_award_leaves_the_cautious_and_lender_cases_in_every_year() -> None:
    """T12 (D-130 with owner decision D66, option A; replaces the plan's T12).

    The same £15,000 CONTRACTED local-flex line, priced without (``base``) and with the site's own
    1-year award on file (``awarded``; no ``commitments=`` and no ``contract_tenor_years=``). With
    the term known, the line is in term in year 1, where `_NO_P90_IN_TERM` zeroes its P90 (D-139),
    and expired from year 2, where `_NO_P90_AFTER_EXPIRY` removes it. So P90 and the lender series
    lose exactly what the un-tenored line contributed, in every one of the 15 years:
    ``15,000 x (1 - fade/100)^(y-1) x (1 - haircut/100)``. P50 keeps the faded re-contracting
    expectation (owner decision D65); only its total is asserted.
    """
    library = load_assumptions("gb_v1_6")
    haircut = float(library.get("p50_p90_haircuts", "p90_revenue_haircut_pct").value)
    fade = float(library.get("degradation", "annual_capacity_fade_pct").value)
    base = _run(library=library)
    awarded = _run(
        library=library,
        site=_site().model_copy(
            update={"local_flex_awards": [_commitment(s.MarketStream.LOCAL_FLEX_PICLO, 1.0)]}
        ),
    )

    assert (_FLEX, 1.0) in awarded.cashflows.contracted_tenor_years
    # Vacuity guard: the case prices the line, so the deltas are not differences between zeros.
    assert base.cashflows.annual_revenue_by_stream_gbp[_FLEX] == pytest.approx(15_000.0)
    assert haircut < 100.0, "a 100% class haircut would hide the removal"

    def _removed(year: int) -> float:
        return 15_000.0 * (1.0 - fade / 100.0) ** (year - 1) * (1.0 - haircut / 100.0)

    p90_b, p90_a = base.cashflows.p90_ebitda_gbp, awarded.cashflows.p90_ebitda_gbp
    lend_b, lend_a = (
        base.cashflows.p90_contracted_ebitda_gbp,
        awarded.cashflows.p90_contracted_ebitda_gbp,
    )
    assert len(p90_a) == len(lend_a) == _HORIZON
    assert p90_b[0] - p90_a[0] == pytest.approx(_removed(1), abs=0.05), (
        "the contracted year is not counted either (D-139, owner decision D66)"
    )
    assert lend_b[0] - lend_a[0] == pytest.approx(_removed(1), abs=0.05), (
        "the contracted year is not counted either (D-139, owner decision D66)"
    )
    for year in range(2, _HORIZON + 1):
        assert p90_b[year - 1] - p90_a[year - 1] == pytest.approx(_removed(year), abs=0.05)
        assert lend_b[year - 1] - lend_a[year - 1] == pytest.approx(_removed(year), abs=0.05)

    assert sum(awarded.cashflows.p50_ebitda_gbp) == pytest.approx(
        sum(base.cashflows.p50_ebitda_gbp), abs=0.05
    )
    assert any(_IN_TERM_PREFIX in w and _IN_TERM_ZERO in w for w in awarded.case.warnings)
