"""WP-SITE-01 acceptance 3: the same inputs and seed reproduce the series hash.

The hash covers the members, the expected series and every band (rounded to 1e-6 kW, with -0.0
folded into 0.0) plus a manifest of seed, draws, sampling scheme, method version, levels, quantile
definition, the canonical inputs (every stated distribution), the ambient hash and the block size.

Reproduced: in-process, in a fresh interpreter, and as the bytes of every file written. Moved: by
the seed, by the draw count, and by any one stated input. The one-minute window hash is
reproducible too, and a window is the same whichever order windows are asked for in.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from tests._datacentre_support import DRAWS, SEED, make_inputs, uniform

from gridq.datacentre import synthesise
from gridq.datacentre.derive import write_outputs

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def first():  # type: ignore[no-untyped-def]
    return synthesise(make_inputs(), seed=SEED, draws=DRAWS)


def test_same_inputs_and_seed_reproduce_the_series_hash(first) -> None:  # type: ignore[no-untyped-def]
    again = synthesise(make_inputs(), seed=SEED, draws=DRAWS)
    assert again.series_hash == first.series_hash
    assert again.series_hash.startswith("sha256:") and len(again.series_hash) == 71
    assert (again.members_kw == first.members_kw).all()


def test_a_fresh_interpreter_reproduces_the_series_hash(first) -> None:  # type: ignore[no-untyped-def]
    """No hidden state: module caches, import order and registry state do not enter the hash."""
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(REPO)!r})
        from tests._datacentre_support import make_inputs
        from gridq.datacentre import synthesise
        print(synthesise(make_inputs(), seed={SEED}, draws={DRAWS}).series_hash)
        """
    )
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    out = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=True,
        cwd=REPO,
    )
    assert out.stdout.strip() == first.series_hash


def test_the_written_files_are_byte_identical(first, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    a = write_outputs(first, tmp_path / "a")
    b = write_outputs(synthesise(make_inputs(), seed=SEED, draws=DRAWS), tmp_path / "b")
    for x, y in zip(
        (a.expected_csv, a.bands_csv, a.members_npy, a.manifest_json),
        (b.expected_csv, b.bands_csv, b.members_npy, b.manifest_json),
        strict=True,
    ):
        assert x.name == y.name
        assert x.read_bytes() == y.read_bytes()


def test_the_seed_the_draws_and_any_input_move_the_hash(first) -> None:  # type: ignore[no-untyped-def]
    other_seed = synthesise(make_inputs(), seed=SEED + 1, draws=DRAWS)
    more_draws = synthesise(make_inputs(), seed=SEED, draws=DRAWS + 1)
    base = make_inputs()
    nudged = base.model_copy(
        update={
            "distribution_loss": base.distribution_loss.model_copy(
                update={"fraction": uniform(0.01, 0.031)}
            )
        }
    )
    other_input = synthesise(nudged, seed=SEED, draws=DRAWS)
    hashes = {
        first.series_hash,
        other_seed.series_hash,
        more_draws.series_hash,
        other_input.series_hash,
    }
    assert len(hashes) == 4


def test_one_minute_windows_reproduce_and_do_not_depend_on_call_order(first) -> None:  # type: ignore[no-untyped-def]
    start = datetime(2027, 3, 7, 6)
    end = start + timedelta(hours=3)
    w1 = first.one_minute(start, end, jensen_gap_flag_kw=None)
    first.one_minute(datetime(2027, 3, 3), datetime(2027, 3, 5), jensen_gap_flag_kw=None)
    fresh = synthesise(make_inputs(), seed=SEED, draws=DRAWS)
    w2 = fresh.one_minute(start, end, jensen_gap_flag_kw=None)
    assert w1.window_hash == w2.window_hash
    assert (w1.members_kw == w2.members_kw).all()
    w3 = synthesise(make_inputs(), seed=SEED + 1, draws=DRAWS).one_minute(
        start, end, jensen_gap_flag_kw=None
    )
    assert w3.window_hash != w1.window_hash
