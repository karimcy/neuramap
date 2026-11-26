"""Tests for the optimisation-engine interface."""

from collections.abc import Callable

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.engine import BaselineEngine, OptimisationEngine


def test_baseline_engine_satisfies_interface() -> None:
    assert isinstance(BaselineEngine(), OptimisationEngine)


def test_build_optimisation_request(make_site: Callable[..., s.SiteModel]) -> None:
    lib = load_assumptions("gb_v0_1")
    site = make_site(with_max_import=True)
    req = BaselineEngine().build_optimisation_request(site, lib, scenario_set="central_set")
    assert isinstance(req, s.OptimisationRequest)
    assert req.assumption_version == lib.metadata.version
    assert req.solve_mode == s.SolveMode.BASELINE
    assert req.site_model.site_id == site.site_id
    assert req.scenario_set_id == "central_set"
