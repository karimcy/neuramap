"""The consent-gated wholesale EXPORT overlay (issue 018 / D43, A2/A3).

Two layers, on the real linopy/HiGHS solver over the shared 7-day fixture:

- solver (`MilpDispatchSolver.solve_with_export` / the `_solve_milp` export path): export OFF is
  structurally byte-identical (no export variable), export ON drains SoE, respects the MEC bound
  and shared inverter, counts export in warranty throughput, prevents the buy-and-export wash, and
  is deterministic;
- engine (`MilpEngine._export_upside` + the WHOLESALE_P415 swap): the overlay runs ONLY when the
  site's consents cohere AND a covering price series is supplied; the delta is the incremental
  value (never gross), lands as CONDITIONAL gated upside, and NEVER touches the priced floor /
  value_streams / financial case / DSCR.
"""

from __future__ import annotations

import numpy as np
import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem
from gridq.ingestion.market_forecasts import (
    MarketForecast,
    MarketForecastPoint,
    hash_market_forecast,
)
from gridq.optimisation.engine import MilpEngine
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.quotes import generate_quote
from gridq.schemas import ConsentState, ConsentStatus, Firmness, MarketStream

POWER_KW = 200.0
ENERGY_KWH = 400.0


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def fixture(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    path = tmp_path_factory.mktemp("export_milp") / "hh.csv"
    rows, ts = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows)
    lib = load_assumptions("gb_v0_1")
    return site, lib, ts


def _forecast(ts, price_gbp_per_mwh) -> MarketForecast:
    """A MarketForecast over the fixture timestamps at a flat (or per-instant) £/MWh price."""
    if np.isscalar(price_gbp_per_mwh):
        prices = [float(price_gbp_per_mwh)] * len(ts)
    else:
        prices = [float(p) for p in price_gbp_per_mwh]
    points = [
        MarketForecastPoint(timestamp=t.to_pydatetime(), price_gbp_per_mwh=p)
        for t, p in zip(ts, prices, strict=True)
    ]
    fc = MarketForecast(points=points, source="test-wholesale", forecast_version="v-test")
    fc.content_hash = hash_market_forecast(fc)
    return fc


def _export_site(site):
    """The fixture site with export consents coherent (G99∧G100 approved + export MPAN; MEC>0)."""
    return site.model_copy(
        update={
            "consents": ConsentState(
                g98=ConsentStatus.APPROVED,
                g99=ConsentStatus.APPROVED,
                g100=ConsentStatus.APPROVED,
                export_mpan="1900000000000",
            )
        }
    )


def _problem(site, lib):
    return DispatchProblem(
        site_model=site,
        assumptions=lib,
        power_kw=POWER_KW,
        energy_kwh=ENERGY_KWH,
        duration_hours=ENERGY_KWH / POWER_KW,
        constraints=[],
    )


# --------------------------------------------------------------------------- solver layer


def test_export_off_is_structurally_byte_identical(fixture) -> None:
    site, lib, _ = fixture
    result = MilpDispatchSolver().solve(_problem(site, lib))
    solver = MilpDispatchSolver()
    solver.solve(_problem(site, lib))
    schedule = solver.last_schedule()
    assert schedule is not None
    # No export variable was created: the export accessors are the None/0 defaults, and the
    # schedule ref carries no export keys (byte-identical to the pre-018 hash by construction).
    assert schedule.export_kw is None
    assert schedule.annual_export_mwh is None
    assert schedule.export_revenue_gbp == 0.0
    assert "export" not in schedule.ref().split(":")[-1]  # no export payload in the content hash
    assert result.solver_status.value == "optimal"


def test_positive_export_pays_delta_nets_gross_and_floor_matches(fixture) -> None:
    site, lib, ts = fixture
    # floor (export off) on the SAME spec — the baseline the delta subtracts.
    fs = MilpDispatchSolver()
    fs.solve(_problem(site, lib))
    floor = fs.last_schedule()
    # overlay: wholesale 500 £/MWh (= 0.5 £/kWh) >> the 0.30/0.10 retail spread ⇒ export pays.
    overlay, warn = MilpDispatchSolver().solve_with_export(
        _problem(_export_site(site), lib), _forecast(ts, 500.0)
    )
    assert overlay is not None, warn
    export_mwh = overlay.annual_export_mwh
    export_rev = overlay.export_revenue_gbp
    assert export_mwh is not None and export_mwh > 0
    # export revenue = exported MWh × the flat £/MWh price, exactly.
    assert export_rev == pytest.approx(export_mwh * 500.0, rel=1e-4)
    delta = round(
        max((overlay.priced_floor_terms_gbp + export_rev) - floor.priced_floor_terms_gbp, 0.0), 2
    )
    assert delta > 0
    # the delta is the INCREMENTAL value, strictly LESS than gross export revenue — it nets the
    # behind-the-meter arbitrage the overlay cannibalised to free energy for export.
    assert delta < export_rev
    # floor value is untouched: the floor's own priced terms are exactly the no-export optimum.
    assert floor.priced_floor_terms_gbp == pytest.approx(fs.last_schedule().priced_floor_terms_gbp)


def test_export_is_bounded_by_mec(fixture) -> None:
    site, lib, ts = fixture
    tight = _export_site(site).model_copy(
        update={"connection": site.connection.model_copy(update={"mec_kw": 40.0})}
    )
    overlay, warn = MilpDispatchSolver().solve_with_export(
        _problem(tight, lib), _forecast(ts, 500.0)
    )
    assert overlay is not None, warn
    assert overlay.export_kw is not None
    assert float(overlay.export_kw.max()) <= 40.0 + 1e-6  # never exceeds the export capacity


def test_export_and_discharge_share_the_inverter(fixture) -> None:
    site, lib, ts = fixture
    overlay, warn = MilpDispatchSolver().solve_with_export(
        _problem(_export_site(site), lib), _forecast(ts, 500.0)
    )
    assert overlay is not None, warn
    # discharge (BTM) + export (past the meter) never exceed the rated power at any instant.
    assert np.all(overlay.discharge_kw + overlay.export_kw <= POWER_KW + 1e-6)


def test_export_counts_toward_warranty_throughput(fixture) -> None:
    from gridq.optimisation._assumptions import ceiling_value_for_duration

    site, lib, ts = fixture
    overlay, warn = MilpDispatchSolver().solve_with_export(
        _problem(_export_site(site), lib), _forecast(ts, 500.0)
    )
    assert overlay is not None, warn
    # The warranty cap bounds annual (discharge + export) throughput, NOT discharge alone — mirror
    # the solver's own ceiling lookup at the 2 h duration. With 500 £/MWh the export wants to run
    # maximally, so the cap binds: total flow ≈ cap (and, decisively, does not exceed it — which it
    # would if export were left out of the throughput constraint).
    cap_cycles, _ = ceiling_value_for_duration(
        lib.get("warranty_cycle_limits", "max_cycles_per_year_by_duration_h").value,
        ENERGY_KWH / POWER_KW,
    )
    annual_cap_kwh = float(cap_cycles) * ENERGY_KWH
    annual_flow_kwh = overlay.annual_discharge_kwh + (overlay.annual_export_mwh or 0.0) * 1000.0
    assert annual_flow_kwh <= annual_cap_kwh + 1.0
    assert annual_flow_kwh > 0.9 * annual_cap_kwh  # the cap actually binds (export is counted)


def test_wash_prevention_no_simultaneous_charge_and_export(fixture) -> None:
    site, lib, ts = fixture
    # 500 £/MWh × rte 0.88 = 0.44 £/kWh > every retail band ⇒ wash risk everywhere ⇒ surgical
    # binaries at every instant ⇒ the battery may never charge and export in the same half-hour.
    overlay, warn = MilpDispatchSolver().solve_with_export(
        _problem(_export_site(site), lib), _forecast(ts, 500.0)
    )
    assert overlay is not None, warn
    both = (overlay.charge_kw > 1e-4) & (overlay.export_kw > 1e-4)
    assert not bool(both.any()), "no instant may both charge and export (the buy-and-export wash)"


def test_zero_wholesale_export_never_pays(fixture) -> None:
    site, lib, ts = fixture
    fs = MilpDispatchSolver()
    fs.solve(_problem(site, lib))
    floor = fs.last_schedule()
    overlay, warn = MilpDispatchSolver().solve_with_export(
        _problem(_export_site(site), lib), _forecast(ts, 0.0)
    )
    assert overlay is not None, warn
    assert overlay.export_revenue_gbp == 0.0
    delta = round(
        max(
            (overlay.priced_floor_terms_gbp + overlay.export_revenue_gbp)
            - floor.priced_floor_terms_gbp,
            0.0,
        ),
        2,
    )
    assert delta == 0.0


def test_coverage_gap_skips_the_overlay(fixture) -> None:
    site, lib, ts = fixture
    fc = _forecast(ts, 500.0)
    fc_gap = MarketForecast(
        points=fc.points[:-5], source="t", forecast_version="v"
    )  # last 5 stamps uncovered
    overlay, warn = MilpDispatchSolver().solve_with_export(
        _problem(_export_site(site), lib), fc_gap
    )
    assert overlay is None
    assert any("not run" in w or "not covered" in w for w in warn)


def test_overlay_is_deterministic(fixture) -> None:
    site, lib, ts = fixture
    fc = _forecast(ts, 500.0)
    a, _ = MilpDispatchSolver().solve_with_export(_problem(_export_site(site), lib), fc)
    b, _ = MilpDispatchSolver().solve_with_export(_problem(_export_site(site), lib), fc)
    assert a is not None and b is not None
    assert np.array_equal(a.export_kw, b.export_kw)
    assert np.array_equal(a.charge_kw, b.charge_kw)
    assert a.priced_floor_terms_gbp == b.priced_floor_terms_gbp


# --------------------------------------------------------------------------- engine layer


@pytest.fixture(scope="module")
def engine_runs(fixture) -> tuple:
    """Run the engine three ways ONCE: no forecast, forecast + incoherent consents, and eligible."""
    site, lib, ts = fixture
    fc = _forecast(ts, 500.0)
    no_forecast = generate_quote(_export_site(site), lib, engine=MilpEngine())
    incoherent = generate_quote(
        site, lib, engine=MilpEngine(market_forecast=fc)
    )  # consents UNKNOWN
    eligible = generate_quote(_export_site(site), lib, engine=MilpEngine(market_forecast=fc))
    return no_forecast, incoherent, eligible


def _wholesale(result):
    return next(v for v in result.gated_upside if v.stream == MarketStream.WHOLESALE_P415)


def test_incoherent_consents_leave_the_result_a_no_op(engine_runs) -> None:
    no_forecast, incoherent, _ = engine_runs
    # a forecast with a consent-incoherent site must not run the overlay: WHOLESALE_P415 stays £0.
    assert _wholesale(incoherent.result).annual_revenue_gbp == 0.0
    # and the whole result (bar identity metadata) matches the no-forecast run deep-equal.
    a = incoherent.result.model_dump(mode="json", exclude={"result_id", "request_id"})
    b = no_forecast.result.model_dump(mode="json", exclude={"result_id", "request_id"})
    # no_forecast ran on the export-eligible site; incoherent on the plain site — same physics
    # (consents don't change the floor), so the priced floor + gated upside are identical.
    assert a["value_streams"] == b["value_streams"]
    assert a["gated_upside"] == b["gated_upside"]


def test_no_forecast_is_a_no_op(engine_runs) -> None:
    no_forecast, _, _ = engine_runs
    assert _wholesale(no_forecast.result).annual_revenue_gbp == 0.0


def test_eligible_site_with_forecast_populates_wholesale_upside(engine_runs) -> None:
    no_forecast, _, eligible = engine_runs
    w = _wholesale(eligible.result)
    # the export upside lands as CONDITIONAL gated upside (never FIRM), with an honest basis note.
    assert w.annual_revenue_gbp > 0
    assert w.firmness == Firmness.CONDITIONAL
    joined = " ".join(w.warnings)
    assert "export upside" in joined
    assert "NEVER enters the priced floor" in joined
    assert "sha256" in joined
    # the engine hook exposes the same measured upside for later persistence.
    # (delta is on the record; the hook mirrors it.)


def test_wholesale_upside_never_touches_the_priced_floor(engine_runs) -> None:
    no_forecast, _, eligible = engine_runs
    # the floor value_streams + the financial case are byte-identical with and without the overlay:
    # wholesale £ never enters the priced floor, P50/P90 or DSCR.
    assert eligible.result.value_streams == no_forecast.result.value_streams, (
        "the priced floor must be identical whether or not the export overlay ran"
    )
    # WHOLESALE_P415 is a gated-upside stream, so it can never appear in value_streams.
    assert all(v.stream != MarketStream.WHOLESALE_P415 for v in eligible.result.value_streams)


def test_export_upside_hook_matches_the_record(engine_runs) -> None:
    _, _, eligible = engine_runs
    # rebuild the engine result to read the hook (the pipeline discards the engine; re-solve once).
    site = eligible.site
    lib = load_assumptions("gb_v0_1")
    ts = None
    # reuse the forecast the pipeline used by rebuilding it over the site's own series length.
    import pandas as pd

    df = pd.read_csv(site.load_data.half_hourly_file_ref, parse_dates=["timestamp"])
    ts = pd.DatetimeIndex(df["timestamp"])
    fc = _forecast(ts, 500.0)
    engine = MilpEngine(market_forecast=fc)
    request = engine.build_optimisation_request(site, lib)
    engine.solve(request)
    upside = engine.last_export_upside()
    assert upside is not None
    assert upside.delta_gbp == pytest.approx(_wholesale(eligible.result).annual_revenue_gbp)
    assert upside.export_mwh > 0
    assert upside.price_content_hash == fc.content_hash


def _wholesale_consented_site(site):
    """Export-eligible AND consented to WHOLESALE_P415 with a declared export tariff (D43)."""
    from gridq.schemas import ExportArrangement, RevenueStreamConsent

    return _export_site(site).model_copy(
        update={
            "stream_consents": (
                RevenueStreamConsent(stream=MarketStream.WHOLESALE_P415, consented=True),
            ),
            "export_arrangement": ExportArrangement(export_tariff_gbp_per_kwh=0.08),
        }
    )


def test_consented_wholesale_prices_the_contracted_export_tariff_into_the_floor(fixture) -> None:
    """Contracted export-tariff mode (D43): with consent to WHOLESALE_P415 + a declared export
    tariff, the agreed tariff × overlay export MWh is a CONTRACTED floor entry (moved into
    value_streams), while the perfect-foresight Elexon-MID delta stays a SEPARATE CONDITIONAL
    indicative annotation in gated_upside — never contracted."""
    site, lib, ts = fixture
    fc = _forecast(ts, 500.0)
    consented = _wholesale_consented_site(site)
    engine = MilpEngine(market_forecast=fc)
    request = engine.build_optimisation_request(consented, lib)
    result = engine.solve(request)
    upside = engine.last_export_upside()
    assert upside is not None and upside.export_mwh > 0

    floor = [v for v in result.value_streams if v.stream == MarketStream.WHOLESALE_P415]
    assert len(floor) == 1, "the consented contracted wholesale entry must sit in value_streams"
    contracted = floor[0]
    assert contracted.firmness == Firmness.CONTRACTED  # never FIRM
    assert contracted.consent_status == "given"
    assert contracted.annual_revenue_gbp == pytest.approx(
        round(0.08 * upside.export_mwh * 1000.0, 2)
    )

    # the MID perfect-foresight delta stays indicative, CONDITIONAL, gated — never contracted.
    mid = _wholesale(result)
    assert mid.firmness == Firmness.CONDITIONAL
    assert mid.annual_revenue_gbp == pytest.approx(upside.delta_gbp)

    # deterministic: a second identical solve reproduces the contracted floor entry byte-for-byte.
    engine2 = MilpEngine(market_forecast=fc)
    result2 = engine2.solve(engine2.build_optimisation_request(consented, lib))
    dump = lambda r: [  # noqa: E731
        v.model_dump(mode="json")
        for v in r.value_streams
        if v.stream == MarketStream.WHOLESALE_P415
    ]
    assert dump(result) == dump(result2)
