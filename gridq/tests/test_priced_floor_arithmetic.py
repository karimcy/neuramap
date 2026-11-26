"""Priced-floor arithmetic: N2, N5, F-03, N23 (hostile/WP-0 review 2026-08-10).

Four defects that all move the same numbers — the £ a candidate is ranked on, the £ the P90
series carries, and the volumes finance charges against them:

- **N2** — the CM non-delivery penalty was subtracted on top of the P90 haircut on the *same*
  contracted revenue: two deductions for one event, so winning a Capacity Market agreement made
  P90 EBITDA WORSE than not holding one. One shortfall now drives both, bounded at the revenue.
- **N5** — the selection metric summed gross ``annual_revenue_gbp`` while `_split_signed` routes
  the negative part of a signed dispatch component onto ``annual_cost_gbp``. The omission grows
  with size, so it inverted the ranking between viable candidates.
- **F-03** — the MILP objective priced charging at energy + DUoS only, without the site's £/kWh
  levy stack, so the optimiser booked marginal cycles whose margin the levy leakage erases.
- **N23** — finance was handed the no-export dispatch volumes while the result carried
  overlay-export revenue: the extra import that export required was never levied.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.dispatch import DispatchProblem
from gridq.finance.cashflows import DispatchEnergy, build_cashflow_model
from gridq.ingestion.market_forecasts import (
    MarketForecast,
    MarketForecastPoint,
    hash_market_forecast,
)
from gridq.optimisation.engine import MilpEngine
from gridq.optimisation.milp_solver import MilpDispatchSolver, degradation_cost_gbp_per_kwh
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.sizing.evaluator import priced_floor_value_gbp
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import CandidateEvaluation

# --------------------------------------------------------------------------- N2 helpers

_CM_REVENUE = 50_000.0
_FIRM_REVENUE = 40_000.0


def _assumption(value: float) -> dict:
    return {
        "value": value,
        "unit": "pct",
        "source": "test",
        "status": "placeholder",
        "owner_or_reviewer": "test",
        "confidence": "low",
        "last_reviewed": str(date(2026, 7, 2)),
        "notes": "test-only",
    }


def _lib_with_penalty_cap(cap_pct: float, release: str = "gb_v0_1") -> AssumptionLibrary:
    base = load_assumptions(release).model_dump(mode="json")
    base["penalties"] = {"cm_annual_cap_pct_of_revenue": _assumption(cap_pct)}
    return AssumptionLibrary.model_validate(base)


def _result(*, with_cm: bool) -> s.OptimisationResult:
    streams = [
        s.ValueStreamResult(
            stream=s.MarketStream.TARIFF_ARBITRAGE,
            annual_revenue_gbp=_FIRM_REVENUE,
            firmness=s.Firmness.FIRM,
        )
    ]
    if with_cm:
        streams.append(
            s.ValueStreamResult(
                stream=s.MarketStream.CAPACITY_MARKET,
                annual_revenue_gbp=_CM_REVENUE,
                firmness=s.Firmness.CONTRACTED,
            )
        )
    return s.OptimisationResult(
        result_id="res_n2",
        request_id="req_n2",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=200.0,
        selected_energy_kwh=400.0,
        duration_hours=2.0,
        value_streams=streams,
        solver_status=s.SolverStatus.BASELINE,
    )


# --------------------------------------------------------------------------- N2


def test_n2_winning_a_cm_agreement_never_lowers_p90_ebitda(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """THE DEFECT. Two otherwise identical sites, one holding a £50,000/yr CM agreement.

    Pre-N2 the contract contributed ``+R`` to P50 and ``−0.15R`` to P90 — the site that WON the
    contract priced £57,500/yr of P90 EBITDA WORSE than the one that did not, because the 100%
    annual penalty cap was subtracted on top of the 85% of the same revenue the P90 haircut kept.
    """
    lib = _lib_with_penalty_cap(100.0)
    site = make_site()
    with_cm = build_cashflow_model(site, _result(with_cm=True), lib)
    without_cm = build_cashflow_model(site, _result(with_cm=False), lib)

    assert with_cm.p50_ebitda_gbp[0] > without_cm.p50_ebitda_gbp[0]  # unchanged: P50 books +R
    assert with_cm.p90_ebitda_gbp[0] > without_cm.p90_ebitda_gbp[0], (
        "a contracted CM agreement must never reduce P90 EBITDA relative to holding no contract"
    )


def test_n2_penalty_and_revenue_loss_are_driven_by_one_shortfall(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The penalty is the annual cap scaled by the SAME shortfall the contracted haircut applies.

    On gb_v0_1 no ``p90_revenue_haircut_contracted_pct`` exists, so contracted revenue shares the
    15% merchant haircut: shortfall s = 0.15, and the penalty is ``100% × £50,000 × 0.15``, not
    the full £50,000 cap the pre-N2 code subtracted at every shortfall including zero.
    """
    lib = _lib_with_penalty_cap(100.0)
    model = build_cashflow_model(make_site(), _result(with_cm=True), lib)
    shortfall = float(lib.get("p50_p90_haircuts", "p90_revenue_haircut_pct").value) / 100.0
    assert model.p90_penalty_exposure_gbp == pytest.approx(_CM_REVENUE * 1.0 * shortfall)
    assert any("SAME" in w and "delivery shortfall" in w for w in model.warnings)


def test_n2_zero_shortfall_means_zero_penalty(make_site: Callable[..., s.SiteModel]) -> None:
    """One shortfall, both deductions: at a 0% contracted haircut nothing was forfeited, so no
    penalty can be due. Pre-N2 the full annual cap was charged here regardless."""
    base = load_assumptions("gb_v0_1").model_dump(mode="json")
    base["penalties"] = {"cm_annual_cap_pct_of_revenue": _assumption(100.0)}
    base["p50_p90_haircuts"]["p90_revenue_haircut_contracted_pct"] = _assumption(0.0)
    lib = AssumptionLibrary.model_validate(base)
    model = build_cashflow_model(make_site(), _result(with_cm=True), lib)
    assert model.p90_penalty_exposure_gbp == pytest.approx(0.0)


def test_n2_combined_deduction_is_bounded_at_the_contracted_revenue(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """You cannot lose more than a stream that is worth £R. With an absurd 1000% cap the combined
    (forfeited revenue + penalty) deduction is trimmed so the contracted P90 contribution floors
    at £0 — the bounded worst case is losing the revenue, never owing more than you earned."""
    lib = _lib_with_penalty_cap(1000.0)
    site = make_site()
    with_cm = build_cashflow_model(site, _result(with_cm=True), lib)
    without_cm = build_cashflow_model(site, _result(with_cm=False), lib)
    # The contracted contribution to P90 EBITDA, isolated: bounded below at £0 and above at P50's.
    p90_contribution = with_cm.p90_ebitda_gbp[0] - without_cm.p90_ebitda_gbp[0]
    p50_contribution = with_cm.p50_ebitda_gbp[0] - without_cm.p50_ebitda_gbp[0]
    assert p90_contribution == pytest.approx(0.0, abs=0.02)
    assert p50_contribution >= p90_contribution >= 0.0
    assert any("penalty bounded (N2)" in w for w in with_cm.warnings)


# --------------------------------------------------------------------------- N5


def _stream(revenue: float, cost: float, firmness: s.Firmness) -> s.ValueStreamResult:
    return s.ValueStreamResult(
        stream=s.MarketStream.TARIFF_ARBITRAGE,
        annual_revenue_gbp=revenue,
        annual_cost_gbp=cost,
        firmness=firmness,
    )


def _sized_result(streams: list[s.ValueStreamResult], power_kw: float) -> s.OptimisationResult:
    return s.OptimisationResult(
        result_id="res_n5",
        request_id="req_n5",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=power_kw,
        selected_energy_kwh=power_kw * 2.0,
        duration_hours=2.0,
        value_streams=streams,
        solver_status=s.SolverStatus.BASELINE,
    )


def _evaluation(result: s.OptimisationResult) -> CandidateEvaluation:
    power = result.selected_power_kw or 0.0
    return CandidateEvaluation(
        candidate=SizingCandidate(power_kw=power, duration_hours=2.0),
        optimisation_result=result,
        priceable=True,
        bankable=False,
        min_p90_dscr=None,
        covenant=None,
        passes_covenant=None,
        levered_return_pct=None,
        project_irr_pct=None,
        financial=None,
    )


def test_n5_selection_metric_nets_the_dispatch_cost() -> None:
    """The metric is ``revenue − cost``, not gross revenue: a signed dispatch component's negative
    part lives on ``annual_cost_gbp`` (`valuation._split_signed`), and finance nets it in EBITDA."""
    result = _sized_result([_stream(78_000.0, 26_000.0, s.Firmness.FIRM)], 800.0)
    assert priced_floor_value_gbp(result) == pytest.approx(52_000.0)
    assert MilpEngine._floor_value(_evaluation(result)) == pytest.approx(52_000.0)


def test_n5_gross_revenue_ranking_inverted_the_choice_between_viable_candidates() -> None:
    """The failure mode, pinned: charge-energy cost is size-correlated, so a big candidate whose
    extra revenue is mostly extra charge cost outranked a small one that nets MORE. Candidate A
    (200 kW) nets £42k on £42k of revenue; candidate B (800 kW) grosses £78k but nets £38k and
    carries ~4x the capex. Gross-revenue ranking picked B."""
    a = _sized_result([_stream(42_000.0, 0.0, s.Firmness.FIRM)], 200.0)
    b = _sized_result([_stream(78_000.0, 40_000.0, s.Firmness.FIRM)], 800.0)
    gross = lambda r: sum(v.annual_revenue_gbp for v in r.value_streams)  # noqa: E731
    assert gross(b) > gross(a), "fixture must exercise the inversion"
    assert priced_floor_value_gbp(a) > priced_floor_value_gbp(b)
    assert MilpEngine._floor_value(_evaluation(a)) > MilpEngine._floor_value(_evaluation(b))


def test_n5_contracted_costs_are_netted_too() -> None:
    """Both priced firmness classes net their own cost — the floor is {FIRM, CONTRACTED}."""
    result = _sized_result(
        [
            _stream(30_000.0, 5_000.0, s.Firmness.FIRM),
            _stream(20_000.0, 3_000.0, s.Firmness.CONTRACTED),
            _stream(99_000.0, 0.0, s.Firmness.CONDITIONAL),  # never in the priced floor
        ],
        300.0,
    )
    assert priced_floor_value_gbp(result) == pytest.approx(42_000.0)
    assert MilpEngine._floor_value(_evaluation(result)) == pytest.approx(42_000.0)


# --------------------------------------------------------------------------- F-03


@pytest.fixture(scope="module")
def _hh(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    """The shared deterministic 7-day metered series + its row count."""
    path = tmp_path_factory.mktemp("f03_milp") / "hh.csv"
    rows, _ts = write_hh_csv(path, days=7)
    return path, rows


def _tariff_site(_hh, *, levies: dict[str, float], day: float, night: float, duos: float = 0.0):
    path, rows = _hh
    site = multi_rate_site(path, rows=rows)
    return site.model_copy(
        update={
            "tariff_stack": s.TariffStack(
                basis=SYNTHETIC_BASIS,
                provenance=SYNTHETIC_PROVENANCE,
                supplier="F03TestCo",
                import_rates={"day": day, "night": night},
                duos_red_rate=duos,
                levies=levies,
            )
        }
    )


def _dispatch(site) -> HalfHourlyDispatchSchedule:
    solver = MilpDispatchSolver()
    solver.solve(
        DispatchProblem(
            site_model=site,
            assumptions=load_assumptions("gb_v0_1"),
            power_kw=200.0,
            energy_kwh=400.0,
            duration_hours=2.0,
            constraints=[],
        )
    )
    schedule = solver.last_schedule()
    assert schedule is not None
    return schedule


_F03_LEVY_GBP_PER_KWH = 0.02


def _f03_thin_spread(*, levy: float, night: float = 0.10) -> dict[str, float]:
    """Day/night prices placing the marginal cycle INSIDE the window that ``levy`` closes.

    Per delivered kWh the cycle earns ``day``, pays ``night / RTE`` for the energy it must import
    to deliver it, pays ``c_deg`` for the wear, and pays ``levy x (1/RTE - 1)`` for the levy on the
    round-trip losses — the levy falls on the ``1/RTE`` kWh imported at night and is avoided on the
    one kWh not imported by day, so only the loss fraction is a net cost. The margin is therefore

        m(levy) = day - night / RTE - c_deg - levy x (1 / RTE - 1)

    and the fixture needs ``0 < m(0) < levy x (1/RTE - 1)``: profitable unlevied, loss-making
    levied. It is placed at the MIDPOINT of that window so it sits clear of both edges.

    **Derived, not pinned, and that is the point of this helper.** The spread was originally the
    literal 1.5p/kWh, which silently assumed ``c_deg = 0``. When DEG-01 put a marginal cycle cost
    in the objective, break-even moved past that literal, the unlevied arm stopped cycling at all,
    and the test no longer exercised its own defect — it was the fixture's own inert-fixture guard
    on the next line that caught it, not the assertions. Reading ``c_deg`` and RTE from the same
    release the solver reads keeps the window correct through the next such change.
    """
    assumptions = load_assumptions("gb_v0_1")
    rte = float(assumptions.get("round_trip_efficiency", "ac_round_trip").value)
    c_deg, _ = degradation_cost_gbp_per_kwh(assumptions)
    window = levy * (1.0 / rte - 1.0)
    return {"day": night / rte + c_deg + window / 2.0, "night": night}


def test_f03_the_levy_stack_stops_a_cycle_whose_margin_it_erases(_hh) -> None:
    """THE DEFECT. On a spread thin enough that the cycle's margin is smaller than the levy it
    incurs on its own round-trip losses, the cycle LOSES money — but the pre-F-03 objective priced
    charging at energy + DUoS only, could not see the levy, and booked the cycle anyway: reported
    FIRM arbitrage revenue against roughly twice as much in levies that finance then charged. The
    optimiser now refuses it. The spread is derived (see ``_f03_thin_spread``) so that the arm which
    must cycle still can, whatever the release's degradation cost has since become."""
    thin = _f03_thin_spread(levy=_F03_LEVY_GBP_PER_KWH)
    unlevied = _dispatch(_tariff_site(_hh, levies={}, **thin))
    levied = _dispatch(_tariff_site(_hh, levies={"ccl": _F03_LEVY_GBP_PER_KWH}, **thin))

    # WP-CORE-01 triage (2026-11-16). This asserted ZERO charge and ZERO arbitrage on the levied
    # arm. D57 limb 1 (DFS "OPEN and PRICED on every site") and D57 item 4 (205ed92, 2026-08-18)
    # put DFS utilisation into the objective on every release; this 7-day fixture has 8
    # DFS-called half-hours paying £0.1597/kWh, which clears the levy and the wear. Measured on the
    # levied arm: 19,565.734 kWh/yr discharged, ALL of it DFS delivery and 0.0 outside the DFS
    # periods; charge 22,233.789 kWh = 19,565.734 / RTE 0.88; and the energy-band "arbitrage" is
    # just that DFS cycle's own day/night spread, 19,565.734 x 0.1363 - 22,233.789 x 0.10 =
    # £443.43. Unlevied, 117,394 kWh/yr is discharged OUTSIDE the DFS periods. So F-03's claim
    # (the thin TARIFF cycle no longer pays once the levy is in the objective) is unchanged, and
    # is now asserted where it lives: outside the DFS-called half-hours.
    def _outside_dfs_kwh(schedule: HalfHourlyDispatchSchedule) -> float:
        mask = schedule.dfs_price_gbp_per_kwh > 0
        return float(
            schedule.discharge_kw[~mask].sum() * schedule.interval_hours * schedule.annual_factor
        )

    assert unlevied.annual_charge_kwh > 0, "fixture must exercise the defect: it cycled unlevied"
    assert unlevied.arbitrage_value_gbp > 0
    assert _outside_dfs_kwh(unlevied) > 0.0, "the unlevied arm must trade the thin tariff spread"
    assert _outside_dfs_kwh(levied) == pytest.approx(0.0), (
        "with the levy stack in the objective price the marginal cycle no longer pays"
    )
    dfs_kwh = levied.dfs_delivered_mwh * 1000.0
    assert levied.annual_discharge_kwh == pytest.approx(dfs_kwh)
    rte = levied.annual_discharge_kwh / levied.annual_charge_kwh
    assert rte == pytest.approx(0.88, rel=1e-6)
    assert levied.arbitrage_value_gbp == pytest.approx(
        dfs_kwh * thin["day"] - levied.annual_charge_kwh * thin["night"], abs=0.01
    )


def test_f03_levy_is_a_decision_price_only_and_never_enters_reported_revenue(_hh) -> None:
    """The reconciliation. The levy is in the objective so the optimiser SEES it, but the reported
    value streams are built from the schedule's separate energy/DUoS arrays — so no levy £ ever
    reaches revenue, and finance's ``levies_on_charge`` stays the ONE place it is charged. Pinned
    on a spread wide enough that the levy does not change the dispatch: identical schedules must
    report identical arbitrage/DUoS £."""
    wide = dict(day=0.30, night=0.10, duos=0.10)
    unlevied = _dispatch(_tariff_site(_hh, levies={}, **wide))
    levied = _dispatch(_tariff_site(_hh, levies={"ro": 0.02, "fit": 0.01}, **wide))

    assert levied.annual_charge_kwh == pytest.approx(unlevied.annual_charge_kwh)
    assert levied.arbitrage_value_gbp == pytest.approx(unlevied.arbitrage_value_gbp)
    assert levied.duos_avoidance_value_gbp == pytest.approx(unlevied.duos_avoidance_value_gbp)
    # ... and the levy is charged exactly once, on the finance cost side, at the D36 net-import
    # convention the objective's own levy term is the exact negative of.
    net_charge = levied.annual_charge_kwh - levied.annual_discharge_kwh
    model = build_cashflow_model(
        _tariff_site(_hh, levies={"ro": 0.02, "fit": 0.01}, **wide),
        _sized_result([_stream(levied.arbitrage_value_gbp, 0.0, s.Firmness.FIRM)], 200.0),
        load_assumptions("gb_v0_1"),
        dispatch_energy=DispatchEnergy(
            annual_charge_kwh=levied.annual_charge_kwh,
            annual_discharge_kwh=levied.annual_discharge_kwh,
        ),
    )
    assert model.annual_cost_by_line_gbp["levies_on_charge"] == pytest.approx(
        round(0.03 * net_charge, 2), abs=0.02
    )


# --------------------------------------------------------------------------- N23


def _forecast(ts, price_gbp_per_mwh: float) -> MarketForecast:
    points = [
        MarketForecastPoint(timestamp=t.to_pydatetime(), price_gbp_per_mwh=price_gbp_per_mwh)
        for t in ts
    ]
    fc = MarketForecast(points=points, source="test-wholesale", forecast_version="v-test")
    fc.content_hash = hash_market_forecast(fc)
    return fc


def _export_consented_site(site) -> s.SiteModel:
    """Export consents coherent (G99∧G100 + export MPAN), WHOLESALE_P415 consented, tariff set."""
    return site.model_copy(
        update={
            "consents": s.ConsentState(
                g98=s.ConsentStatus.APPROVED,
                g99=s.ConsentStatus.APPROVED,
                g100=s.ConsentStatus.APPROVED,
                export_mpan="1900000000000",
            ),
            "stream_consents": (
                s.RevenueStreamConsent(stream=s.MarketStream.WHOLESALE_P415, consented=True),
            ),
            "export_arrangement": s.ExportArrangement(export_tariff_gbp_per_kwh=0.08),
        }
    )


@pytest.fixture(scope="module")
def _export_fixture(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    path = tmp_path_factory.mktemp("n23_milp") / "hh.csv"
    rows, ts = write_hh_csv(path, days=7)
    return multi_rate_site(path, rows=rows), load_assumptions("gb_v0_1"), ts


def test_n23_finance_gets_the_overlay_volumes_when_export_revenue_is_priced(
    _export_fixture,
) -> None:
    """THE DEFECT. With consent + a declared export tariff the overlay's export MWh is priced into
    the floor as CONTRACTED, but `_dispatch_energy_for` re-dispatches the plain **no-export**
    problem — so finance was charged the floor solve's import while banking export revenue the
    floor solve never charged for. Levies were understated and the price-to-target's delivered
    energy overstated. The volumes must now come from the overlay's own dispatch."""
    site, lib, ts = _export_fixture
    consented = _export_consented_site(site)
    fc = _forecast(ts, 500.0)  # 0.50 £/kWh >> the retail spread, so export definitely pays

    engine = MilpEngine(market_forecast=fc)
    result = engine.solve(engine.build_optimisation_request(consented, lib))
    upside = engine.last_export_upside()
    assert upside is not None and upside.export_mwh > 0
    priced = [v for v in result.value_streams if v.stream == s.MarketStream.WHOLESALE_P415]
    assert len(priced) == 1 and priced[0].annual_revenue_gbp > 0

    # The same site with no forecast: no overlay, no priced export, and the floor's own volumes.
    floor_engine = MilpEngine()
    floor_engine.solve(floor_engine.build_optimisation_request(consented, lib))
    floor_energy = floor_engine.selected_dispatch_energy()
    overlay_energy = engine.selected_dispatch_energy()
    assert floor_energy is not None and overlay_energy is not None

    def _net_import(e) -> float:
        # The levy base (D36): levies are charged on the NET import increase, because a
        # BEHIND-THE-METER discharge displaces levied site import. An EXPORTED kWh displaces
        # nothing, so an overlay that redirects discharge past the meter raises this sharply.
        assert e.annual_charge_kwh is not None and e.annual_discharge_kwh is not None
        return max(0.0, e.annual_charge_kwh - e.annual_discharge_kwh)

    assert _net_import(overlay_energy) > 2 * _net_import(floor_energy), (
        "the overlay's exported kWh displace no site import, so the levied net import — the "
        "cost the pre-N23 pipeline never charged — is materially larger than the floor solve's"
    )
    assert overlay_energy.annual_discharge_kwh is not None
    assert floor_energy.annual_discharge_kwh is not None
    assert overlay_energy.annual_discharge_kwh < floor_energy.annual_discharge_kwh, (
        "delivered (behind-the-meter) energy falls when discharge is redirected to export — the "
        "price-to-target denominator was overstated by the difference"
    )
    assert engine.last_forecast_priced_floor_note() is not None
    assert "N23" in (engine.last_forecast_priced_floor_note() or "")


def test_n23_contracted_export_is_held_out_when_volumes_cannot_be_reconciled(
    _export_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two are wired to one switch: revenue that cannot be matched to the charge volume that
    bought it is NOT priced. Simulating an unavailable overlay schedule must drop the contracted
    floor entry (the MID delta still reports as gated upside) — never price it on floor volumes."""
    site, lib, ts = _export_fixture
    consented = _export_consented_site(site)
    engine = MilpEngine(market_forecast=_forecast(ts, 500.0))
    monkeypatch.setattr(MilpEngine, "_reconcile_export_volumes", lambda *a, **k: None)
    result = engine.solve(engine.build_optimisation_request(consented, lib))

    assert all(v.stream != s.MarketStream.WHOLESALE_P415 for v in result.value_streams)
    assert any(v.stream == s.MarketStream.WHOLESALE_P415 for v in result.gated_upside)
    note = engine.last_forecast_priced_floor_note()
    assert note is not None and "NOT priced into the floor (N23)" in note
