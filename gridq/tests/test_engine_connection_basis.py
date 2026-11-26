"""The engine's connection-capacity contract: sizing basis, verdict labelling, required capacity,
and the export-floor netting.

Four defects, all in `gridq.optimisation.engine`, all found by running the six live campaign sites:

- **the search box was anchored on LOAD, not on the CONNECTION.** `_binding_capacity` returned the
  observed peak whenever one existed and MIC only as a fallback, so the two-stage box (5–150% of it)
  never saw the connection. Knowl Hill: 325 kW of evidenced capacity, 22.6 kW metered peak, box
  capped at 33.9 kW — 302 kW of evidenced headroom invisible to the search. And tripling another
  site's MIC produced an IDENTICAL candidate set, because MIC could not reach the box at all.
- **a FALSE causal sentence.** `_capacity_headroom_note` fired on a `MIC ≈ peak within 1%` METADATA
  signature and then asserted the signature "leaves no charge headroom … every candidate necessarily
  priced at or near zero". The engine's own run record refutes that: 121,777 kWh/yr charged over
  2,181 half-hours at full rated power, median measured headroom 118.7 kW, priced floors to
  £18,510/yr. It also returned INSUFFICIENT_DATA — a DATA-shaped label on an ECONOMIC outcome
  (every candidate's `project_irr_pct` is None because every cashflow year is negative).
- **the required connection capacity was never reported.** Per D44 an unknown MIC is a derived
  assumption plus a data ask, never a blocker — and the ask is only answerable with a number.
- **MD-F-05 limb (b): the export floor double-counted.** The contracted export stream was appended
  to the UNCHANGED no-export `value_streams`, so the floor read "full no-export arbitrage + full
  export volume" — two mutually exclusive uses of the same stored kWh.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.optimisation.engine import ExportUpside, MilpEngine
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import CandidateEvaluation, SizingRun

# --------------------------------------------------------------------------- helpers


def _site(make_site, *, mic_kw: float, asc_kw: float | None, peak_kw: float | None):
    """A fixture site with a chosen connection/peak state.

    ``mic_kw=0.0`` is the schema's way of saying "not declared" (`ConnectionDetails.mic_kw` is a
    required non-negative float, and `ingestion.missing_data` treats 0 as the missing case).
    """
    site = make_site()
    load = site.load_data.model_copy(update={"max_import_kw": peak_kw})
    conn = s.ConnectionDetails(mic_kw=mic_kw, asc_kw=asc_kw)
    return site.model_copy(update={"load_data": load, "connection": conn})


def _result(
    power_kw: float,
    floor_revenue_gbp: float,
    *,
    binding: list[str] | None = None,
) -> s.OptimisationResult:
    return s.OptimisationResult(
        result_id="res_basis",
        request_id="req_basis",
        implementation_level=s.ImplementationLevel.PRODUCTION,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=power_kw,
        selected_energy_kwh=power_kw * 2.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=floor_revenue_gbp,
                firmness=s.Firmness.FIRM,
            )
        ],
        constraints_binding=list(binding or []),
        solver_status=s.SolverStatus.OPTIMAL,
    )


def _evaluation(
    power_kw: float,
    floor_revenue_gbp: float,
    *,
    binding: list[str] | None = None,
) -> CandidateEvaluation:
    """A full-series, priceable, UNVIABLE candidate (no positive return metric)."""
    return CandidateEvaluation(
        candidate=SizingCandidate(power_kw=power_kw, duration_hours=2.0),
        optimisation_result=_result(power_kw, floor_revenue_gbp, binding=binding),
        priceable=True,
        bankable=False,
        min_p90_dscr=None,
        covenant=1.3,
        passes_covenant=None,
        levered_return_pct=None,
        project_irr_pct=None,
        financial=None,
        stage="confirm",
        proxy=False,
    )


def _run(candidates: list[CandidateEvaluation]) -> SizingRun:
    return SizingRun(
        search_space_size=len(candidates),
        candidates_evaluated=candidates,
        selected=None,
        selection_reason="no bankable candidate: no size meets the P90 DSCR covenant",
        rejected_by_covenant=[],
        bankable=False,
        assumption_version="gb_v0_1",
    )


def _schedule(
    *,
    load_kw: np.ndarray,
    charge_kw: np.ndarray,
    power_kw: float = 100.0,
    energy_price: float = 0.20,
    duos_rate: float = 0.10,
    discharge_kw: np.ndarray | None = None,
) -> HalfHourlyDispatchSchedule:
    n = load_kw.size
    discharge = np.zeros(n) if discharge_kw is None else discharge_kw
    return HalfHourlyDispatchSchedule(
        power_kw=power_kw,
        energy_kwh=power_kw * 2.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=charge_kw,
        discharge_kw=discharge,
        soe_kwh=np.zeros(n),
        site_load_kw=load_kw,
        duos_rate_gbp_per_kwh=np.full(n, duos_rate),
        red_band_mask=np.zeros(n, dtype=bool),
        energy_price_gbp_per_kwh=np.full(n, energy_price),
    )


# ------------------------------------------------------- 1. the box sees the CONNECTION


def test_the_search_box_is_anchored_on_the_declared_connection_limit(make_site) -> None:
    """The Knowl Hill regression, pinned. 325 kW declared, 22.6 kW metered peak: the basis is the
    CONNECTION, so the box tops out at 1.5 x 325 kW rather than 1.5 x 22.6 kW."""
    basis = MilpEngine._sizing_basis(_site(make_site, mic_kw=325.0, asc_kw=325.0, peak_kw=22.619))
    assert basis is not None
    assert basis.capacity_kw == 325.0
    assert basis.basis == "connection_limit"
    assert basis.connection_limit_kw == 325.0 and basis.observed_peak_kw == 22.619
    assert "DECLARED CONNECTION LIMIT" in basis.note
    # The defect this replaces: the old rule returned the peak, capping the box at 33.9 kW.
    assert basis.capacity_kw * 1.5 > 33.93


def test_the_binding_limit_is_the_min_of_mic_and_asc(make_site) -> None:
    """Same convention as the provisioning floor and the solver's own charge cap (N7): a battery
    charges through the SMALLER of the two declarations, so that is what the box is scaled from."""
    basis = MilpEngine._sizing_basis(_site(make_site, mic_kw=500.0, asc_kw=380.0, peak_kw=None))
    assert basis is not None and basis.capacity_kw == 380.0


def test_a_declared_limit_below_the_observed_peak_is_flagged_not_trusted(make_site) -> None:
    """The meter refutes the declaration (D5: the observed peak is a FLOOR on MIC). The box is
    anchored on the larger of the two so it cannot exclude a size either basis evidences, and the
    contradiction is named with the connection data ask."""
    basis = MilpEngine._sizing_basis(_site(make_site, mic_kw=300.0, asc_kw=None, peak_kw=420.0))
    assert basis is not None
    assert basis.capacity_kw == 420.0
    assert basis.basis == "connection_limit_below_observed_peak"
    assert basis.connection_limit_kw == 300.0
    assert "connection_mic_mec_asc" in basis.note


def test_with_no_declared_limit_the_observed_peak_is_the_labelled_fallback(make_site) -> None:
    basis = MilpEngine._sizing_basis(_site(make_site, mic_kw=0.0, asc_kw=None, peak_kw=420.0))
    assert basis is not None
    assert basis.capacity_kw == 420.0 and basis.basis == "observed_peak"
    assert "connection_mic_mec_asc" in basis.note  # a data ask, never a blocker (D44)


def test_no_scale_at_all_is_the_only_none(make_site) -> None:
    assert MilpEngine._sizing_basis(_site(make_site, mic_kw=0.0, asc_kw=None, peak_kw=None)) is None


def test_the_basis_actually_reaches_the_two_stage_search_box(make_site, monkeypatch) -> None:
    """The wiring, not just the helper: whatever `_sizing_basis` returns must be the
    `binding_capacity_kw` the two-stage search scales its box from. The probe that exposed this
    defect tripled a site's MIC and got an IDENTICAL candidate set, because the number never
    travelled."""
    captured: dict[str, float] = {}

    def _fake_two_stage(site, assumptions, **kwargs):
        captured["binding_capacity_kw"] = kwargs["binding_capacity_kw"]
        return _run([_evaluation(10.0, 100.0)])

    monkeypatch.setattr("gridq.optimisation.engine.two_stage_sizing", _fake_two_stage)
    site = _site(make_site, mic_kw=325.0, asc_kw=325.0, peak_kw=22.619)
    engine = MilpEngine()
    engine.solve(engine.build_optimisation_request(site, load_assumptions("gb_v0_1")))

    assert captured["binding_capacity_kw"] == 325.0
    assert engine.last_sizing_basis() is not None
    assert engine.last_sizing_basis().basis == "connection_limit"


def test_the_sizing_basis_is_stated_on_the_persisted_run_and_the_result(
    make_site, monkeypatch
) -> None:
    site = _site(make_site, mic_kw=325.0, asc_kw=325.0, peak_kw=22.619)
    engine = MilpEngine()
    monkeypatch.setattr(MilpEngine, "_run_search", lambda *a, **k: _run([_evaluation(10.0, 5.0)]))
    result = engine.solve(engine.build_optimisation_request(site, load_assumptions("gb_v0_1")))

    run = engine.last_sizing_run()
    assert run is not None
    assert any("sizing basis = DECLARED CONNECTION LIMIT" in w for w in run.warnings)
    assert any("sizing basis = DECLARED CONNECTION LIMIT" in w for w in result.warnings)


# ------------------------------------------- 2. the verdict label matches the verdict's KIND


def test_a_placeholder_mic_no_longer_relabels_an_economic_result_as_missing_data(
    make_site, monkeypatch
) -> None:
    """Candidates SOLVED, PRICED and turned out uneconomic, with MIC == the metered peak. That is an
    ECONOMIC outcome: it keeps `no_viable_configuration`. The placeholder-MIC signature rides
    alongside as a DATA ASK — a separate warning, never the verdict."""
    site = _site(make_site, mic_kw=420.0, asc_kw=None, peak_kw=420.0)
    engine = MilpEngine()
    monkeypatch.setattr(
        MilpEngine, "_run_search", lambda *a, **k: _run([_evaluation(210.0, 18_509.8)])
    )

    result = engine.solve(engine.build_optimisation_request(site, load_assumptions("gb_v0_1")))

    assert result.solver_status == s.SolverStatus.OPTIMAL
    assert result.selection_status == s.SelectionStatus.NO_VIABLE_CONFIGURATION
    assert result.warnings[0].startswith("no viable configuration")
    ask = next(w for w in result.warnings if w.startswith("connection capacity unknown"))
    assert "DATA ASK" in ask
    # the false causal sentence is gone, and its claim is contradicted by the run's own floor
    assert "necessarily priced at or near zero" not in " ".join(result.warnings)
    assert "leaves no charge headroom" not in " ".join(result.warnings)
    assert "18,509.80" in " ".join(result.warnings)


def test_an_undeclared_connection_keeps_the_data_verdict_because_charging_is_forced_to_zero(
    make_site, monkeypatch
) -> None:
    """The complement, and the reason the branches must stay split: with no declared limit the
    solver's connection limit resolves to 0 kW, so `charge_ub` is 0 in every half-hour and the £0
    floors are MECHANICAL. Calling that "no viable configuration" would be the same category error
    in the opposite direction."""
    site = _site(make_site, mic_kw=0.0, asc_kw=None, peak_kw=420.0)
    engine = MilpEngine()
    monkeypatch.setattr(MilpEngine, "_run_search", lambda *a, **k: _run([_evaluation(210.0, 0.0)]))

    result = engine.solve(engine.build_optimisation_request(site, load_assumptions("gb_v0_1")))

    assert result.solver_status == s.SolverStatus.INSUFFICIENT_DATA
    assert result.selection_status == s.SelectionStatus.INSUFFICIENT_DATA
    assert result.warnings[0].startswith("connection capacity unknown")
    assert "MECHANICALLY" in result.warnings[0]
    assert "connection_mic_mec_asc" in result.warnings[0]


def test_real_headroom_and_an_uneconomic_site_still_gets_the_economics_verdict_alone(
    make_site, monkeypatch
) -> None:
    """No placeholder signature, a real declared limit: the economics verdict stands with no
    connection note attached at all."""
    site = _site(make_site, mic_kw=900.0, asc_kw=900.0, peak_kw=420.0)
    engine = MilpEngine()
    monkeypatch.setattr(
        MilpEngine, "_run_search", lambda *a, **k: _run([_evaluation(210.0, 500.0)])
    )

    result = engine.solve(engine.build_optimisation_request(site, load_assumptions("gb_v0_1")))

    assert result.selection_status == s.SelectionStatus.NO_VIABLE_CONFIGURATION
    assert not any(w.startswith("connection capacity unknown") for w in result.warnings)


def test_the_note_states_measured_headroom_from_the_sites_own_series(make_site) -> None:
    """The replacement for the inferred claim: real statistics off the real half-hourly file."""
    site = _site(make_site, mic_kw=420.0, asc_kw=None, peak_kw=420.0)
    measured = MilpEngine()._measured_charge_headroom(site, 420.0)
    assert measured is not None
    assert measured.startswith("MEASURED charge headroom")
    assert "median" in measured and "charge-energy envelope" in measured


def test_measured_headroom_is_omitted_not_invented_when_the_series_is_unreadable(
    make_site,
) -> None:
    site = _site(make_site, mic_kw=420.0, asc_kw=None, peak_kw=420.0)
    broken = site.load_data.model_copy(update={"half_hourly_file_ref": "no/such/file.csv"})
    unreadable = site.model_copy(update={"load_data": broken})
    assert MilpEngine()._measured_charge_headroom(unreadable, 420.0) is None
    # ... and the note then SAYS it is unmeasured rather than asserting there was no headroom.
    note = MilpEngine._capacity_headroom_note(site, measured=None)
    assert note is not None and "UNAVAILABLE" in note
    assert "NOT claim the connection left no room to charge" in note


def test_whether_the_connection_charge_limit_actually_bound_is_reported_either_way() -> None:
    """The direct measured refutation of "there is no room to charge"."""
    did_not = MilpEngine._connection_charge_limit_bound(_run([_evaluation(10.0, 1.0)]))
    assert did_not is not None and "did NOT bind" in did_not
    did = MilpEngine._connection_charge_limit_bound(
        _run([_evaluation(10.0, 1.0, binding=["connection_charge_limit"])])
    )
    assert did is not None and "BOUND in 1 of 1" in did
    assert MilpEngine._connection_charge_limit_bound(None) is None


# --------------------------------------------- 3. required connection capacity as an OUTPUT


def test_required_connection_capacity_is_max_load_plus_charge_plus_aux(make_site) -> None:
    """The quantity the solver's own constraint bounds (charge <= limit - load - aux), so the
    reported requirement and the dispatch that was solved cannot disagree."""
    engine = MilpEngine()
    engine._dispatch_schedule = _schedule(
        load_kw=np.array([100.0, 200.0, 50.0, 200.0]),
        charge_kw=np.array([80.0, 0.0, 100.0, 10.0]),
        power_kw=100.0,
    )
    chosen = _evaluation(100.0, 1_000.0)
    lib = load_assumptions("gb_v1_6")  # parasitic_load_fraction_of_power = 0.01

    req = engine._required_connection_capacity(
        _site(make_site, mic_kw=250.0, asc_kw=None, peak_kw=200.0), lib, chosen
    )

    assert req is not None
    # max(load + charge) = max(180, 200, 150, 210) = 210; aux = 0.01 x 100 kW = 1 kW
    assert req.required_kw == pytest.approx(211.0)
    assert req.parasitic_kw == pytest.approx(1.0)
    assert req.observed_peak_load_kw == 200.0 and req.battery_charge_peak_kw == 100.0
    assert req.declared_limit_kw == 250.0
    assert req.spare_vs_declared_kw == pytest.approx(39.0)
    assert req.schedule_basis == "priced-floor dispatch"
    assert "REQUIRED CONNECTION CAPACITY" in req.note


def test_required_capacity_names_itself_as_the_data_ask_when_no_capacity_is_declared(
    make_site,
) -> None:
    engine = MilpEngine()
    engine._dispatch_schedule = _schedule(
        load_kw=np.array([100.0, 100.0]), charge_kw=np.array([50.0, 0.0])
    )
    req = engine._required_connection_capacity(
        _site(make_site, mic_kw=0.0, asc_kw=None, peak_kw=100.0),
        load_assumptions("gb_v0_1"),  # no parasitic key at all -> zero draw, as in the solver
        _evaluation(100.0, 1_000.0),
    )
    assert req is not None
    assert req.parasitic_kw == 0.0 and req.required_kw == pytest.approx(150.0)
    assert req.declared_limit_kw is None and req.spare_vs_declared_kw is None
    assert "THIS FIGURE IS THE DATA ASK" in req.note
    assert "connection_mic_mec_asc" in req.note


def test_required_capacity_flags_a_declared_limit_below_the_requirement(make_site) -> None:
    engine = MilpEngine()
    engine._dispatch_schedule = _schedule(
        load_kw=np.array([100.0, 100.0]), charge_kw=np.array([90.0, 0.0])
    )
    req = engine._required_connection_capacity(
        _site(make_site, mic_kw=150.0, asc_kw=None, peak_kw=100.0),
        load_assumptions("gb_v0_1"),
        _evaluation(100.0, 1_000.0),
    )
    assert req is not None and req.spare_vs_declared_kw == pytest.approx(-40.0)
    assert "BELOW this requirement" in req.note


def test_required_capacity_is_measured_on_the_overlay_when_export_is_priced(make_site) -> None:
    """N23 already re-bases finance's volumes onto the overlay when contracted export is priced; the
    required capacity must follow the same dispatch or it understates the import the priced case
    assumes."""
    engine = MilpEngine()
    engine._dispatch_schedule = _schedule(
        load_kw=np.array([100.0, 100.0]), charge_kw=np.array([10.0, 0.0])
    )
    engine._export_overlay_schedule = _schedule(
        load_kw=np.array([100.0, 100.0]), charge_kw=np.array([90.0, 0.0])
    )
    engine._export_volumes_reconciled = True
    req = engine._required_connection_capacity(
        _site(make_site, mic_kw=250.0, asc_kw=None, peak_kw=100.0),
        load_assumptions("gb_v0_1"),
        _evaluation(100.0, 1_000.0),
    )
    assert req is not None
    assert req.schedule_basis == "export overlay dispatch"
    assert req.required_kw == pytest.approx(190.0)


def test_no_schedule_means_no_required_capacity_figure(make_site) -> None:
    engine = MilpEngine()
    assert (
        engine._required_connection_capacity(
            _site(make_site, mic_kw=250.0, asc_kw=None, peak_kw=100.0),
            load_assumptions("gb_v0_1"),
            _evaluation(100.0, 1_000.0),
        )
        is None
    )


def test_both_outputs_reach_the_result_on_the_success_path(make_site) -> None:
    """The wiring, not just the helpers. D44 makes the required capacity an OUTPUT, and `warnings`
    is the channel that actually reaches the record today (no field is added to the frozen
    `OptimisationResult` — D13), so a figure computed and never stated would be no output at all.
    Both sentences must be on the RECOMMENDED-spec result, not only on the no-spec verdicts."""
    engine = MilpEngine()
    site = _site(make_site, mic_kw=250.0, asc_kw=None, peak_kw=200.0)
    chosen = _evaluation(100.0, 1_000.0)
    engine._sizing_basis_used = MilpEngine._sizing_basis(site)
    engine._dispatch_schedule = _schedule(
        load_kw=np.array([100.0, 200.0]), charge_kw=np.array([80.0, 0.0])
    )
    engine._required_capacity = engine._required_connection_capacity(
        site, load_assumptions("gb_v1_6"), chosen
    )
    request = engine.build_optimisation_request(site, load_assumptions("gb_v0_1"))

    result = engine._final_result(request, chosen)

    assert any("sizing basis = DECLARED CONNECTION LIMIT" in w for w in result.warnings)
    assert any("REQUIRED CONNECTION CAPACITY" in w for w in result.warnings)
    # the ask is answerable: it names a kW figure, not "the MIC"
    assert any("kW of import capacity" in w for w in result.warnings)
    # ...and the duck-typed hook agrees with what the record says (one number, two channels)
    req = engine.last_required_connection_capacity()
    assert req is not None and f"{req.required_kw:,.1f} kW" in " ".join(result.warnings)
    assert engine.last_sizing_basis() is not None


# ------------------------------------------------- 4. MD-F-05 limb (b): the export netting


def _floor_streams(arbitrage_gbp: float, duos_gbp: float) -> list[s.ValueStreamResult]:
    return [
        s.ValueStreamResult(
            stream=s.MarketStream.TARIFF_ARBITRAGE,
            annual_revenue_gbp=max(arbitrage_gbp, 0.0),
            annual_cost_gbp=max(-arbitrage_gbp, 0.0),
            firmness=s.Firmness.FIRM,
        ),
        s.ValueStreamResult(
            stream=s.MarketStream.DUOS_RED_BAND_AVOIDANCE,
            annual_revenue_gbp=max(duos_gbp, 0.0),
            annual_cost_gbp=max(-duos_gbp, 0.0),
            firmness=s.Firmness.FIRM,
        ),
        s.ValueStreamResult(
            stream=s.MarketStream.CAPACITY_MARKET,
            annual_revenue_gbp=7_000.0,
            firmness=s.Firmness.CONTRACTED,
        ),
    ]


def _net(streams: list[s.ValueStreamResult]) -> float:
    return round(sum(v.annual_revenue_gbp - v.annual_cost_gbp for v in streams), 2)


def test_the_export_floor_is_rebased_onto_the_overlay_dispatch() -> None:
    """The double-count: the base streams are the NO-EXPORT solve's, and gross export revenue used
    to be appended to them unchanged. After re-basing, the dispatch-derived components carry the
    overlay's own (cannibalised) figures and the CM entry is untouched."""
    base = _floor_streams(10_000.0, 4_000.0)
    # An overlay that diverted energy to the wire: arbitrage £6,000, DUoS £3,000.
    overlay = _schedule(
        load_kw=np.array([100.0, 100.0]),
        charge_kw=np.array([0.0, 0.0]),
        discharge_kw=np.array([60_000.0, 0.0]),
        energy_price=0.20,
        duos_rate=0.10,
    )
    rebased, note = MilpEngine._rebase_dispatch_floor_streams(
        base, overlay, cannibalised_gbp=5_000.0
    )

    by_stream = {v.stream: v for v in rebased}
    arb = by_stream[s.MarketStream.TARIFF_ARBITRAGE]
    duos = by_stream[s.MarketStream.DUOS_RED_BAND_AVOIDANCE]
    # 60,000 kW x 0.5 h x £0.20/kWh = £6,000 ; x £0.10/kWh = £3,000
    assert arb.annual_revenue_gbp == pytest.approx(6_000.0)
    assert duos.annual_revenue_gbp == pytest.approx(3_000.0)
    assert by_stream[s.MarketStream.CAPACITY_MARKET].annual_revenue_gbp == 7_000.0
    assert note is not None and "EXPORT-FLOOR NETTING APPLIED" in note
    assert "5,000.00" in note
    assert any("MD-F-05(b)" in w for w in arb.warnings)


def test_the_rebased_floor_equals_the_overlays_own_objective_terms() -> None:
    """The arithmetic that makes the total physically possible: the re-based dispatch components sum
    to the OVERLAY's `priced_floor_terms_gbp`, so `floor + gross contracted export` is
    `overlay terms + export` — not `no-export terms + export`."""
    base = _floor_streams(10_000.0, 4_000.0)
    overlay = _schedule(
        load_kw=np.array([100.0, 100.0]),
        charge_kw=np.array([0.0, 0.0]),
        discharge_kw=np.array([60_000.0, 0.0]),
    )
    rebased, _ = MilpEngine._rebase_dispatch_floor_streams(base, overlay, cannibalised_gbp=5_000.0)
    dispatch_derived = [
        v
        for v in rebased
        if v.stream in (s.MarketStream.TARIFF_ARBITRAGE, s.MarketStream.DUOS_RED_BAND_AVOIDANCE)
    ]
    assert _net(dispatch_derived) == pytest.approx(overlay.priced_floor_terms_gbp)
    # and the un-netted total really was higher — the defect was material, not cosmetic
    assert _net(base) > _net(rebased)


def test_a_negative_rebased_component_lands_on_the_cost_channel_not_clamped() -> None:
    """F-02 discipline survives the re-base: a signed component's negative part is a real cost that
    finance subtracts, never deleted, so `revenue - cost` still reproduces the schedule exactly."""
    base = _floor_streams(10_000.0, 4_000.0)
    overlay = _schedule(
        load_kw=np.array([100.0, 100.0]),
        charge_kw=np.array([60_000.0, 0.0]),  # buys at a loss to free energy for the wire
        discharge_kw=np.array([0.0, 0.0]),
    )
    rebased, _ = MilpEngine._rebase_dispatch_floor_streams(base, overlay, cannibalised_gbp=1.0)
    arb = next(v for v in rebased if v.stream == s.MarketStream.TARIFF_ARBITRAGE)
    assert arb.annual_revenue_gbp == 0.0
    assert arb.annual_cost_gbp == pytest.approx(6_000.0)
    assert arb.annual_revenue_gbp - arb.annual_cost_gbp == pytest.approx(
        overlay.arbitrage_value_gbp
    )


def test_rebasing_without_an_overlay_schedule_changes_nothing_and_claims_nothing() -> None:
    base = _floor_streams(10_000.0, 4_000.0)
    rebased, note = MilpEngine._rebase_dispatch_floor_streams(base, None, cannibalised_gbp=0.0)
    assert rebased == base and note is None


# ------------------------------- 4b. ONE netting site, on the COMPOSED result


def _export_site(make_site, *, tariff_gbp_per_kwh: float = 0.08):
    """A site that consents to WHOLESALE_P415 and declares an agreed export tariff — the two
    conditions that put contracted export revenue into the priced floor (D43/N22)."""
    site = _site(make_site, mic_kw=420.0, asc_kw=None, peak_kw=200.0)
    return s.SiteModel.model_validate(
        {
            **site.model_dump(mode="python"),
            "stream_consents": (
                s.RevenueStreamConsent(stream=s.MarketStream.WHOLESALE_P415, consented=True),
            ),
            "export_arrangement": s.ExportArrangement(export_tariff_gbp_per_kwh=tariff_gbp_per_kwh),
        }
    )


def _export_priced_engine(overlay, *, cannibalised_gbp: float, export_mwh: float) -> MilpEngine:
    """An engine in the exact state `_final_result` sees after a successful export overlay."""
    engine = MilpEngine()
    engine._export_overlay_schedule = overlay
    engine._export_volumes_reconciled = True
    engine._export_upside_result = ExportUpside(
        delta_gbp=1.0,
        export_mwh=export_mwh,
        price_content_hash="hash",
        price_source_ref="ref",
        coverage_note=None,
        cannibalised_floor_gbp=cannibalised_gbp,
    )
    return engine


def _chosen_with_gated_wholesale(streams: list[s.ValueStreamResult]) -> CandidateEvaluation:
    base = _evaluation(100.0, 0.0)
    result = base.optimisation_result.model_copy(
        update={
            "value_streams": streams,
            "gated_upside": [
                s.ValueStreamResult(
                    stream=s.MarketStream.WHOLESALE_P415,
                    firmness=s.Firmness.CONDITIONAL,
                    gate_status="conditional",
                )
            ],
        }
    )
    return replace(base, optimisation_result=result)


def test_the_composed_result_nets_once_and_says_where(make_site) -> None:
    """The whole point of limb (b), end to end on `_final_result`.

    TWO implementations of the netting live in this tree and they are ALTERNATIVES: this engine
    re-bases the dispatch components and keeps the export entry gross, while
    `valuation.wholesale_contracted_stream_result` can instead subtract the cannibalised £ from the
    export entry. Applying both deducts the same £ twice. This pins the engine's choice AND the
    record it leaves, so the valuation half moving underneath cannot silently produce either a
    double-net or a contradictory record.
    """
    overlay = _schedule(
        load_kw=np.array([100.0, 100.0]),
        charge_kw=np.array([0.0, 0.0]),
        discharge_kw=np.array([60_000.0, 0.0]),  # arbitrage £6,000 + DUoS £3,000
    )
    engine = _export_priced_engine(overlay, cannibalised_gbp=5_000.0, export_mwh=100.0)
    site = _export_site(make_site)
    chosen = _chosen_with_gated_wholesale(_floor_streams(10_000.0, 4_000.0))
    request = engine.build_optimisation_request(site, load_assumptions("gb_v0_1"))

    result = engine._final_result(request, chosen)

    joined = " ".join([*result.warnings, *(w for v in result.value_streams for w in v.warnings)])
    # the netting happened, once, on the dispatch components
    assert "EXPORT-FLOOR NETTING APPLIED" in joined
    assert "NETTED ON THE DISPATCH COMPONENTS" in joined
    # ...and the stale claim that the floor is still gross-and-overstated is GONE, not corrected
    # underneath it: a record that states an overstatement and then retracts it is the
    # contradiction class the review keeps re-raising.
    assert "NOT NETTED" not in joined

    contracted = next(v for v in result.value_streams if v.stream == s.MarketStream.WHOLESALE_P415)
    assert contracted.firmness == s.Firmness.CONTRACTED
    # GROSS by design: £0.08/kWh x 100 MWh = £8,000, NOT netted a second time here.
    assert contracted.annual_revenue_gbp == pytest.approx(8_000.0)


def test_the_composed_floor_is_overlay_terms_plus_gross_export_not_no_export_terms(
    make_site,
) -> None:
    """The arithmetic identity that makes the total physically possible. The pre-fix floor was
    ``no-export terms + gross export``; it is now ``overlay terms + gross export``, lower by exactly
    the measured cannibalised value."""
    overlay = _schedule(
        load_kw=np.array([100.0, 100.0]),
        charge_kw=np.array([0.0, 0.0]),
        discharge_kw=np.array([60_000.0, 0.0]),
    )
    base_streams = _floor_streams(10_000.0, 4_000.0)
    engine = _export_priced_engine(overlay, cannibalised_gbp=5_000.0, export_mwh=100.0)
    site = _export_site(make_site)
    request = engine.build_optimisation_request(site, load_assumptions("gb_v0_1"))

    result = engine._final_result(request, _chosen_with_gated_wholesale(base_streams))

    dispatch_derived = [
        v
        for v in result.value_streams
        if v.stream in (s.MarketStream.TARIFF_ARBITRAGE, s.MarketStream.DUOS_RED_BAND_AVOIDANCE)
    ]
    assert _net(dispatch_derived) == pytest.approx(overlay.priced_floor_terms_gbp)
    # CM (£7,000) is not dispatch-derived and is untouched; export is gross (£8,000).
    assert _net(result.value_streams) == pytest.approx(
        overlay.priced_floor_terms_gbp + 7_000.0 + 8_000.0
    )
    # exactly the measured cannibalised £5,000 lower than the physically impossible pre-fix total
    assert _net(result.value_streams) == pytest.approx(_net(base_streams) + 8_000.0 - 5_000.0)


def test_the_export_entry_is_never_netted_below_the_overlays_own_objective(make_site) -> None:
    """Why the export entry is NOT the netting site. The overlay is chosen against the wholesale
    FORECAST but this entry is priced at the AGREED TARIFF, so the tariff can underprice what the
    overlay gave up (``cannibalised > gross``). Netting on the entry clamps at £0 and DELETES the
    excess, lifting the reported floor ABOVE the overlay's objective — the F-02 clamp defect. Here
    the excess lands on the components' cost channel instead, so nothing is deleted."""
    overlay = _schedule(
        load_kw=np.array([100.0, 100.0]),
        charge_kw=np.array([60_000.0, 0.0]),  # bought at a loss to free energy for the wire
        discharge_kw=np.array([0.0, 0.0]),
    )
    engine = _export_priced_engine(overlay, cannibalised_gbp=20_000.0, export_mwh=100.0)
    site = _export_site(make_site)
    request = engine.build_optimisation_request(site, load_assumptions("gb_v0_1"))

    result = engine._final_result(request, _chosen_with_gated_wholesale(_floor_streams(1.0, 1.0)))

    contracted = next(v for v in result.value_streams if v.stream == s.MarketStream.WHOLESALE_P415)
    assert contracted.annual_revenue_gbp == pytest.approx(8_000.0)  # gross, unclamped
    dispatch_derived = [
        v
        for v in result.value_streams
        if v.stream in (s.MarketStream.TARIFF_ARBITRAGE, s.MarketStream.DUOS_RED_BAND_AVOIDANCE)
    ]
    # the overlay's floor terms are NEGATIVE here and survive as a cost, never clamped to £0
    assert overlay.priced_floor_terms_gbp < 0.0
    assert _net(dispatch_derived) == pytest.approx(overlay.priced_floor_terms_gbp)


def test_restating_the_netting_site_leaves_a_genuinely_gross_entry_alone() -> None:
    """``rebased=False`` means no component was re-based, so the valuation half's gross warning is
    TRUE and must survive. The strip is a correction, never a blanket suppression."""
    entry = s.ValueStreamResult(
        stream=s.MarketStream.WHOLESALE_P415,
        annual_revenue_gbp=8_000.0,
        firmness=s.Firmness.CONTRACTED,
        warnings=["GROSS — NOT NETTED (MD-F-05 limb b): ..."],
    )
    unchanged = MilpEngine._restate_export_netting_site(entry, cannibalised_gbp=0.0, rebased=False)
    assert unchanged == entry
    restated = MilpEngine._restate_export_netting_site(
        entry, cannibalised_gbp=5_000.0, rebased=True
    )
    assert not any("NOT NETTED" in w for w in restated.warnings)
    assert any("NETTED ON THE DISPATCH COMPONENTS" in w for w in restated.warnings)
    assert restated.annual_revenue_gbp == 8_000.0  # the £ is untouched by the restatement


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
