"""Two-stage search FINAL verdicts: N6 and N7 (WP-0 review 2026-08-10).

Both findings are about the same class of error — a first-class, final refusal ("no viable
configuration", "connection reinforcement") rendered off a STRUCTURAL SEARCH CONSTANT rather than
off the thing being claimed:

- **N6** — a FINAL no-viable verdict could rest on the ten candidates the typical-day PROXY
  ranked highest, and when nothing carries a positive metric that ranking degenerates to the
  ``(-power, -duration)`` tie-break: the ten SMALLEST candidates. Before the verdict may stand, a
  size-diverse cover spanning the box is now solved on the FULL metered series.
- **N7** — "the obligation cannot be met by any battery this connection supports" was raised when
  the provisioning floor escaped the coarse grid (1.5x the site's own peak, 8 h). That grid is
  scaled from the LOAD, not the connection. Reinforcement is now declared only against a declared
  MIC/ASC; a floor inside the connection extends the search box instead.

The search stages are exercised with `_evaluate_spec` stubbed out: these tests are about which
candidates the search decides to SOLVE and which verdict it renders, not about the dispatch.
"""

from __future__ import annotations

from datetime import time

import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.optimisation import two_stage
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.two_stage import (
    NO_VIABLE_COVER_DURATION_HOURS,
    NO_VIABLE_COVER_MAX_SOLVES,
    NO_VIABLE_COVER_POWER_FRACTIONS,
    connection_power_limit_kw,
    no_viable_cover_pairs,
    provisioning_cover_pairs,
    provisioning_reinforcement_reason,
    two_stage_sizing,
)
from gridq.sizing.objective import SizingObjective
from gridq.sizing.provisioning import ProvisioningInfeasibleError
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import CandidateEvaluation

BINDING_KW = 320.0


@pytest.fixture(scope="module")
def site_and_lib(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    path = tmp_path_factory.mktemp("verdicts") / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    return multi_rate_site(path, rows=rows), load_assumptions("gb_v0_1")


def _stub_evaluate(*, project_irr_pct: float | None):
    """A `_evaluate_spec` double: prices every spec, none of them viable (unless told otherwise)."""

    def _evaluate(site, assumptions, candidate: SizingCandidate, **kwargs) -> CandidateEvaluation:
        return CandidateEvaluation(
            candidate=candidate,
            optimisation_result=s.OptimisationResult(
                result_id="res_stub",
                request_id="req_stub",
                implementation_level=s.ImplementationLevel.PRODUCTION,
                bankable_status=s.BankableStatus.NOT_BANKABLE,
                selected_power_kw=candidate.power_kw,
                selected_energy_kwh=candidate.energy_kwh,
                duration_hours=candidate.duration_hours,
                value_streams=[],
                solver_status=s.SolverStatus.OPTIMAL,
            ),
            priceable=True,
            bankable=False,
            min_p90_dscr=None,
            covenant=1.3,
            passes_covenant=None,
            levered_return_pct=None,
            project_irr_pct=project_irr_pct,
            financial=None,
        )

    return _evaluate


def _run(site, lib, **kwargs):
    return two_stage_sizing(
        site,
        lib,
        base_solver=MilpDispatchSolver(),
        objective=SizingObjective(min_p90_dscr_covenant=1.3),
        binding_capacity_kw=BINDING_KW,
        seed=0,
        **kwargs,
    )


# ------------------------------------------------------------------------------ N6


def test_n6_cover_pairs_span_the_box_and_are_capped() -> None:
    pairs = no_viable_cover_pairs(set(), 400.0, lambda p, kwh: True)
    assert len(pairs) <= NO_VIABLE_COVER_MAX_SOLVES
    powers = {p for p, _ in pairs}
    durations = {d for _, d in pairs}
    # Both extremes of both axes are present — the point of the cover.
    assert min(powers) == pytest.approx(min(NO_VIABLE_COVER_POWER_FRACTIONS) * 400.0)
    assert max(powers) == pytest.approx(max(NO_VIABLE_COVER_POWER_FRACTIONS) * 400.0)
    assert min(durations) == min(NO_VIABLE_COVER_DURATION_HOURS)
    assert max(durations) == max(NO_VIABLE_COVER_DURATION_HOURS)


def test_n6_cover_skips_already_confirmed_specs_and_floor_rejects() -> None:
    already = {(140.0, 2.0)}
    pairs = no_viable_cover_pairs(already, 400.0, lambda p, kwh: p >= 100.0)
    assert (140.0, 2.0) not in pairs
    assert all(p >= 100.0 for p, _ in pairs)


def test_n6_no_viable_verdict_is_covered_on_the_full_series_before_it_stands(
    site_and_lib, monkeypatch
) -> None:
    """The failure the finding names: with no candidate carrying a positive metric the proxy
    ranking degenerates to the ten SMALLEST candidates, and a FINAL "this site does not work"
    was rendered off them. The cover must reach the top of the power range and the longest
    duration on the FULL series before the verdict may stand."""
    site, lib = site_and_lib
    monkeypatch.setattr(two_stage, "_evaluate_spec", _stub_evaluate(project_irr_pct=-2.0))

    run = _run(site, lib)

    assert run.selected is None  # the run still refuses — the verdict is honest, just covered
    confirmed = {
        (round(e.candidate.power_kw, 3), round(e.candidate.duration_hours, 2))
        for e in run.candidates_evaluated
        if e.stage == "confirm" and not e.proxy
    }
    for fraction in NO_VIABLE_COVER_POWER_FRACTIONS:
        power = round(fraction * BINDING_KW, 3)
        assert any(p == power for p, _ in confirmed), f"cover missed {power} kW"
    assert any(d == max(NO_VIABLE_COVER_DURATION_HOURS) for _, d in confirmed)
    assert any("no-viable cover (N6)" in w for w in run.warnings)


def test_n6_cover_does_not_run_when_a_candidate_is_viable(site_and_lib, monkeypatch) -> None:
    """It costs nothing on the runs that matter: a search that found a viable size never pays
    for the cover."""
    site, lib = site_and_lib
    monkeypatch.setattr(two_stage, "_evaluate_spec", _stub_evaluate(project_irr_pct=6.0))

    run = _run(site, lib)

    assert not any("no-viable cover (N6)" in w for w in run.warnings)


# ------------------------------------------------------------------------------ N7


def test_n7_reinforcement_only_when_the_floor_exceeds_a_declared_limit() -> None:
    # 700 kW floor, 1,000 kW connection: buildable — the review's worked example.
    assert provisioning_reinforcement_reason(700.0, 6_000.0, 1_000.0) is None
    # Above the connection: the verdict is real and stays final.
    reason = provisioning_reinforcement_reason(1_200.0, 6_000.0, 1_000.0)
    assert reason is not None and "connection reinforcement" in reason


def test_n7_no_declared_limit_can_never_render_a_reinforcement_verdict() -> None:
    """An undeclared MIC is a data ask, not grounds for a final refusal."""
    assert provisioning_reinforcement_reason(5_000.0, 40_000.0, None) is None


def test_n7_cover_pairs_meet_both_floors_and_respect_the_limit() -> None:
    pairs = provisioning_cover_pairs(700.0, 6_000.0, 1_000.0)
    assert pairs
    for power, duration in pairs:
        assert power >= 700.0 - 1e-6  # the power floor
        assert power <= 1_000.0 + 1e-6  # never beyond the connection
        assert power * duration >= 6_000.0 - 1e-6  # the energy floor


def test_n7_connection_limit_is_the_binding_declared_capacity(site_and_lib) -> None:
    site, _ = site_and_lib
    assert connection_power_limit_kw(site) == 500.0  # min(MIC, ASC), never the observed peak
    no_limit = site.model_copy(update={"connection": s.ConnectionDetails(mic_kw=0.0)})
    assert connection_power_limit_kw(no_limit) is None


def _with_requirement(site, *, addition_kw: float, cap_kw: float):
    req = s.ProvisioningRequirement(
        requirement_id="hgv-1",
        windows=[
            s.AvailabilityWindow(
                days_of_week=[0, 1, 2, 3, 4], start_time=time(17), end_time=time(19)
            )
        ],
        additional_load_kw=addition_kw,
        import_cap_kw=cap_kw,
        tenor_years=10.0,
    )
    return site.model_copy(update={"provisioning_requirements": [req]})


def test_n7_floor_outside_the_coarse_grid_but_inside_the_connection_extends_the_search(
    site_and_lib, monkeypatch
) -> None:
    """The exact case the finding describes: the floor beats every site-scaled candidate
    (1.5 x 320 kW = 480 kW) but sits inside the site's 500 kW connection. The old code refused
    the site; the search must now cover the floor instead."""
    site, lib = site_and_lib
    monkeypatch.setattr(two_stage, "_evaluate_spec", _stub_evaluate(project_irr_pct=5.0))
    # unmet = 320 (window load) + 360 - 300 (cap) = 380 kW day one -> ~489 kW at end of
    # tenor: past the 480 kW coarse ceiling, inside the 500 kW connection.
    prov_site = _with_requirement(site, addition_kw=360.0, cap_kw=300.0)

    run = _run(prov_site, lib)  # must not raise ProvisioningInfeasibleError

    assert any("sizing box EXTENDED to cover the provisioning floor" in w for w in run.warnings)
    limit = connection_power_limit_kw(prov_site)
    assert limit is not None
    powers = [e.candidate.power_kw for e in run.candidates_evaluated]
    assert max(powers) > 1.5 * BINDING_KW  # beyond the site-scaled coarse grid
    assert max(powers) <= limit + 1e-6  # and never beyond the connection


def test_n7_floor_beyond_the_connection_is_still_a_final_reinforcement_verdict(
    site_and_lib, monkeypatch
) -> None:
    """The genuine verdict is untouched: a floor the connection cannot carry is still final."""
    site, lib = site_and_lib
    monkeypatch.setattr(two_stage, "_evaluate_spec", _stub_evaluate(project_irr_pct=5.0))
    # unmet = 320 + 900 - 300 = 920 kW at day one, well beyond the 500 kW connection.
    prov_site = _with_requirement(site, addition_kw=900.0, cap_kw=300.0)

    with pytest.raises(ProvisioningInfeasibleError) as excinfo:
        _run(prov_site, lib)
    assert "connection reinforcement" in str(excinfo.value)
    assert "declared limit is 500 kW" in str(excinfo.value)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
