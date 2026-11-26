"""Mutation check for the resilience floor (D174): ten seeded faults, each killed.

Each mutant is a one-place source edit of ``gridq.adequacy`` compiled into a throwaway module.
The real code passes every killer (``test_adequacy_kernel.py``); each mutant must fail at least
one. A no-op control (an edit that changes nothing observable) must survive, so the harness is
shown able to tell the two apart.
"""

from __future__ import annotations

import importlib
import inspect
import sys
import types
from collections.abc import Callable

import pytest
from tests import _adequacy_killers as K

ZERO_FLOOR = """

def _zero(policy):
    s = policy.state
    cells = tuple(c.model_copy(update={"floor_kwh": 0}) for c in s.cells)
    st = s.model_copy(update={"cells": cells, "flat_floor_kwh": 0, "fail_safe_floor_kwh": 0})
    return policy.model_copy(update={"state": st})
"""


def _mutant(module: str, old: str, new: str, *, append: str = "") -> types.ModuleType:
    src = inspect.getsource(importlib.import_module(module))
    assert src.count(old) == 1, f"mutation site not unique in {module}: {old!r}"
    mod = types.ModuleType(f"{module}_mutant")
    mod.__dict__["__file__"] = f"<mutant of {module}>"
    sys.modules[mod.__name__] = mod
    mod.__dict__["MUTANT_SOURCE"] = src.replace(old, new) + append
    try:
        exec(compile(mod.MUTANT_SOURCE, mod.__file__, "exec"), mod.__dict__)
    finally:
        del sys.modules[mod.__name__]
    return mod


def _function_mutant(
    mp: pytest.MonkeyPatch, module: str, func: str, old: str, new: str
) -> types.ModuleType:
    """Replace one function of the real module (so its classes stay the real ones)."""
    mod = importlib.import_module(module)
    src = inspect.getsource(getattr(mod, func))
    assert src.count(old) == 1, f"mutation site not unique in {module}.{func}: {old!r}"
    ns = dict(mod.__dict__)
    exec(compile(src.replace(old, new), f"<mutant of {module}.{func}>", "exec"), ns)
    mp.setattr(mod, func, ns[func])
    return mod


def _kernel_mutant(old: str, new: str) -> Callable[[pytest.MonkeyPatch], types.ModuleType]:
    def apply(mp: pytest.MonkeyPatch) -> types.ModuleType:
        from gridq.adequacy import simulator

        mut = _mutant("gridq.adequacy.kernel", old, new)
        mp.setattr(simulator, "run_kernel", mut.run_kernel)
        return mut

    return apply


DISPATCH_COVER = "got, soe = _dispatch(soe, bat_req, Purpose.EVENT_COVER, floor, cover_floor)"

MUTANTS: dict[str, tuple[Callable[..., types.ModuleType], Callable[[types.ModuleType], None]]] = {
    "M1_lower_zero": (
        _kernel_mutant("lower = max(floor_j, purpose_floor_j)", "lower = 0"),
        lambda m: (K.k_dispatch_unit(m), K.k_drain()),
    ),
    "M2_lower_floor_minus_one": (
        _kernel_mutant(
            "lower = max(floor_j, purpose_floor_j)", "lower = max(floor_j, purpose_floor_j) - 1"
        ),
        lambda m: (K.k_dispatch_unit(m), K.k_drain()),
    ),
    "M3_purpose_floor_only": (
        _kernel_mutant("lower = max(floor_j, purpose_floor_j)", "lower = purpose_floor_j"),
        lambda m: K.k_dispatch_unit(m),
    ),
    "M4_cover_bypasses_dispatch": (
        _kernel_mutant(DISPATCH_COVER, "got = bat_req; soe = soe - got"),
        lambda m: (K.assert_structural(m.MUTANT_SOURCE), K.k_drain()),
    ),
    "M5_envelope_floor_only": (
        lambda mp: _mutant(
            "gridq.adequacy.simulator",
            "floor_j = np.maximum(floor_kwh, env_floor) * J_PER_KWH",
            "floor_j = env_floor * J_PER_KWH",
        ),
        lambda m: K.k_adversary_floor(sim=m),
    ),
    "M6_unsigned_zeroes_floor": (
        lambda mp: _mutant(
            "gridq.adequacy.simulator",
            "                        policy=policy,\n",
            '                        policy=policy if approval.kind == "approved" else '
            "_zero(policy),\n",
            append=ZERO_FLOOR,
        ),
        lambda m: K.k_unsigned(sim=m),
    ),
    "M7_previous_floor_after_step_up": (
        lambda mp: _mutant(
            "gridq.adequacy.simulator",
            "        dtype=np.int64,\n    )[p.cells]\n    res_kw",
            "        dtype=np.int64,\n"
            "    )[np.concatenate((p.cells[:1], p.cells[:-1]))]\n    res_kw",
        ),
        lambda m: K.k_step_up(sim=m),
    ),
    "M8_floor_not_ceiled": (
        lambda mp: _function_mutant(
            mp,
            "gridq.adequacy.floor",
            "_energy_to_floor",
            "return max(0, ceil_snapped(kwh / eta))",
            "return max(0, int(kwh / eta))",
        ),
        lambda m: K.k_sizing(floor=m),
    ),
    "M9_one_second_own_clamp": (
        _kernel_mutant(
            DISPATCH_COVER,
            "got, soe = (_dispatch(soe, bat_req, Purpose.EVENT_COVER, floor, cover_floor) "
            "if dt == 60 else (np.minimum(bat_req, np.maximum(soe, 0)), "
            "soe - np.minimum(bat_req, np.maximum(soe, 0))))",
        ),
        lambda m: K.k_drain(resolution_s=1),
    ),
    "M10_merchant_in_events": (
        lambda mp: _mutant(
            "gridq.adequacy.simulator",
            "merchant_w = np.where(window, 0, merch * 1000)",
            "merchant_w = merch * 1000 + 0 * window",
        ),
        lambda m: K.k_merchant_event(sim=m),
    ),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_killed(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    apply, killer = MUTANTS[name]
    mut = apply(monkeypatch)
    with pytest.raises(AssertionError):
        killer(mut)


def test_ten_mutants() -> None:
    assert len(MUTANTS) == 10


def test_control_survives(monkeypatch: pytest.MonkeyPatch) -> None:
    """A behaviour-neutral edit passes every killer: the harness does not kill everything."""
    from gridq.adequacy import simulator

    mut = _mutant(
        "gridq.adequacy.kernel",
        "lower = max(floor_j, purpose_floor_j)",
        "lower = max(purpose_floor_j, floor_j)",
    )
    monkeypatch.setattr(simulator, "run_kernel", mut.run_kernel)
    sim = _mutant("gridq.adequacy.simulator", "_MAX_DEN = 10**6", "_MAX_DEN = 10**6 + 0")
    K.k_dispatch_unit(mut)
    K.k_drain()
    K.k_drain(resolution_s=1)
    K.k_step_up()
    K.k_adversary_floor(sim=sim)
    K.k_merchant_event(sim=sim)
    K.k_unsigned(sim=sim)
    K.k_sizing()
    K.assert_structural(mut.MUTANT_SOURCE)
