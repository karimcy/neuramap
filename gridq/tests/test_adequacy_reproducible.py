"""WP-SITE-04 acceptance 3: outputs reproduce by hash for the same scenario set, in-process and in
a fresh interpreter (D171).

The explicit-array case (`golden_run`) is pinned to literal hashes: integer arrays, exact
rationals and SHA-256 leave nothing platform-dependent. A load-model (WP-SITE-01) run is checked
for in-process and fresh-interpreter equality but not pinned, as WP-SITE-01 pins no float output.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from datetime import UTC, date, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from tests import _adequacy_support as A
from tests._datacentre_support import isolated_registries, make_inputs

from gridq.adequacy import SynthesisLoad, run_adequacy, size_floor
from gridq.datacentre import synthesise

ROOT = Path(__file__).resolve().parents[1]

#: Pinned for the explicit-array case. Never re-pinned: a change here is a change of method.
GOLDEN_SIMULATION_HASH = "sha256:59d4ac86a014daefc984169119d05142f56566f3b0cfbe96c92c73de6ab61468"
GOLDEN_RESULT_HASH = "sha256:53bed392ef59c2ea67b299ce5756dc5c1dc7d590ee6bfa0833bed5d7a710c0c9"
GOLDEN_PATHS_G1_RESERVE_OFF = (
    "sha256:77f649fd7deea25f4f0f54794a729e91ce9364124716bb3ddb438a03fa054167"
)

_ = isolated_registries  # the fixture, used by name below


@pytest.fixture(scope="module")
def golden() -> Any:
    return A.golden_run()


def test_golden_hashes(golden: Any) -> None:
    assert golden.simulation_hash == GOLDEN_SIMULATION_HASH
    assert golden.content_hash() == GOLDEN_RESULT_HASH
    run = golden.outcome.runs[0]
    assert (run.posture, run.thermal) == ("reserve_held", "off")
    assert dict(run.paths_digests)["g1"] == GOLDEN_PATHS_G1_RESERVE_OFF


def _floats(node: Any) -> list[float]:
    if isinstance(node, float):
        return [node]
    if isinstance(node, dict):
        return [f for v in node.values() for f in _floats(v)]
    if isinstance(node, list | tuple):
        return [f for v in node for f in _floats(v)]
    return []


def test_golden_values_are_exact(golden: Any) -> None:
    run = golden.outcome.runs[0]
    assert run.lolp.point.as_fraction() == Fraction(23, 36)
    assert _floats(golden.model_dump()) == []  # no float anywhere in the record


def test_same_inputs_same_hash_in_process(golden: Any) -> None:
    assert A.golden_run().content_hash() == golden.content_hash()


def test_skip_and_no_skip_give_the_same_record(golden: Any) -> None:
    assert A.golden_run(skip=False).content_hash() == golden.content_hash()


def _fresh(code: str) -> str:
    env = {
        **os.environ,
        "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}",
        "PYTHONHASHSEED": "4242",
    }
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    return out.stdout.strip()


def test_fresh_interpreter_reproduces_the_golden_hash(golden: Any) -> None:
    got = _fresh(
        """
        from tests import _adequacy_support as A
        print(A.golden_run().content_hash())
        """
    )
    assert got == golden.content_hash()


def _synthesis_run() -> Any:
    syn = synthesise(make_inputs(site_id=A.SITE), seed=20261126, draws=72)
    load = SynthesisLoad(synthesis=syn, record_class="synthetic_fixture")
    b = A.bess(initial=1400, operating=1600)
    policy = size_floor(
        A.requirement(fraction=Fraction(1, 2), seconds=1800), b, A.flat_partition(), load=load
    )
    ss = A.scenario_set(
        [("m1", Fraction(1), [(600, 90, 0.7, 300.0), (1100, 30, 1.0, None)])],
        start=datetime(2027, 3, 3, tzinfo=UTC),
    )
    return run_adequacy(
        ss, load, policy, b, A.config(), as_of=date(2027, 1, 10), thermal=A.surface()
    )


def test_load_model_run_reproduces_in_a_fresh_interpreter(isolated_registries: None) -> None:
    here = _synthesis_run()
    assert here.inputs.load.kind == "datacentre_synthesis"  # type: ignore[union-attr]
    assert _synthesis_run().content_hash() == here.content_hash()
    got = _fresh(
        """
        from tests.test_adequacy_reproducible import _synthesis_run
        print(_synthesis_run().content_hash())
        """
    )
    assert got == here.content_hash()
