"""`DemoEngine` ↔ `MilpEngine` parity for the two rules that had drifted apart.

**Why this file exists at all.** `gridq.app.demo.DemoEngine` is not a mock: it runs the real MILP,
it drives the real sizing search, and it stamps `ImplementationLevel.PRODUCTION` on its result. So
when a rule is fixed in `MilpEngine` and a second, older implementation of the same rule survives
here, the repo does not have "a stale demo" — it has a **production defect on the path the app
actually serves**, and the fix is recorded as landed while the old behaviour keeps running. Two such
divergences were found:

- **two capacity bases.** `demo.py` carried the deleted ``max_import_kw or mic_kw or 0.0`` rule in
  two places (the two-stage call and the legacy grid) — anchor on observed load, connection only as
  a fallback. That is what the engine replaced, because it meant the connection **never reached the
  search box at all**: on the live campaign a 325 kW connection with a 22.6 kW metered peak had its
  box capped at 33.9 kW. The engine now anchors the box's UPPER edge on ``min(MIC, ASC)`` and its
  LOWER edge separately on the site's load-relevant scale (`two_stage.load_relevant_capacity_kw`),
  because raising the basis had also raised the *smallest* searchable system.
- **two export-netting behaviours.** `demo.py` appended GROSS contracted export revenue to the
  UNCHANGED no-export value streams, reporting "full no-export arbitrage + full export volume" —
  two mutually exclusive uses of the same stored kWh (MD-F-05 limb (b)). It never measured the
  cannibalised value at all.

**This was live, not latent.** The demo's `WHOLESALE_P415` consent records
``agreed_rate_gbp: null``, which looks dormant, but `value_stack.gates.agreed_rate` falls back to
``ExportArrangement.export_tariff_gbp_per_kwh`` — £0.075/kWh on the demo site — and the demo's
consents cohere for export with a committed price series present. So the demo's priced floor carried
a contracted export entry, gross, on top of un-rebased components, on every run.

Both are now closed by **delegating to the engine's own implementations** rather than restating them
(a second copy of a rule is how the drift happened). These tests pin the delegation behaviourally,
and the last two pin it structurally: exactly ONE capacity basis and ONE netting site in the tree.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import gridq.app.demo as demo_module
import gridq.schemas as s
from gridq.app.demo import DemoEngine
from gridq.optimisation.engine import ExportUpside, MilpEngine
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.two_stage import (
    POWER_FRACTION_BOUNDS,
    coarse_power_grid_kw,
    load_relevant_capacity_kw,
    power_search_floor_kw,
)
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import CandidateEvaluation

DEMO_SOURCE = Path(demo_module.__file__).read_text(encoding="utf-8")


def _code_only(source: str) -> str:
    """``source`` with every comment and string literal removed.

    The structural guards below assert that a deleted rule's *expression* is gone. Matching raw text
    would also match this module's own prose about that rule (and `demo.py`'s, which documents both
    defects deliberately), so a guard written against the file text would fail for describing the
    thing it forbids — or worse, pass because a comment happened to contain the expression. Tokenise
    instead, so the guards see code and nothing else.
    """
    import io
    import tokenize

    kept: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        kept.append(tok.string)
    return " ".join(kept)


DEMO_CODE = _code_only(DEMO_SOURCE)


# --------------------------------------------------------------------------- helpers


class _SearchReached(Exception):
    """Raised by the stub sizing search to abort `solve` once its arguments are captured.

    The point of the capture tests is which capacity the demo hands the search — everything after
    that is a real full-year MILP. Aborting keeps them sub-second without stubbing the engine.
    """


def _site(make_site, *, mic_kw: float, asc_kw: float | None, peak_kw: float | None) -> s.SiteModel:
    """A site with a chosen connection/peak state and a (never-read) half-hourly ref.

    ``mic_kw=0.0`` is the schema's way of saying "not declared" (`ConnectionDetails.mic_kw` is a
    required non-negative float and `ingestion.missing_data` treats 0 as the missing case).
    """
    site = make_site()
    load = site.load_data.model_copy(
        update={"max_import_kw": peak_kw, "half_hourly_file_ref": "never-read.csv"}
    )
    return site.model_copy(
        update={"load_data": load, "connection": s.ConnectionDetails(mic_kw=mic_kw, asc_kw=asc_kw)}
    )


def _capture_binding_capacity(monkeypatch: pytest.MonkeyPatch, site: s.SiteModel) -> float:
    """Run `DemoEngine.solve` far enough to record the ``binding_capacity_kw`` it searches on."""
    seen: dict[str, Any] = {}

    def _stub(*_args: Any, **kwargs: Any) -> None:
        seen.update(kwargs)
        raise _SearchReached

    monkeypatch.setattr(demo_module, "two_stage_sizing", _stub)
    engine = DemoEngine(market_forecast=None)
    request = engine.build_optimisation_request(site, _assumptions())
    with pytest.raises(_SearchReached):
        engine.solve(request)
    return float(seen["binding_capacity_kw"])


def _assumptions():
    from gridq.assumptions import load_assumptions

    return load_assumptions("gb_v0_1")


def _floor_streams(arbitrage_gbp: float, duos_gbp: float) -> list[s.ValueStreamResult]:
    """Priced-floor components as the NO-EXPORT solve reports them (signed, split F-02 style)."""
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


def _overlay(
    *,
    discharge_kw: np.ndarray,
    charge_kw: np.ndarray | None = None,
    energy_price: float = 0.20,
    duos_rate: float = 0.10,
) -> HalfHourlyDispatchSchedule:
    n = discharge_kw.size
    return HalfHourlyDispatchSchedule(
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=np.zeros(n) if charge_kw is None else charge_kw,
        discharge_kw=discharge_kw,
        soe_kwh=np.zeros(n),
        site_load_kw=np.full(n, 100.0),
        duos_rate_gbp_per_kwh=np.full(n, duos_rate),
        red_band_mask=np.zeros(n, dtype=bool),
        energy_price_gbp_per_kwh=np.full(n, energy_price),
    )


def _chosen(streams: list[s.ValueStreamResult]) -> CandidateEvaluation:
    """A confirmed candidate carrying a WHOLESALE_P415 gated entry — the state `_final_demo_result`
    needs before the contracted export entry can be built."""
    result = s.OptimisationResult(
        result_id="res_parity",
        request_id="req_parity",
        implementation_level=s.ImplementationLevel.PRODUCTION,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=100.0,
        selected_energy_kwh=200.0,
        duration_hours=2.0,
        value_streams=streams,
        gated_upside=[
            s.ValueStreamResult(
                stream=s.MarketStream.WHOLESALE_P415,
                annual_revenue_gbp=0.0,
                firmness=s.Firmness.CONDITIONAL,
            )
        ],
        solver_status=s.SolverStatus.OPTIMAL,
        warnings=["base warning"],
    )
    return CandidateEvaluation(
        candidate=SizingCandidate(power_kw=100.0, duration_hours=2.0),
        optimisation_result=result,
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


def _export_site(make_site, *, tariff_gbp_per_kwh: float | None = 0.075) -> s.SiteModel:
    """A site that consents to WHOLESALE_P415 and declares an agreed export tariff — the two
    conditions that put CONTRACTED export revenue into the priced floor (D43/N22)."""
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


def _primed_demo_engine(
    overlay: HalfHourlyDispatchSchedule | None,
    *,
    cannibalised_gbp: float,
    export_mwh: float,
    reconciled: bool = True,
) -> DemoEngine:
    """A `DemoEngine` in the exact state `_final_demo_result` sees after a successful overlay."""
    engine = DemoEngine(market_forecast=None)
    engine._export_overlay_schedule = overlay
    engine._export_volumes_reconciled = reconciled
    engine._export_upside_result = ExportUpside(
        delta_gbp=1.0,
        export_mwh=export_mwh,
        price_content_hash="hash",
        price_source_ref="ref",
        coverage_note=None,
        cannibalised_floor_gbp=cannibalised_gbp,
    )
    return engine


def _request(site: s.SiteModel) -> s.OptimisationRequest:
    return s.OptimisationRequest(
        request_id="req_parity",
        site_model=site,
        assumption_version="gb_v0_1",
        requested_streams=[s.MarketStream.WHOLESALE_P415],
        solve_mode=s.SolveMode.PRODUCTION,
    )


def _net(streams: list[s.ValueStreamResult]) -> float:
    return round(sum(v.annual_revenue_gbp - v.annual_cost_gbp for v in streams), 2)


# =========================================================== 1. ONE capacity basis


def test_the_demo_searches_on_the_connection_not_on_the_observed_load(
    make_site, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Knowl-Hill-shaped regression, on the DEMO path. 325 kW declared, 22.6 kW metered peak:
    the demo must hand the search the CONNECTION, so the box tops out at 1.5 x 325 kW.

    Under the old ``max_import_kw or mic_kw`` rule it handed over 22.619 and the box stopped at
    33.93 kW — 302 kW of evidenced headroom the search could not reach at any size."""
    site = _site(make_site, mic_kw=325.0, asc_kw=325.0, peak_kw=22.619)
    binding = _capture_binding_capacity(monkeypatch, site)
    assert binding == 325.0
    assert binding != 22.619  # the deleted rule's answer
    assert binding * POWER_FRACTION_BOUNDS[1] > 33.93


def test_the_demo_and_the_engine_derive_the_identical_capacity_basis(
    make_site, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ONE basis in the tree. Across every state `_sizing_basis` distinguishes — connection above
    the peak, connection BELOW the peak (the contradiction the meter refutes), and no connection
    declared at all — the capacity the demo searches on is the engine's own derivation."""
    states = [
        (500.0, 500.0, 248.5),  # connection above the peak (the demo site's own shape)
        (325.0, 325.0, 22.619),  # large connection, tiny load
        (100.0, 100.0, 260.0),  # declared limit BELOW the metered peak
        (0.0, None, 180.0),  # nothing declared -> observed peak, labelled
    ]
    for mic, asc, peak in states:
        site = _site(make_site, mic_kw=mic, asc_kw=asc, peak_kw=peak)
        basis = MilpEngine._sizing_basis(site)
        assert basis is not None
        assert _capture_binding_capacity(monkeypatch, site) == basis.capacity_kw


def test_raising_the_basis_did_not_raise_the_smallest_searchable_system(make_site) -> None:
    """The TWO-ANCHOR result, not just the upper edge.

    Anchoring the box on the connection also multiplied ``0.05 x basis`` — the smallest searchable
    system — by the same factor, which on a low-load/large-connection site collapsed the
    economically relevant band (behind-the-meter discharge is capped at ``min(power, load)``) to a
    single coarse point. The demo passes the binding capacity and lets `two_stage_sizing` derive the
    floor from `load_relevant_capacity_kw`, so the box widens at the top **and** keeps its floor.
    """
    site = _site(make_site, mic_kw=500.0, asc_kw=500.0, peak_kw=248.5)
    basis = MilpEngine._sizing_basis(site)
    assert basis is not None and basis.capacity_kw == 500.0

    relevant = load_relevant_capacity_kw(site, basis.capacity_kw, series_peak_kw=248.5)
    powers = coarse_power_grid_kw(basis.capacity_kw, relevant)
    # Upper edge tracks the WIRE (it used to stop at 1.5 x 248.5 = 372.75 kW)...
    assert max(powers) == pytest.approx(POWER_FRACTION_BOUNDS[1] * 500.0)
    assert max(powers) > POWER_FRACTION_BOUNDS[1] * 248.5
    # ...while the lower edge stays scaled to the LOAD, exactly where it was before.
    assert min(powers) == pytest.approx(power_search_floor_kw(relevant))
    assert min(powers) == pytest.approx(POWER_FRACTION_BOUNDS[0] * 248.5)
    # And the band a viable system would first appear in is still densely covered, not one point.
    assert len([p for p in powers if p <= 248.5]) >= 5


def test_the_demo_derives_the_power_floor_in_exactly_one_place(make_site) -> None:
    """The floor is derived INSIDE `two_stage_sizing`, never restated in the demo — the second
    derivation site is precisely the defect class this file exists to close.

    `demo.py` *documents* the two-anchor domain (it must, to explain why it passes only the binding
    capacity), so this is asserted against code with comments and docstrings stripped."""
    assert "load_relevant_capacity_kw" not in DEMO_CODE
    assert "power_search_floor_kw" not in DEMO_CODE


def test_an_absent_capacity_is_refused_and_never_read_as_zero(make_site) -> None:
    """D45's no-null amendment: ``or 0.0`` conflated "genuinely zero" with "absent" and let a null
    silently read as a number. A site that evidences NEITHER a connection limit nor an observed peak
    now gets a first-class insufficient-data verdict naming both fields."""
    site = _site(make_site, mic_kw=0.0, asc_kw=None, peak_kw=None)
    assert MilpEngine._sizing_basis(site) is None

    engine = DemoEngine(market_forecast=None)
    result = engine.solve(engine.build_optimisation_request(site, _assumptions()))
    assert result.solver_status == s.SolverStatus.INSUFFICIENT_DATA
    assert result.selected_power_kw is None
    assert result.implementation_level == s.ImplementationLevel.PRODUCTION
    joined = " ".join(result.warnings)
    assert "connection.mic_kw" in joined and "load_data.max_import_kw" in joined
    assert engine.last_sizing_basis() is None


def test_no_second_capacity_basis_survives_in_the_demo_module() -> None:
    """Structural guard on the exact expression that drifted. The fix was recorded as landed while
    ``base = site.load_data.max_import_kw or site.connection.mic_kw or 0.0`` kept running in a
    PRODUCTION-stamped path — twice. It must not come back by copy-paste."""
    assert "max_import_kw or" not in DEMO_CODE
    assert "mic_kw or" not in DEMO_CODE
    assert "asc_kw or" not in DEMO_CODE
    # D45's amendment is about a null reading as a NUMBER on the pricing path, so the guard is on
    # the capacity expression rather than on `or 0.0` everywhere: one `or 0.0` legitimately survives
    # (``annual_export_mwh or 0.0``, character-for-character the engine's own line — "fixing" it
    # here would recreate the divergence this file exists to close).
    capacity_tokens = ("mic_kw", "asc_kw", "max_import_kw", "capacity_kw", "binding_capacity")
    for line in DEMO_SOURCE.splitlines():
        code = line.split("#", 1)[0]
        if "or 0.0" in code or "or 0)" in code:
            assert not any(tok in code for tok in capacity_tokens), (
                f"a capacity is being read through an `or` default — D45: {line.strip()!r}"
            )
    # The basis is obtained from the engine, not re-derived.
    assert "MilpEngine . _sizing_basis ( site )" in DEMO_CODE


def test_the_demo_states_which_capacity_scaled_its_search_box(make_site) -> None:
    """The basis sentence is part of the record: a reader of the persisted sizing surface must be
    able to see WHY the grid stops where it stops (mirrors `MilpEngine._final_result`)."""
    site = _site(make_site, mic_kw=325.0, asc_kw=325.0, peak_kw=22.619)
    basis = MilpEngine._sizing_basis(site)
    assert basis is not None
    engine = _primed_demo_engine(None, cannibalised_gbp=0.0, export_mwh=0.0)
    engine._export_upside_result = None
    engine._sizing_basis_used = basis
    result = engine._final_demo_result(_request(site), _chosen(_floor_streams(10_000.0, 4_000.0)))
    assert basis.note in result.warnings
    assert "DECLARED CONNECTION LIMIT" in " ".join(result.warnings)


# ====================================================== 2. ONE export-netting site


def test_the_demo_rebases_the_dispatch_floor_components_onto_the_overlay(make_site) -> None:
    """MD-F-05 limb (b) on the demo path. The base streams are the NO-EXPORT solve's; the demo used
    to append gross export revenue to them unchanged. The dispatch-derived components now carry the
    OVERLAY's own (cannibalised) figures, and capability-priced entries are untouched."""
    site = _export_site(make_site)
    # An overlay that diverted energy to the wire: 60,000 kW x 0.5 h -> £6,000 arb / £3,000 DUoS.
    overlay = _overlay(discharge_kw=np.array([60_000.0, 0.0]))
    engine = _primed_demo_engine(overlay, cannibalised_gbp=5_000.0, export_mwh=10.0)

    result = engine._final_demo_result(_request(site), _chosen(_floor_streams(10_000.0, 4_000.0)))
    by_stream = {v.stream: v for v in result.value_streams}
    arb = by_stream[s.MarketStream.TARIFF_ARBITRAGE]
    duos = by_stream[s.MarketStream.DUOS_RED_BAND_AVOIDANCE]
    assert arb.annual_revenue_gbp == pytest.approx(6_000.0)
    assert duos.annual_revenue_gbp == pytest.approx(3_000.0)
    # Not re-based: its £ comes from capability, not from the arbitrage export cannibalises.
    assert by_stream[s.MarketStream.CAPACITY_MARKET].annual_revenue_gbp == 7_000.0
    assert any("MD-F-05(b)" in w for w in arb.warnings)
    assert any("EXPORT-FLOOR NETTING APPLIED" in w for w in result.warnings)
    # The defect was material, not cosmetic: the un-netted floor really was higher.
    assert _net(_floor_streams(10_000.0, 4_000.0)) > _net(
        [v for v in result.value_streams if v.stream != s.MarketStream.WHOLESALE_P415]
    )


def test_the_demo_nets_the_cannibalised_value_exactly_once(make_site) -> None:
    """There is ONE netting site and applying it twice would deduct the same £ twice.

    The engine nets on the dispatch COMPONENTS and leaves the export entry GROSS; the alternative
    (`valuation.wholesale_contracted_stream_result`'s ``cannibalised_floor_gbp``) nets the entry and
    leaves the components alone. The two are mutually exclusive, so the export entry must carry the
    full ``tariff x MWh``, and the floor must total ``overlay terms + gross export``."""
    site = _export_site(make_site, tariff_gbp_per_kwh=0.075)
    overlay = _overlay(discharge_kw=np.array([60_000.0, 0.0]))
    engine = _primed_demo_engine(overlay, cannibalised_gbp=5_000.0, export_mwh=10.0)

    result = engine._final_demo_result(_request(site), _chosen(_floor_streams(10_000.0, 4_000.0)))
    export = next(v for v in result.value_streams if v.stream == s.MarketStream.WHOLESALE_P415)
    # GROSS: 10 MWh x 1,000 x £0.075/kWh = £750 — NOT £750 - £5,000 clamped to £0.
    assert export.annual_revenue_gbp == pytest.approx(750.0)
    assert export.firmness == s.Firmness.CONTRACTED
    # ...and the total is the physically consistent one.
    dispatch_derived = [
        v
        for v in result.value_streams
        if v.stream in (s.MarketStream.TARIFF_ARBITRAGE, s.MarketStream.DUOS_RED_BAND_AVOIDANCE)
    ]
    assert _net(dispatch_derived) == pytest.approx(overlay.priced_floor_terms_gbp)
    # The entry says WHERE the netting happened, and does not also claim to be un-netted.
    assert any("NOT ON THIS ENTRY" in w for w in export.warnings)
    assert not any("NOT NETTED" in w for w in export.warnings)


def test_the_demo_never_passes_the_rejected_second_deduction(make_site) -> None:
    """Structural guard on double-netting: the demo must not hand
    ``cannibalised_floor_gbp`` to `wholesale_contracted_stream_result`. Its subtraction is CLAMPED
    (``max(0, gross - cannibalised)``), which is the F-02 defect class this repo already fixed once,
    and it would net £ the components have already given up."""
    site = _export_site(make_site)
    overlay = _overlay(discharge_kw=np.array([60_000.0, 0.0]))
    engine = _primed_demo_engine(overlay, cannibalised_gbp=5_000.0, export_mwh=10.0)

    seen: dict[str, Any] = {}
    real = demo_module.wholesale_contracted_stream_result

    def _spy(*args: Any, **kwargs: Any) -> s.ValueStreamResult:
        seen.update(kwargs)
        return real(*args, **kwargs)

    engine_module_attr = "wholesale_contracted_stream_result"
    setattr(demo_module, engine_module_attr, _spy)
    try:
        engine._final_demo_result(_request(site), _chosen(_floor_streams(10_000.0, 4_000.0)))
    finally:
        setattr(demo_module, engine_module_attr, real)
    assert seen, "the contracted export entry was never built"
    assert "cannibalised_floor_gbp" not in seen


def test_a_negative_rebased_component_lands_on_the_cost_channel(make_site) -> None:
    """F-02 discipline survives the demo's re-base: a signed component's negative part is a real
    cost finance subtracts, never a per-component clamp to £0, so ``revenue - cost`` still
    reproduces the overlay schedule exactly and the floor cannot exceed the MILP's own objective."""
    site = _export_site(make_site)
    # An overlay that BUYS at a loss to free energy for the wire.
    overlay = _overlay(discharge_kw=np.array([0.0, 0.0]), charge_kw=np.array([60_000.0, 0.0]))
    engine = _primed_demo_engine(overlay, cannibalised_gbp=1.0, export_mwh=10.0)

    result = engine._final_demo_result(_request(site), _chosen(_floor_streams(10_000.0, 4_000.0)))
    arb = next(v for v in result.value_streams if v.stream == s.MarketStream.TARIFF_ARBITRAGE)
    assert arb.annual_revenue_gbp == 0.0
    assert arb.annual_cost_gbp == pytest.approx(6_000.0)
    assert arb.annual_revenue_gbp - arb.annual_cost_gbp == pytest.approx(
        overlay.arbitrage_value_gbp
    )


def test_contracted_export_is_held_out_when_its_own_volumes_are_unavailable(make_site) -> None:
    """N23: priced export revenue and the charge volume that bought it read the SAME switch, so a
    floor entry without its own dispatch volumes is structurally impossible rather than unlikely.
    The honest degrade — the MID delta still reports as gated upside — and it says so."""
    site = _export_site(make_site)
    engine = _primed_demo_engine(None, cannibalised_gbp=0.0, export_mwh=10.0, reconciled=False)

    result = engine._final_demo_result(_request(site), _chosen(_floor_streams(10_000.0, 4_000.0)))
    assert not any(v.stream == s.MarketStream.WHOLESALE_P415 for v in result.value_streams), (
        "export revenue was priced into the floor with no charge volume behind it"
    )
    note = engine.last_forecast_priced_floor_note()
    assert note is not None and "NOT priced into the floor (N23)" in note


def test_the_demo_states_that_the_forecast_moved_its_priced_floor(make_site) -> None:
    """N22: this is the line that prices a forecast-measured figure into the demo's floor, and the
    demo is a live example of it (£0.075/kWh via `ExportArrangement`), so it is on the record."""
    site = _export_site(make_site)
    overlay = _overlay(discharge_kw=np.array([60_000.0, 0.0]))
    engine = _primed_demo_engine(overlay, cannibalised_gbp=5_000.0, export_mwh=10.0)

    engine._final_demo_result(_request(site), _chosen(_floor_streams(10_000.0, 4_000.0)))
    note = engine.last_forecast_priced_floor_note()
    assert note is not None
    assert "CHANGED A PRICED-FLOOR FIGURE" in note
    assert "0.0750" in note and "N23" in note


def test_no_agreed_export_tariff_leaves_the_floor_untouched(make_site) -> None:
    """The netting fires only where the defect exists. With no declared export tariff there is no
    contracted floor entry, so no re-base happens and the no-export components stand unchanged."""
    site = _export_site(make_site, tariff_gbp_per_kwh=None)
    overlay = _overlay(discharge_kw=np.array([60_000.0, 0.0]))
    engine = _primed_demo_engine(overlay, cannibalised_gbp=5_000.0, export_mwh=10.0)

    base = _floor_streams(10_000.0, 4_000.0)
    result = engine._final_demo_result(_request(site), _chosen(base))
    assert result.value_streams == base
    assert engine.last_forecast_priced_floor_note() is None


def test_the_demos_netting_is_the_engines_netting(make_site) -> None:
    """The parity assertion the whole file is for: the demo's netted floor is byte-identical to the
    engine's own `_rebase_dispatch_floor_streams` + `_restate_export_netting_site` output, because
    it IS that code rather than a copy of it. A future change to the engine's netting site — D46's
    study basis, say — therefore lands on the demo path automatically."""
    site = _export_site(make_site)
    overlay = _overlay(discharge_kw=np.array([60_000.0, 0.0]))
    engine = _primed_demo_engine(overlay, cannibalised_gbp=5_000.0, export_mwh=10.0)
    base = _floor_streams(10_000.0, 4_000.0)

    result = engine._final_demo_result(_request(site), _chosen(base))
    expected, note = MilpEngine._rebase_dispatch_floor_streams(
        base, overlay, cannibalised_gbp=5_000.0
    )
    assert note is not None
    got_floor = [v for v in result.value_streams if v.stream != s.MarketStream.WHOLESALE_P415]
    assert got_floor == expected

    export = next(v for v in result.value_streams if v.stream == s.MarketStream.WHOLESALE_P415)
    restated = MilpEngine._restate_export_netting_site(
        demo_module.wholesale_contracted_stream_result(
            _chosen(base).optimisation_result.gated_upside[0],
            agreed_tariff_gbp_per_kwh=0.075,
            export_mwh=10.0,
            price_content_hash="hash",
            price_source_ref="ref",
            coverage_note=None,
        ),
        cannibalised_gbp=5_000.0,
        rebased=True,
    )
    assert export == restated


def test_the_demo_measures_the_cannibalised_value_it_nets(make_site) -> None:
    """The netting quantity is MEASURED, never assumed: `_export_upside` computes it from the two
    solves it already runs (no-export floor terms minus overlay floor terms, clamped at 0 for solver
    tolerance). Before this change the demo never measured it at all and the field defaulted to £0,
    so the re-base would have moved nothing even once it existed."""
    source = inspect.getsource(DemoEngine._export_upside)
    assert "cannibalised_floor_gbp=cannibalised" in source
    assert "floor_schedule.priced_floor_terms_gbp - overlay_schedule.priced_floor_terms_gbp" in (
        source.replace("\n", " ").replace("  ", " ")
    )
    # ...and it is the MEASURED field that reaches the re-base, not a recomputation.
    final_source = inspect.getsource(DemoEngine._final_demo_result)
    assert "cannibalised_gbp=eu.cannibalised_floor_gbp" in final_source


def test_the_demo_reconciles_finance_volumes_onto_the_overlay_dispatch(make_site) -> None:
    """N23's other half, which is what makes the GROSS export entry sound: the exported energy's own
    charge cost rides the overlay charge volume finance receives. Without this the demo priced
    export revenue against the no-export solve's import — the levies never reached the cost side."""
    site = _export_site(make_site)
    overlay = _overlay(discharge_kw=np.array([40_000.0, 0.0]), charge_kw=np.array([0.0, 50_000.0]))
    engine = _primed_demo_engine(overlay, cannibalised_gbp=100.0, export_mwh=10.0, reconciled=False)
    engine._dispatch_energy = None

    engine._reconcile_export_volumes(site, _chosen(_floor_streams(10_000.0, 4_000.0)))
    assert engine._export_volumes_reconciled is True
    energy = engine.selected_dispatch_energy()
    assert energy is not None
    assert energy.annual_charge_kwh == pytest.approx(overlay.annual_charge_kwh)
    assert energy.annual_discharge_kwh == pytest.approx(overlay.annual_discharge_kwh)


def test_no_second_netting_implementation_survives_in_the_demo_module() -> None:
    """Structural guard: the demo appends the export entry to the RE-BASED streams, never to
    ``chosen.optimisation_result.value_streams`` directly. That expression was the double-count."""
    assert "* chosen . optimisation_result . value_streams ," not in DEMO_CODE
    assert "MilpEngine . _rebase_dispatch_floor_streams (" in DEMO_CODE
    assert "MilpEngine . _restate_export_netting_site (" in DEMO_CODE
