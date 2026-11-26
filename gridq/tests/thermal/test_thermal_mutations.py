"""Mutation checks for WP-THERM-03: each guard on the certificate is weakened in memory, in a
fresh interpreter, and the test named beside it must go red (pytest exit 1). Each has a control
(same test, same finder, source unmutated: exit 0). The runner is the IC-11 one
(``tests/contracts/_contracts_support.run_mutant``).

Every check that can end a hold has a mutant, against the fixture variant on which it binds
(`test_thermal_model.test_every_certificate_check_binds_on_some_fixture`): the plant floor and
the hall band on the base fixture, the loop band, the rate of change and the recovery capacity on
the tight one. The recovery mutant is caught inside the build: the rebound recomputes the clear
time exactly and refuses a certified point that cannot clear within the horizon.

Left out on purpose: the running minimum that keeps the frontier monotone in kW. Every check is
already monotone in kW, so the guard is defensive and no fixture can make its mutant differ.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "contracts"))
from _contracts_support import run_mutant  # noqa: E402

CERT = "gridq.thermal.certificate"
FIRM = TESTS / "test_thermal_firmness.py"

#: (guard, module, exact source text, its in-memory replacement, test file, test).
MUTATIONS: list[tuple[str, str, str, str, Path, str]] = [
    (
        "hall (IT inlet) bound",
        CERT,
        "            e / c > plant.hall_up,\n",
        "            e / c > plant.hall_up + 0.5,\n",
        FIRM,
        "test_quick_firmness_check[base]",
    ),
    (
        "plant floor (minimum part load headroom)",
        CERT,
        "            bad_f | (k > bounds.head[:, m : m + 1]),\n",
        "            bad_f | (k > bounds.head[:, m : m + 1] + 20),\n",
        FIRM,
        "test_quick_firmness_check[base]",
    ),
    (
        "loop band (tight fixture)",
        CERT,
        "            (e - ca * d) / c > plant.loop_up,\n",
        "            (e - ca * d) / c > plant.loop_up + 0.5,\n",
        FIRM,
        "test_quick_firmness_check[tight]",
    ),
    (
        "rate of change (tight fixture)",
        CERT,
        "            f > plant.f_rate,\n",
        "            f > plant.f_rate * 2,\n",
        FIRM,
        "test_quick_firmness_check[tight]",
    ),
    (
        "recovery capacity after the hold (tight fixture)",
        CERT,
        "            e > rec_cap[:, m + 1 : m + 2],\n",
        "            e > 2 * rec_cap[:, m + 1 : m + 2],\n",
        FIRM,
        "test_quick_firmness_check[tight]",
    ),
    (
        "rebound energy",
        CERT,
        "        energy = math.ceil(e_end / 3600.0 * slope)\n",
        "        energy = math.ceil(e_end / 3600.0 * slope * 0.5)\n",
        FIRM,
        "test_quick_firmness_check[base]",
    ),
    (
        "rebound peak",
        CERT,
        "        peak = max(1, math.ceil(u_peak * slope))\n",
        "        peak = max(1, math.ceil(u_peak * slope * 0.5))\n",
        FIRM,
        "test_quick_firmness_check[base]",
    ),
]
IDS = [m[0] for m in MUTATIONS]


def _source(module: str) -> str:
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None
    return Path(spec.origin).read_text(encoding="utf-8")


#: One control per distinct (module, test node): a control runs the same node through the same
#: finder with the source unmutated, so the mutants sharing a node share its control.
CONTROLS = sorted({(m[1], m[4], m[5]) for m in MUTATIONS})


def test_every_mutant_has_a_control() -> None:
    assert {(m[1], m[4], m[5]) for m in MUTATIONS} == set(CONTROLS)
    assert len(CONTROLS) == 2


@pytest.mark.parametrize("module,file,test", CONTROLS, ids=[c[2] for c in CONTROLS])
def test_the_control_passes_on_the_unmutated_source(module: str, file: Path, test: str) -> None:
    assert run_mutant(module, "", "", f"{file}::{test}") == 0, test


@pytest.mark.parametrize("guard,module,old,new,file,test", MUTATIONS, ids=IDS)
def test_weakening_a_guard_turns_its_test_red(
    guard: str, module: str, old: str, new: str, file: Path, test: str
) -> None:
    assert _source(module).count(old) == 1, guard
    assert run_mutant(module, old, new, f"{file}::{test}") == 1, guard
