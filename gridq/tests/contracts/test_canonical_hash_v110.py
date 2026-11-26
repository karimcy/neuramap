"""Canonical hashing of the contract-version 1.1.0 fixtures (``fixtures/v1_1_0/``, WP-BO-06).

The 1.0.0 canonical-hash tests (``test_contracts_canonical_hash.py``) parametrised over the 1.1.0
fixture list and its own golden table: pinned hash, key order and whitespace, interpreter seed,
any value change. Every fixture in the folder must have a pinned hash.
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
    ALL_V110_FIXTURES,
    FIXTURES_V110,
    GOLDEN_HASHES,
    GOLDEN_HASHES_V110,
    ID_FIELD_FOR_PREFIX,
    MODEL_FOR_PREFIX,
    load_v110,
    prefix_of,
)
from test_contracts_canonical_hash import SRC, _parse, _shuffle

from gridq.contracts._base import canonical_json, content_hash


def test_golden_hashes_v110_cover_every_v110_fixture() -> None:
    assert sorted(GOLDEN_HASHES_V110) == sorted(p.name for p in ALL_V110_FIXTURES)
    assert not set(GOLDEN_HASHES_V110) & set(GOLDEN_HASHES)
    assert not set(GOLDEN_HASHES_V110.values()) & set(GOLDEN_HASHES.values())


@pytest.mark.parametrize("path", ALL_V110_FIXTURES, ids=lambda p: p.name)
def test_v110_fixture_is_1_1_0_and_matches_its_pinned_hash(path: Path) -> None:
    assert load_v110(path.name)["contract_version"] == "1.1.0"
    obj = _parse(path.name, path.read_text(encoding="utf-8"))
    assert content_hash(obj) == GOLDEN_HASHES_V110[path.name]
    assert json.loads(canonical_json(obj))["contract_version"] == "1.1.0"


@pytest.mark.parametrize("path", ALL_V110_FIXTURES, ids=lambda p: p.name)
@pytest.mark.parametrize("seed", range(5))
def test_v110_hash_is_independent_of_key_order_and_whitespace(path: Path, seed: int) -> None:
    data = load_v110(path.name)
    shuffled = _shuffle(data, random.Random(seed))
    for text in (json.dumps(shuffled), json.dumps(shuffled, indent=4), json.dumps(data)):
        assert content_hash(_parse(path.name, text)) == GOLDEN_HASHES_V110[path.name]


def test_v110_hash_is_stable_across_interpreter_runs() -> None:
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
            [sys.executable, "-c", code, str(FIXTURES_V110), json.dumps(MODEL_FOR_PREFIX)],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        assert json.loads(res.stdout) == GOLDEN_HASHES_V110, seed


@pytest.mark.parametrize("path", ALL_V110_FIXTURES, ids=lambda p: p.name)
def test_any_change_of_value_changes_the_v110_hash(path: Path) -> None:
    data = load_v110(path.name)
    field = ID_FIELD_FOR_PREFIX[prefix_of(path.name)]
    data[field] = data[field] + "-B"
    assert content_hash(_parse(path.name, json.dumps(data))) != GOLDEN_HASHES_V110[path.name]
