"""The MPC rolling-horizon dispatch path is quarantined at the production entry point.

Why a raise and not a warning. The path does not fail loudly; it fails **closed-but-silent**. Every
window refuses (a zero state-of-energy seed against a minimum-SoE floor, held there because the
infeasible branch carries the unchanged state forward — an absorbing fixed point, measured 14/14),
and yet the assembled result still reports ``status="optimal"`` with 0.0 volumes, because
``mpc_solver.py`` hard-codes that status and ``milp_solver.solve_window`` swallows the refusal text.
So a caller cannot tell "no dispatch was possible" from "dispatch was worth nothing". Under D45 that
is a confidently wrong number, which ranks as **worse than a blocked run**.

The tests below pin the two halves that matter: the production entry point refuses, and the default
annual path — the one every published pack used — is untouched by the quarantine.
"""

from __future__ import annotations

import pytest

from gridq.engine.errors import InvalidRequestError
from gridq.optimisation import MilpEngine
from gridq.optimisation.mpc_solver import MpcDispatchSolver


def test_use_mpc_is_refused_at_the_production_entry_point() -> None:
    with pytest.raises(InvalidRequestError, match="QUARANTINED"):
        MilpEngine(use_mpc=True)


def test_the_refusal_is_unconditional_not_release_or_config_gated() -> None:
    """No combination of otherwise-valid engine config re-opens the path.

    A quarantine that a caller can configure around is not a quarantine. There is no assumption
    release on which this path is currently trustworthy, so nothing here may act as an escape.
    """
    for kwargs in (
        {"seed": 7},
        {"use_two_stage_search": False},
        {"use_two_stage_search": True},
        {"mpc_settings": None},
    ):
        with pytest.raises(InvalidRequestError, match="QUARANTINED"):
            MilpEngine(use_mpc=True, **kwargs)


def test_the_default_annual_path_is_unaffected() -> None:
    """The quarantine must not touch `use_mpc=False`, which is what production runs.

    Without this, a quarantine that accidentally disabled the working path would look identical to
    one that worked: both would stop MPC from producing numbers.
    """
    engine = MilpEngine()
    assert engine is not None
    # The dispatch solver must be the annual sizing solver, not an MPC instance.
    assert not isinstance(engine._dispatch_solver, MpcDispatchSolver)
    assert engine._dispatch_solver is engine._sizing_solver


def test_the_solver_itself_stays_constructible_so_the_fix_can_be_developed() -> None:
    """Deliberate scope limit: the quarantine is on the ENGINE, not on `MpcDispatchSolver`.

    Quarantining the class would break the three test modules that exercise it directly, which are
    exactly the tests needed to repair it. Blocking the reachable production path is what protects
    priced numbers; blocking the class as well would only obstruct the fix.
    """
    assert MpcDispatchSolver() is not None
