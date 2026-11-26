"""Tests for the dispatch solver contract + the SimpleSolver placeholder.

Proves the placeholder runs the dispatch seam, conforms to the `DispatchResult` contract, stays
honestly not-bankable, prices nothing, and — critically — never invents a charge/discharge volume.
"""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.dispatch import (
    DispatchProblem,
    DispatchResult,
    DispatchSolver,
    SimpleSolver,
    dispatch_constraints_for,
)
from gridq.engine.errors import InvalidRequestError


def _problem(
    make_site: Callable[..., s.SiteModel], *, with_max_import: bool = True
) -> DispatchProblem:
    lib = load_assumptions("gb_v0_1")
    site = make_site(with_max_import=with_max_import)
    streams = list(s.MarketStream)
    return DispatchProblem(
        site_model=site,
        assumptions=lib,
        power_kw=42.0,
        energy_kwh=84.0,
        duration_hours=2.0,
        requested_streams=streams,
        constraints=dispatch_constraints_for(streams, site, lib),
    )


def test_simple_solver_satisfies_protocol() -> None:
    assert isinstance(SimpleSolver(), DispatchSolver)


def test_returns_contract_valid_result(make_site: Callable[..., s.SiteModel]) -> None:
    result = SimpleSolver().solve(_problem(make_site))
    assert isinstance(result, DispatchResult)
    assert result.implementation_level == s.ImplementationLevel.BASELINE
    assert result.solver_status == s.SolverStatus.BASELINE
    # the requested spec is echoed (it is an input, not a solver choice)
    assert result.power_kw == 42.0
    assert result.energy_kwh == 84.0
    assert result.duration_hours == 2.0


def test_result_is_not_bankable(make_site: Callable[..., s.SiteModel]) -> None:
    result = SimpleSolver().solve(_problem(make_site))
    assert result.bankable_status == s.BankableStatus.NOT_BANKABLE
    assert any("not bankable" in w.lower() for w in result.warnings)
    # the schema itself forbids a baseline result claiming bankable_candidate
    bad = result.model_dump(mode="python")
    bad["bankable_status"] = s.BankableStatus.BANKABLE_CANDIDATE
    with pytest.raises(ValidationError):
        DispatchResult.model_validate(bad)


def test_charge_discharge_volume_is_unavailable_never_invented(
    make_site: Callable[..., s.SiteModel],
) -> None:
    result = SimpleSolver().solve(_problem(make_site))
    # the placeholder runs no dispatch, so volumes are unavailable (None), not zero, not invented
    assert result.annual_charge_kwh is None
    assert result.annual_discharge_kwh is None
    assert result.annual_throughput_mwh is None
    assert result.equivalent_full_cycles is None
    assert any("never invented" in w.lower() for w in result.warnings)


def test_nothing_is_priced_and_streams_split_correctly(
    make_site: Callable[..., s.SiteModel],
) -> None:
    result = SimpleSolver().solve(_problem(make_site))
    assert result.priced_revenue_gbp == 0.0
    # gated streams live only in gated_upside; the floor holds no gated stream
    assert result.gated_upside
    for v in result.gated_upside:
        assert v.stream in s.GATED_UPSIDE_STREAMS
        assert v.firmness != s.Firmness.FIRM
    assert all(v.stream not in s.GATED_UPSIDE_STREAMS for v in result.value_streams)
    # no revenue is invented anywhere
    for v in result.value_streams + result.gated_upside:
        assert v.annual_revenue_gbp == 0.0
        assert v.annual_cost_gbp == 0.0


def test_insufficient_data_path(make_site: Callable[..., s.SiteModel]) -> None:
    # a T0 site with no max_import_kw cannot bound charge load against the connection
    result = SimpleSolver().solve(_problem(make_site, with_max_import=False))
    assert result.solver_status == s.SolverStatus.INSUFFICIENT_DATA
    assert result.bankable_status == s.BankableStatus.NOT_BANKABLE
    assert any("insufficient data" in w.lower() for w in result.warnings)
    assert result.annual_charge_kwh is None


def test_result_is_deterministic(make_site: Callable[..., s.SiteModel]) -> None:
    problem = _problem(make_site)
    a = SimpleSolver().solve(problem).model_dump()
    b = SimpleSolver().solve(problem).model_dump()
    assert a == b  # DispatchResult has no created_at, so the full dump is stable


def test_solve_rejects_wrong_problem_type() -> None:
    with pytest.raises(InvalidRequestError):
        SimpleSolver().solve(object())  # type: ignore[arg-type]


def test_gated_stream_in_value_streams_is_rejected() -> None:
    # the DispatchResult validator bars a gated-upside stream from the priced floor
    with pytest.raises(ValidationError):
        DispatchResult(
            power_kw=10.0,
            energy_kwh=20.0,
            duration_hours=2.0,
            implementation_level=s.ImplementationLevel.BASELINE,
            bankable_status=s.BankableStatus.NOT_BANKABLE,
            solver_status=s.SolverStatus.BASELINE,
            value_streams=[
                s.ValueStreamResult(
                    stream=s.MarketStream.WHOLESALE_P415, firmness=s.Firmness.CONDITIONAL
                )
            ],
        )


def test_constraints_union_is_deduped_and_deterministic(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    site = make_site()
    streams = list(s.MarketStream)
    cons_a = dispatch_constraints_for(streams, site, lib)
    cons_b = dispatch_constraints_for(streams, site, lib)
    ids = [c.constraint_id for c in cons_a]
    assert len(ids) == len(set(ids))  # de-duplicated (warranty/throughput repeats across streams)
    assert [c.constraint_id for c in cons_b] == ids  # deterministic order
    assert "connection_charge_limit" in ids
    assert "warranty_throughput_cap" in ids
