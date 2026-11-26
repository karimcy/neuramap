"""The D46 study basis through `MilpEngine` — on the real linopy/HiGHS solver.

What has to be true, and is asserted here:

1. **An underwriting run is untouched.** No basis ⇒ the plain `MilpDispatchSolver`, no export
   variable in the model at all (structural, not tolerance), and a result dump identical to the one
   an explicit ``study_basis=None`` produces.
2. **A study basis reaches every candidate.** The export £ is inside the candidate's own
   `DispatchResult`, so it moves the priced floor, the P50/P90 cashflows, the DSCR and the IRRs —
   which is the whole difference between D46 and the `ExportFeasibilityOverride` that preceded it.
   Pinned on the proxy path too, because that is where the two-stage search spends its solves.
3. **Exactly ONE netting site (MD-F-05 limb b).** The floor's dispatch-derived components come from
   the export dispatch itself and the export entry is GROSS, so ``floor total == the export
   dispatch's own objective terms + gross export``. Verified as arithmetic, not assumed.
4. **Consent is not moved.** `export_dispatch_enabled` stays false, no consent / MEC / export-MPAN /
   `stream_consents` field is written anywhere, and every channel says "requires customer consent".
5. **A second export mechanism is refused, not merged.**

The fixture is a deliberately LOW-LOAD site on a large connection — the shape of every site in the
live campaign, and the shape that makes the question interesting: ``discharge ≤ min(power, load)``
strands most of the battery's capability behind the meter, so export is the only route to market for
it.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from tests.test_study_basis_schema import consent_record, limit_record, study, tariff_record

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem, dispatch_constraints_for
from gridq.engine.errors import InvalidRequestError
from gridq.ingestion.market_forecasts import (
    MarketForecast,
    MarketForecastPoint,
    hash_market_forecast,
)
from gridq.optimisation.engine import ExportFeasibilityOverride, MilpEngine
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.proxy_solver import PreloadedDispatchSolver
from gridq.schemas import (
    ConnectionDetails,
    ConsentState,
    ConsentStatus,
    Counterparty,
    DataTier,
    Firmness,
    LoadDataSummary,
    MarketStream,
    PhysicalGateStatus,
    RateUnit,
    RevenueStreamConsent,
    SiteModel,
    SolverStatus,
    StudyBasis,
    TariffStack,
)
from gridq.value_stack.gates import export_dispatch_enabled

#: Above the fixture's night delivered charge cost grossed up for round-trip losses plus the
#: marginal cycle cost (~£0.169/kWh), and below its daytime behind-the-meter value (~£0.33/kWh), so
#: the model prefers behind-the-meter discharge where the load can absorb it and exports only the
#: capability the load strands. That is the trade the study basis exists to price.
EXPORT_TARIFF = 0.25
EXPORT_LIMIT_KW = 400.0
DAYS = 7
LOAD_KW = 20.0
MIC_KW = 500.0


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def fixture(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    """A low-load / large-connection site over a 7-day metered series, plus the library."""
    path = tmp_path_factory.mktemp("study_basis") / "hh.csv"
    n = DAYS * 48
    ts = pd.date_range("2025-01-06 00:00:00", periods=n, freq="30min")
    load = np.full(n, LOAD_KW)
    pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"), "import_kw": load}).to_csv(
        path, index=False
    )
    site = SiteModel(
        site_id="site_study_basis",
        postcode="LE17 4XN",
        data_tier=DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=LoadDataSummary(
            annual_kwh=LOAD_KW * 8760,
            half_hourly_file_ref=str(path),
            half_hourly_rows=n,
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 13),
            max_import_kw=LOAD_KW,
        ),
        annual_consumption_kwh=LOAD_KW * 8760,
        tariff_stack=TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="StudyTestCo",
            import_rates={"day": 0.30, "night": 0.10},
            duos_red_rate=0.10,
            levies={"ro": 0.02, "fit": 0.01},
        ),
        connection=ConnectionDetails(mic_kw=MIC_KW, asc_kw=MIC_KW),  # NOTE: no mec_kw
        consents=ConsentState(),
        physical_gates=PhysicalGateStatus(),
        counterparty=Counterparty(legal_name="Study Test Ltd", company_number="01234567"),
    )
    return site, load_assumptions("gb_v0_1"), ts, load


@pytest.fixture(scope="module")
def basis() -> StudyBasis:
    return study(
        export_limit_kw=EXPORT_LIMIT_KW,
        export_tariff_gbp_per_kwh=EXPORT_TARIFF,
        tariff_record=tariff_record(
            value=EXPORT_TARIFF,
            sources=["SYNTHETIC TEST SERIES (sha256:test); vintage 2026-07-13"],
        ),
        export_limit_record=limit_record(value=EXPORT_LIMIT_KW),
        consent_record=consent_record(),
    )


def _problem(site, lib, *, power_kw: float = 200.0, energy_kwh: float = 400.0):
    streams = list(MarketStream)
    return DispatchProblem(
        site_model=site,
        assumptions=lib,
        power_kw=power_kw,
        energy_kwh=energy_kwh,
        duration_hours=energy_kwh / power_kw,
        requested_streams=streams,
        constraints=dispatch_constraints_for(streams, site, lib),
        scenario=None,
    )


def _floor_total(streams) -> float:
    priced = {Firmness.FIRM, Firmness.CONTRACTED}
    return round(
        sum(v.annual_revenue_gbp - v.annual_cost_gbp for v in streams if v.firmness in priced), 2
    )


def _wholesale(streams):
    return next((v for v in streams if v.stream == MarketStream.WHOLESALE_P415), None)


@pytest.fixture(scope="module")
def arms(fixture, basis) -> tuple:
    """The same site solved on both bases through the legacy 3x3 grid (identical candidate sets)."""
    site, lib, _ts, _load = fixture
    under = MilpEngine(use_two_stage_search=False)
    under_res = under.solve(under.build_optimisation_request(site, lib))
    studied = MilpEngine(use_two_stage_search=False, study_basis=basis)
    studied_res = studied.solve(studied.build_optimisation_request(site, lib))
    return under, under_res, studied, studied_res


# --------------------------------------------------------------------------- 1. untouched


def test_an_underwriting_run_uses_the_plain_solver_and_creates_no_export_variable(fixture) -> None:
    site, lib, _ts, _load = fixture
    engine = MilpEngine(use_two_stage_search=False)
    assert type(engine._sizing_solver) is MilpDispatchSolver
    assert engine._sizing_solver.MODEL_VERSION == "highs-milp-v1"
    assert engine.last_study_basis() is None
    engine.solve(engine.build_optimisation_request(site, lib))
    schedule = engine._dispatch_solver.last_schedule()
    assert schedule is not None
    # Structural byte-identity, not tolerance: no export variable exists in the model at all.
    assert schedule.export_kw is None
    assert schedule.annual_export_mwh is None


def test_study_basis_none_is_identical_to_omitting_it(fixture) -> None:
    site, lib, _ts, _load = fixture
    a = MilpEngine(use_two_stage_search=False)
    b = MilpEngine(use_two_stage_search=False, study_basis=None)
    res_a = a.solve(a.build_optimisation_request(site, lib))
    res_b = b.solve(b.build_optimisation_request(site, lib))
    dump_a = res_a.model_dump(mode="json", exclude={"created_at"})
    dump_b = res_b.model_dump(mode="json", exclude={"created_at"})
    assert dump_a == dump_b


def test_an_underwriting_run_carries_no_study_language(arms) -> None:
    _under, under_res, _studied, _studied_res = arms
    assert not any(StudyBasis.LABEL in w for w in under_res.warnings)
    assert under_res.solver_metadata.model_version == "highs-milp-v1"
    assert _wholesale(under_res.value_streams) is None  # export is not in the floor


# --------------------------------------------------------------------------- 2. reaches candidates


def test_the_study_basis_prices_export_into_the_floor(arms) -> None:
    _under, _under_res, _studied, studied_res = arms
    entry = _wholesale(studied_res.value_streams)
    assert entry is not None, "the study basis must put a WHOLESALE_P415 entry in value_streams"
    assert entry.firmness is Firmness.CONTRACTED  # the firmness class finance prices in the floor
    assert entry.annual_revenue_gbp > 0.0
    assert studied_res.selected_power_kw is not None


def test_the_study_basis_moves_the_priced_floor_p50_p90_dscr_and_the_irrs(arms) -> None:
    """Requirement 3, per candidate: the £ is inside the candidate's own dispatch and finance."""
    under, _under_res, studied, _studied_res = arms
    by_spec_under = {
        (round(e.candidate.power_kw, 6), round(e.candidate.duration_hours, 6)): e
        for e in under.last_sizing_run().candidates_evaluated
    }
    moved = 0
    irr_improved = 0
    for e in studied.last_sizing_run().candidates_evaluated:
        key = (round(e.candidate.power_kw, 6), round(e.candidate.duration_hours, 6))
        base = by_spec_under[key]  # the legacy grid is deterministic: identical candidate sets
        if not (e.priceable and base.priceable):
            continue
        assert e.financial is not None and base.financial is not None
        floor_study = _floor_total(e.optimisation_result.value_streams)
        floor_under = _floor_total(base.optimisation_result.value_streams)
        if floor_study <= floor_under:
            continue
        moved += 1
        # Every downstream finance figure follows the floor, which is the point of D46.
        assert sum(e.financial.case.p50_cashflows) > sum(base.financial.case.p50_cashflows)
        assert sum(e.financial.case.p90_cashflows) > sum(base.financial.case.p90_cashflows)
        # The project IRR must not move the WRONG way. ``None`` is not "worse than a number": it
        # means the cashflows never change sign, so no IRR exists — a candidate can therefore
        # improve from None to a (still negative) IRR, which is what a floor increase does here.
        if base.project_irr_pct is None:
            irr_improved += e.project_irr_pct is not None
        else:
            assert e.project_irr_pct is not None
            assert e.project_irr_pct > base.project_irr_pct
            irr_improved += 1
    assert moved, "no candidate's priced floor moved — the study basis reached nothing"
    assert irr_improved, "the priced floor moved but no candidate's project IRR followed it"


def test_the_p90_series_is_haircut_below_the_p50_series(arms) -> None:
    """The contracted haircut still applies: a study basis is not a risk-free basis."""
    _under, _under_res, studied, _studied_res = arms
    priced = [e for e in studied.last_sizing_run().candidates_evaluated if e.financial is not None]
    assert priced
    for e in priced:
        case = e.financial.case
        assert sum(case.p90_cashflows) <= sum(case.p50_cashflows)


def test_finance_receives_the_study_dispatchs_own_volumes(arms) -> None:
    """The extra import the export required must land on the cost side, not vanish."""
    under, _under_res, studied, _studied_res = arms
    study_energy = studied.selected_dispatch_energy()
    under_energy = under.selected_dispatch_energy()
    assert study_energy is not None and under_energy is not None
    schedule = studied._dispatch_solver.last_schedule()
    assert schedule is not None
    assert study_energy.annual_charge_kwh == pytest.approx(schedule.annual_charge_kwh)
    # BTM discharge only: an exported kWh displaces no site import, so it is not delivered energy.
    assert study_energy.annual_discharge_kwh == pytest.approx(schedule.annual_discharge_kwh)
    assert study_energy.annual_charge_kwh > under_energy.annual_charge_kwh


def test_the_study_entry_appears_on_a_typical_period_PROXY_solve_too(fixture, basis) -> None:
    """Pins the dispatch mechanics the whole design rests on (the two-stage search's hot path).

    `PreloadedDispatchSolver` calls ``self._base._solve_milp`` / ``._solved`` on the instance it was
    handed, so the study subclass's overrides fire on the proxy sweep as well as on the confirm
    stage. If this ever stopped being true the study basis would silently price only the confirmed
    candidates and rank every other one on the underwriting basis.
    """
    site, lib, _ts, _load = fixture
    from gridq.optimisation.engine import _StudyBasisDispatchSolver

    base = _StudyBasisDispatchSolver(basis, seed=0)
    load, timestamps, interval_min, _warn = base._load_series(site.load_data.half_hourly_file_ref)
    weights = np.full(len(load), 2.0)  # a weighted (proxy) solve
    proxy = PreloadedDispatchSolver(
        base, load=load, timestamps=timestamps, interval_min=interval_min, interval_weights=weights
    )
    assert proxy.MODEL_VERSION == f"highs-milp-v1{StudyBasis.SOLVER_SUFFIX}-proxy"
    result = proxy.solve(_problem(site, lib))
    assert result.solver_status is SolverStatus.OPTIMAL
    entry = _wholesale(result.value_streams)
    assert entry is not None and entry.firmness is Firmness.CONTRACTED
    assert entry.annual_revenue_gbp > 0.0


# --------------------------------------------------------------------------- 3. one netting site


def test_the_floor_equals_the_export_dispatchs_own_terms_plus_gross_export(fixture, basis) -> None:
    """MD-F-05 limb (b) as arithmetic: ONE netting site, and the export entry is GROSS.

    The floor's dispatch-derived components are priced from the export dispatch, so the displaced
    behind-the-meter value is already out of them; adding gross export therefore reproduces the
    solved objective terms plus the export revenue exactly. If a second netting were applied
    anywhere, this identity would fail by the doubly-deducted amount.
    """
    from gridq.optimisation.engine import _StudyBasisDispatchSolver

    site, lib, _ts, _load = fixture
    solver = _StudyBasisDispatchSolver(basis, seed=0)
    result = solver.solve(_problem(site, lib))
    schedule = solver.last_schedule()
    assert schedule is not None and schedule.annual_export_mwh is not None
    entry = _wholesale(result.value_streams)
    assert entry is not None
    gross = round(basis.export_tariff_gbp_per_kwh * schedule.annual_export_mwh * 1000.0, 2)
    assert entry.annual_revenue_gbp == pytest.approx(gross, abs=0.01)
    assert entry.annual_cost_gbp == 0.0  # gross: no deduction on this entry
    # The identity is over the DISPATCH-DERIVED floor components plus gross export, and only those.
    # D57 (2026-08-18) put six further streams into the priced floor (CM, local flex, DFS, BM,
    # ancillary, agreed-capacity reduction, load service), none of which is a term in
    # ``priced_floor_terms_gbp`` - that property is the arbitrage + DUoS objective term by
    # definition. Summing the whole floor here would compare a total against a subtotal and would
    # fail by the amount of every stream this test is not about.
    dispatch_derived = {
        MarketStream.TARIFF_ARBITRAGE,
        MarketStream.DUOS_RED_BAND_AVOIDANCE,
        MarketStream.WHOLESALE_P415,
    }
    assert _floor_total(
        [v for v in result.value_streams if v.stream in dispatch_derived]
    ) == pytest.approx(round(schedule.priced_floor_terms_gbp + gross, 2), abs=0.02)


def test_the_displaced_btm_value_is_reported_as_a_check_never_deducted(fixture, basis) -> None:
    site, lib, _ts, _load = fixture
    engine = MilpEngine(use_two_stage_search=False, study_basis=basis)
    result = engine.solve(engine.build_optimisation_request(site, lib))
    check = engine.last_study_netting_check()
    assert check is not None
    assert "measured once, applied never" in check
    assert "exactly ONE netting site" in check
    assert any("MD-F-05(b) CHECK" in w for w in result.warnings)
    # The check is a sentence, not a number anyone subtracts: the entry stays gross.
    entry = _wholesale(result.value_streams)
    assert entry is not None and entry.annual_cost_gbp == 0.0


def test_every_affected_floor_component_carries_the_basis_label(arms) -> None:
    """D46: EVERY affected figure, not just the export entry — a stream renders on its own.

    Tariff arbitrage and DUoS red-band avoidance are priced from the dispatch that exported, so they
    are already net of the displaced behind-the-meter value and are NOT the figures this site would
    report on the underwriting basis. An unlabelled one of those is exactly the "reader mistakes one
    basis for the other" risk D46 records.
    """
    _under, under_res, _studied, studied_res = arms
    affected = {MarketStream.TARIFF_ARBITRAGE, MarketStream.DUOS_RED_BAND_AVOIDANCE}
    seen = 0
    for v in studied_res.value_streams:
        if v.stream not in affected or v.firmness not in {Firmness.FIRM, Firmness.CONTRACTED}:
            continue
        seen += 1
        assert any(w.startswith(StudyBasis.LABEL) for w in v.warnings), v.stream
    assert seen, "no dispatch-derived floor component was priced at all"
    # ...and the same components on an UNDERWRITING run carry no such label.
    for v in under_res.value_streams:
        assert not any(StudyBasis.LABEL in w for w in v.warnings)


def test_exactly_one_wholesale_entry_exists_on_a_study_basis_run(arms) -> None:
    """D57 (2026-08-18): the GBP 0 gated placeholder is DROPPED, not relabelled.

    **What this replaces, and why.** It used to require the placeholder to exist and to carry the
    sentence "NOT a second tranche of export value". The label was correct prose - and a keyed
    reader
    never saw it. Two entries carried the same ``stream`` id, so any consumer resolving
    WHOLESALE_P415 by name (a report template, the D57 acceptance matrix, a spreadsheet pivot) got
    whichever it read last, and a priced CONTRACTED floor entry could be shadowed by a GBP 0
    placeholder - the stream then reads as unearned on the very layer a reader reads.

    So the invariant is now stronger and machine-checkable: on a study-basis run there is EXACTLY
    ONE
    WHOLESALE_P415 entry, it is the priced floor entry, and the gated list carries none. It dies if
    the placeholder is reinstated, and it dies if the floor entry is dropped.
    """
    _under, under_res, _studied, studied_res = arms
    assert _wholesale(studied_res.gated_upside) is None
    floor_entries = [
        v for v in studied_res.value_streams if v.stream == MarketStream.WHOLESALE_P415
    ]
    assert len(floor_entries) == 1
    assert floor_entries[0].firmness == Firmness.CONTRACTED
    # On the UNDERWRITING basis nothing moves: the gated entry is where the whole export value sits.
    assert _wholesale(under_res.gated_upside) is not None
    assert not [v for v in under_res.value_streams if v.stream == MarketStream.WHOLESALE_P415]


# --------------------------------------------------------------------------- 4. consent untouched


def test_no_consent_field_is_written_anywhere(fixture, basis) -> None:
    site, lib, _ts, _load = fixture
    before = site.model_dump(mode="json")
    engine = MilpEngine(use_two_stage_search=False, study_basis=basis)
    engine.solve(engine.build_optimisation_request(site, lib))
    assert site.model_dump(mode="json") == before
    assert site.connection.mec_kw is None
    assert site.consents.g99 is not ConsentStatus.APPROVED
    assert site.consents.export_mpan is None
    assert site.stream_consents is None
    assert export_dispatch_enabled(site) is False


def test_every_channel_says_the_export_requires_customer_consent(arms) -> None:
    _under, _under_res, _studied, studied_res = arms
    # The run as a whole: the banner is FIRST, so a reader cannot reach a figure before the basis.
    assert studied_res.warnings[0].startswith(StudyBasis.LABEL)
    assert "REQUIRES CUSTOMER CONSENT" in studied_res.warnings[0]
    # The basis is machine-readable on the record, not only in prose.
    assert studied_res.solver_metadata.model_version.endswith(StudyBasis.SOLVER_SUFFIX)
    # The entry itself, which can be read and rendered on its own.
    entry = _wholesale(studied_res.value_streams)
    assert entry is not None
    assert entry.consent_status is None  # nothing was consented
    assert entry.gate_status == "study_basis_export_in_floor"  # not an OPEN gate
    assert entry.unlock_condition and "requires customer consent" in entry.unlock_condition
    joined = " ".join(entry.warnings)
    assert StudyBasis.LABEL in joined
    assert "requires customer consent" in joined  # the D45 pending_permission label
    assert "NOT a claim that a contract exists" in joined
    assert StudyBasis.TARIFF_KEY in entry.assumptions_used


def test_a_no_spec_study_run_keeps_the_verdict_first_and_still_discloses_the_basis(
    fixture, basis
) -> None:
    """`_no_spec_result` puts ``reason`` first — the banner must not displace the verdict."""
    site, lib, _ts, _load = fixture
    blind = site.model_copy(
        update={"load_data": site.load_data.model_copy(update={"half_hourly_file_ref": None})}
    )
    engine = MilpEngine(use_two_stage_search=False, study_basis=basis)
    result = engine.solve(engine.build_optimisation_request(blind, lib))
    assert result.selected_power_kw is None
    assert not result.warnings[0].startswith(StudyBasis.LABEL)  # the verdict leads
    assert any(w.startswith(StudyBasis.LABEL) for w in result.warnings)  # ...but the basis is there


# --------------------------------------------------------------------------- 5. refusals


def test_a_second_export_mechanism_is_refused_not_merged(fixture, basis) -> None:
    site, lib, ts, _load = fixture
    points = [MarketForecastPoint(timestamp=t.to_pydatetime(), price_gbp_per_mwh=250.0) for t in ts]
    fc = MarketForecast(points=points, source="test-wholesale")
    fc.content_hash = hash_market_forecast(fc)
    override = ExportFeasibilityOverride(
        mec_kw=EXPORT_LIMIT_KW,
        authorised_by="Olly",
        justification="x",
        acknowledgement=ExportFeasibilityOverride.ACKNOWLEDGEMENT,
    )
    with pytest.raises(InvalidRequestError, match="market_forecast"):
        MilpEngine(study_basis=basis, market_forecast=fc)
    with pytest.raises(InvalidRequestError, match="export_feasibility_override"):
        MilpEngine(study_basis=basis, export_feasibility_override=override)
    # `use_mpc` is deliberately NOT asserted here any more. The MPC path is quarantined
    # engine-wide (2026-08-15), so `MilpEngine(study_basis=basis, use_mpc=True)` now raises for the
    # quarantine reason before the study-basis incompatibility is ever reached. Keeping the
    # assertion would leave a test that passes while proving nothing about the study basis — the
    # incompatibility it named is unreachable, not verified. The quarantine has its own test in
    # `test_mpc_quarantine.py`; restore this line when the quarantine is lifted.
    with pytest.raises(InvalidRequestError, match="sizing_solver"):
        MilpEngine(study_basis=basis, sizing_solver=MilpDispatchSolver())


def test_the_gated_overlay_is_suppressed_under_a_study_basis(fixture, basis) -> None:
    site, lib, _ts, _load = fixture
    engine = MilpEngine(use_two_stage_search=False, study_basis=basis)
    engine.solve(engine.build_optimisation_request(site, lib))
    assert engine.last_export_upside() is None
    assert engine.last_export_feasibility_override() is None


def test_a_site_with_real_consent_and_an_agreed_tariff_refuses_the_study_basis(
    fixture, basis
) -> None:
    """The one refusal: D43's real contracted route is open, so a study would double-count."""
    site, lib, _ts, _load = fixture
    consented = site.model_copy(
        update={
            "stream_consents": [
                RevenueStreamConsent(
                    stream=MarketStream.WHOLESALE_P415,
                    consented=True,
                    agreed_rate_gbp=0.08,
                    agreed_rate_unit=RateUnit.GBP_PER_KWH,
                )
            ]
        }
    )
    engine = MilpEngine(use_two_stage_search=False, study_basis=basis)
    result = engine.solve(engine.build_optimisation_request(consented, lib))
    assert result.selected_power_kw is None
    assert result.warnings[0].startswith("STUDY BASIS REFUSED (D46)")
    assert "not a data refusal" in result.warnings[0]
    # An unconsented site is unaffected by that refusal.
    ok = MilpEngine(use_two_stage_search=False, study_basis=basis)
    assert (
        not ok.solve(ok.build_optimisation_request(site, lib))
        .warnings[0]
        .startswith("STUDY BASIS REFUSED")
    )


def test_the_study_basis_is_deterministic(fixture, basis) -> None:
    site, lib, _ts, _load = fixture
    dumps = []
    for _ in range(2):
        engine = MilpEngine(use_two_stage_search=False, study_basis=basis)
        result = engine.solve(engine.build_optimisation_request(site, lib))
        dumps.append(result.model_dump(mode="json", exclude={"created_at"}))
    assert dumps[0] == dumps[1]
