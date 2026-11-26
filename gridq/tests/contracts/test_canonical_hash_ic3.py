"""Canonical hashing of the IC-3 1.0.0 fixtures (``fixtures/ic3/``, WP-SIZE-07).

IC-15, the ledger and the edge bind an envelope by its content hash, and the edge trusts a compiled
timeline by its hash alone, so both must be stable: the pinned hash, key order and whitespace, the
interpreter's string-hash seed, and any value change moving the hash. Every fixture in the folder
has a pinned hash, in its own table (`GOLDEN_HASHES` and `GOLDEN_HASHES_V110` are not edited).
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest
from _contracts_support import (
    FIXTURES_IC3,
    GOLDEN_HASHES,
    GOLDEN_HASHES_IC3,
    GOLDEN_HASHES_V110,
    IC3_FIXTURES,
    ID_FIELD_FOR_PREFIX,
    MODEL_FOR_PREFIX,
    load_ic3,
    prefix_of,
)
from test_contracts_canonical_hash import SRC, _parse, _shuffle

from gridq.contracts._base import canonical_json, content_hash


def test_golden_hashes_ic3_cover_every_ic3_fixture() -> None:
    assert sorted(GOLDEN_HASHES_IC3) == sorted(p.name for p in IC3_FIXTURES)
    assert len(IC3_FIXTURES) == 5
    for other in (GOLDEN_HASHES, GOLDEN_HASHES_V110):
        assert not set(GOLDEN_HASHES_IC3) & set(other)
        assert not set(GOLDEN_HASHES_IC3.values()) & set(other.values())


@pytest.mark.parametrize("path", IC3_FIXTURES, ids=lambda p: p.name)
def test_ic3_fixture_is_1_0_0_and_matches_its_pinned_hash(path: Path) -> None:
    data = load_ic3(path.name)
    assert (data["contract"], data["contract_version"]) == ("IC-3", "1.0.0")
    obj = _parse(path.name, path.read_text(encoding="utf-8"))
    assert content_hash(obj) == GOLDEN_HASHES_IC3[path.name]
    assert json.loads(canonical_json(obj))["contract_version"] == "1.0.0"


@pytest.mark.parametrize("path", IC3_FIXTURES, ids=lambda p: p.name)
@pytest.mark.parametrize("seed", range(5))
def test_ic3_hash_is_independent_of_key_order_and_whitespace(path: Path, seed: int) -> None:
    data = load_ic3(path.name)
    shuffled = _shuffle(data, random.Random(seed))
    for text in (json.dumps(shuffled), json.dumps(shuffled, indent=4), json.dumps(data)):
        assert content_hash(_parse(path.name, text)) == GOLDEN_HASHES_IC3[path.name]


def test_ic3_hash_is_stable_across_interpreter_runs() -> None:
    code = (
        "import sys, json, pathlib, importlib\n"
        "table = json.loads(sys.argv[2])\n"
        "out = {}\n"
        "for p in sorted(pathlib.Path(sys.argv[1]).glob('ic*.synthetic.json')):\n"
        "    mod, cls = next(v for k, v in table.items() if p.name.startswith(k))\n"
        "    M = getattr(importlib.import_module(mod), cls)\n"
        "    out[p.name] = M.model_validate_json(p.read_text()).content_hash()\n"
        "print(json.dumps(out))\n"
    )
    for seed in ("0", "1", "4242", "random"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(SRC)}
        res = subprocess.run(
            [sys.executable, "-c", code, str(FIXTURES_IC3), json.dumps(MODEL_FOR_PREFIX)],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        assert json.loads(res.stdout) == GOLDEN_HASHES_IC3, seed


@pytest.mark.parametrize("path", IC3_FIXTURES, ids=lambda p: p.name)
def test_any_change_of_value_changes_the_ic3_hash(path: Path) -> None:
    data = load_ic3(path.name)
    field = ID_FIELD_FOR_PREFIX[prefix_of(path.name)]
    data[field] = data[field] + "-B"
    assert content_hash(_parse(path.name, json.dumps(data))) != GOLDEN_HASHES_IC3[path.name]
