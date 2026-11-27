"""Mutation checks for WP-THERM-05 (D-NEW-THERM05-4): each clamp of the safety filter, and the
switch-off lockout guard, is weakened in memory in a fresh interpreter, and the closed-loop test
named beside it must go red (pytest exit 1). Each (module, node) has a control: the same node,
same finder, unmutated source, exit 0. The runner is the IC-11 one.

The filter's bounds each live in one `SafeSet` property, so one mutation weakens one guard
everywhere it is read (the minute's extremes, the end state and the stored-heat bound).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "contracts"))
from _contracts_support import run_mutant  # noqa: E402

FILTER = "gridq.thermal.safety_filter"
CTRL = "gridq.thermal.controller"
SAFETY = TESTS / "test_thermal_controller_safety.py"
TRANS = TESTS / "test_thermal_controller_transitions.py"
LOCKOUT = "test_a_switch_off_that_would_leave_too_few_units_keeps_every_limit"

#: (guard, module, exact source text, its in-memory replacement, test file, test).
MUTATIONS: list[tuple[str, str, str, str, Path, str]] = [
    (
        "IT inlet (hall) maximum",
        FILTER,
        "        return self.plant.hall_up\n",
        "        return self.plant.hall_up + 0.5\n",
        SAFETY,
        "test_the_filter_alone_quick[base]",
    ),
    (
        "floor: binding minimum, humidity and loop minimum",
        FILTER,
        "        return -self.plant.under_allow\n",
        "        return -self.plant.under_allow - 0.5\n",
        SAFETY,
        "test_the_filter_alone_quick[base]",
    ),
    (
        "chilled-water loop maximum (tight fixture)",
        FILTER,
        "        return self.plant.loop_up\n",
        "        return self.plant.loop_up + 0.5\n",
        SAFETY,
        "test_the_filter_alone_quick[tight]",
    ),
    (
        "rate of change, 60 and 15 minutes (tight fixture)",
        FILTER,
        "        return self.plant.f_rate\n",
        "        return self.plant.f_rate * 2\n",
        SAFETY,
        "test_the_filter_alone_quick[tight]",
    ),
    (
        "switch-off recovery lockout",
        CTRL,
        "                pol.keep_units * p.unit_kwth >= hi\n",
        "                pol.keep_units * p.unit_kwth >= hi - 1e9\n",
        TRANS,
        LOCKOUT,
    ),
]
IDS = [m[0] for m in MUTATIONS]


def _source(module: str) -> str:
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None
    return Path(spec.origin).read_text(encoding="utf-8")


CONTROLS = sorted({(m[1], m[4], m[5]) for m in MUTATIONS})


def test_every_mutant_has_a_control() -> None:
    assert {(m[1], m[4], m[5]) for m in MUTATIONS} == set(CONTROLS)
    assert len(CONTROLS) == 3


@pytest.mark.parametrize("module,file,test", CONTROLS, ids=[c[2] for c in CONTROLS])
def test_the_control_passes_on_the_unmutated_source(module: str, file: Path, test: str) -> None:
    assert run_mutant(module, "", "", f"{file}::{test}") == 0, test


@pytest.mark.parametrize("guard,module,old,new,file,test", MUTATIONS, ids=IDS)
def test_weakening_a_guard_turns_its_test_red(
    guard: str, module: str, old: str, new: str, file: Path, test: str
) -> None:
    assert _source(module).count(old) == 1, guard
    assert run_mutant(module, old, new, f"{file}::{test}") == 1, guard
