"""Tests for `MilpEngine` — the production OptimisationEngine (sizer + MILP) behind the frozen seam.

Conformance, honest degrade on the canonical sites, a real selection on a metered HH site,
determinism, the downstream quote seam, and a solve-time benchmark.
"""

from __future__ import annotations

import time

import pytest

from _milp_support import multi_rate_site, write_hh_csv
from gridq.assumptions import load_assumptions
from gridq.dispatch.contract import DispatchProblem
from gridq.engine.conformance import (
    assert_conformant,
    canonical_conformance_sites,
    check_result_conformance,
)
from gridq.optimisation import MilpDispatchSolver, MilpEngine
from gridq.quotes.pipeline import generate_quote
from gridq.schemas import BankableStatus, ImplementationLevel, SolverStatus


@pytest.fixture
def assumptions():
    return load_assumptions("gb_v0_1")


@pytest.fixture
def hh_site(tmp_path):
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    return multi_rate_site(path, rows=rows)


def test_milp_engine_passes_the_conformance_suite(assumptions):
    # The same bar BaselineEngine passes — no silent drift across the mock→real swap.
    assert_conformant(MilpEngine(), assumptions=assumptions)


def test_canonical_sites_degrade_honestly_without_a_metered_series(assumptions):
    engine = MilpEngine()
    for _label, site in canonical_conformance_sites():
        result = engine.solve(engine.build_optimisation_request(site, assumptions))
        # The canonical sites carry no half_hourly_file_ref, so a real dispatch cannot run.
        assert result.solver_status == SolverStatus.INSUFFICIENT_DATA
        assert result.selected_power_kw is None and result.selected_energy_kwh is None
        assert result.implementation_level == ImplementationLevel.PRODUCTION
        assert result.bankable_status == BankableStatus.NOT_BANKABLE


def test_engine_selects_a_real_spec_on_a_metered_hh_site(hh_site, assumptions):
    engine = MilpEngine()
    result = engine.solve(engine.build_optimisation_request(hh_site, assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL
    assert result.selected_power_kw and result.selected_energy_kwh and result.duration_hours
    assert result.implementation_level == ImplementationLevel.PRODUCTION
    assert result.bankable_status == BankableStatus.NOT_BANKABLE  # placeholder assumptions
    assert result.solver_metadata is not None
    assert result.solver_metadata.model_version == "highs-milp-v1"
    floor = sum(
        v.annual_revenue_gbp
        for v in result.value_streams
        if v.firmness.value in ("firm", "contracted")
    )
    assert floor > 0  # real arbitrage + DUoS value in the priced floor
    # The dispatch-energy hook is populated for the selected spec.
    assert engine.selected_dispatch_energy() is not None


def test_engine_is_deterministic(hh_site, assumptions):
    from gridq.engine.conformance import _result_dump

    engine = MilpEngine()
    request = engine.build_optimisation_request(hh_site, assumptions)
    assert _result_dump(engine.solve(request)) == _result_dump(engine.solve(request))


def test_engine_echoes_request_and_stamps_provenance(hh_site, assumptions):
    engine = MilpEngine()
    request = engine.build_optimisation_request(hh_site, assumptions)
    result = engine.solve(request)
    assert result.request_id == request.request_id
    meta = result.solver_metadata
    assert meta and meta.assumption_version == "gb_v0_1" and meta.code_version


def test_quote_pipeline_consumes_milp_engine_and_stays_not_bankable(hh_site, assumptions):
    run = generate_quote(hh_site, assumptions, engine=MilpEngine())
    assert run.bankable is False  # placeholder library never underwrites as bankable
    assert not [r for r in check_result_conformance(run.result) if not r.passed]


def test_progress_sink_reports_sizing_phases_then_one_dispatch(hh_site, assumptions):
    # C035: the duck-typed progress hook narrates the sizing search, then the single post-sizing
    # "dispatch" re-solve — so the web run bar keeps moving through the long opaque phase.
    ticks: list[str] = []
    engine = MilpEngine()
    engine.set_progress_sink(lambda phase, done, total: ticks.append(phase))
    result = engine.solve(engine.build_optimisation_request(hh_site, assumptions))
    assert result.selected_power_kw  # a real spec selected -> the dispatch phase ran
    assert "coarse" in ticks  # the sizing search narrated itself
    assert ticks.count("dispatch") == 1  # exactly one final dispatch phase
    assert ticks.index("dispatch") > ticks.index("coarse")  # after the search completes


def test_progress_sink_is_byte_neutral(hh_site, assumptions):
    from gridq.engine.conformance import _result_dump

    plain = MilpEngine()
    baseline = _result_dump(plain.solve(plain.build_optimisation_request(hh_site, assumptions)))
    wired = MilpEngine()
    wired.set_progress_sink(lambda *a: None)
    with_sink = _result_dump(wired.solve(wired.build_optimisation_request(hh_site, assumptions)))
    assert with_sink == baseline


def test_raising_progress_sink_is_byte_neutral(hh_site, assumptions):
    # C035: a sink that RAISES mid-solve, fired INSIDE the engine at every emit site, changes
    # nothing — the emit guard swallows it and the deterministic result stays byte-identical to a
    # plain solve. The no-op case above cannot catch a missing guard; only a raising sink can.
    # Compared via the conformance `_result_dump` (as the byte-neutral test above): solver metadata
    # carries a clock field, so a raw `==` on the results would be spuriously unequal.
    from gridq.engine.conformance import _result_dump

    def boom(*a):
        raise RuntimeError("sink blew up")

    plain = MilpEngine()
    baseline = _result_dump(plain.solve(plain.build_optimisation_request(hh_site, assumptions)))
    wired = MilpEngine()
    wired.set_progress_sink(boom)
    with_sink = _result_dump(wired.solve(wired.build_optimisation_request(hh_site, assumptions)))
    assert with_sink == baseline


@pytest.mark.parametrize("days", [364])
def test_benchmark_full_year_dispatch_solve(tmp_path, assumptions, days, capsys):
    path = tmp_path / "hh_year.csv"
    rows, _ = write_hh_csv(path, days=days)
    site = multi_rate_site(path, rows=rows)
    problem = DispatchProblem(
        site_model=site,
        assumptions=assumptions,
        power_kw=200.0,
        energy_kwh=400.0,
        duration_hours=2.0,
    )
    started = time.perf_counter()
    result = MilpDispatchSolver().solve(problem)
    elapsed = time.perf_counter() - started
    assert result.solver_status == SolverStatus.OPTIMAL
    with capsys.disabled():
        print(f"\n[benchmark] full-year ({rows} half-hours) MILP dispatch solved in {elapsed:.3f}s")
    assert elapsed < 30.0  # generous CI ceiling; see docs/manual_benchmark.md
