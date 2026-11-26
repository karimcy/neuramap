"""Tests for the BaselineEngine — proves it runs the pipeline without faking bankability."""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.engine import BaselineEngine
from gridq.engine.baseline import BASELINE_POWER_FRACTION


def _solve(
    make_site: Callable[..., s.SiteModel], *, with_max_import: bool = True
) -> s.OptimisationResult:
    lib = load_assumptions("gb_v0_1")
    engine = BaselineEngine()
    req = engine.build_optimisation_request(make_site(with_max_import=with_max_import), lib)
    return engine.solve(req)


def test_returns_valid_result(make_site: Callable[..., s.SiteModel]) -> None:
    result = _solve(make_site)
    assert isinstance(result, s.OptimisationResult)
    assert result.implementation_level == s.ImplementationLevel.BASELINE
    assert result.solver_status == s.SolverStatus.BASELINE
    # sizing is the illustrative fraction of max_import (420 * 0.1 = 42)
    assert result.selected_power_kw == round(420.0 * BASELINE_POWER_FRACTION, 1)
    assert result.duration_hours == 2.0


def test_result_is_deterministic(make_site: Callable[..., s.SiteModel]) -> None:
    lib = load_assumptions("gb_v0_1")
    engine = BaselineEngine()
    req = engine.build_optimisation_request(make_site(with_max_import=True), lib)
    a = engine.solve(req).model_dump(mode="json", exclude={"created_at"})
    b = engine.solve(req).model_dump(mode="json", exclude={"created_at"})
    assert a == b


def test_result_cannot_be_bankable(make_site: Callable[..., s.SiteModel]) -> None:
    result = _solve(make_site)
    assert result.bankable_status == s.BankableStatus.NOT_BANKABLE
    assert any("not bankable" in w.lower() for w in result.warnings)
    # and the schema itself forbids a baseline result claiming bankable_candidate
    bad = result.model_dump(mode="python")
    bad["bankable_status"] = s.BankableStatus.BANKABLE_CANDIDATE
    with pytest.raises(ValidationError):
        s.OptimisationResult.model_validate(bad)


def test_insufficient_data_produces_warnings(make_site: Callable[..., s.SiteModel]) -> None:
    result = _solve(make_site, with_max_import=False)
    assert result.solver_status == s.SolverStatus.INSUFFICIENT_DATA
    assert result.selected_power_kw is None
    assert result.selected_energy_kwh is None
    assert result.duration_hours is None
    assert any("insufficient data" in w.lower() for w in result.warnings)
    assert result.bankable_status == s.BankableStatus.NOT_BANKABLE


def test_no_gated_upside_is_firm_and_nothing_priced(make_site: Callable[..., s.SiteModel]) -> None:
    result = _solve(make_site)
    assert result.gated_upside, "expected gated streams to be listed"
    for v in result.gated_upside:
        assert v.firmness != s.Firmness.FIRM
        assert v.stream in s.GATED_UPSIDE_STREAMS
    # no revenue is invented anywhere
    for v in result.value_streams + result.gated_upside:
        assert v.annual_revenue_gbp == 0.0
    # value_streams hold no gated streams (those live in gated_upside)
    assert all(v.stream not in s.GATED_UPSIDE_STREAMS for v in result.value_streams)
