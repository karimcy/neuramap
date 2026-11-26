"""Canonical hashing and frozen schemas for IC-1 / IC-2 / IC-4 / IC-11 / IC-13 ("referenced by
hash" must be stable). For IC-2 this is the WP-CONN-03 acceptance "the hash is stable under
re-serialisation"; for IC-11 it is "canonical hashing stable across key order"."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import random
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from _contracts_support import (
    FIXTURES,
    GOLDEN_HASHES,
    IC1_FIXTURES,
    IC2_FIXTURES,
    IC4_FIXTURES,
    IC11_FIXTURES,
    IC13_CLAIMS_FIXTURES,
    ID_FIELD_FOR_PREFIX,
    MODEL_FOR_PREFIX,
    load,
    prefix_of,
)
from pydantic import BaseModel

from gridq.contracts.__main__ import main as schema_main
from gridq.contracts._base import ContractModel, canonical_json, content_hash
from gridq.provenance.hashing import hash_json

ALL = [*IC1_FIXTURES, *IC2_FIXTURES, *IC4_FIXTURES, *IC11_FIXTURES, *IC13_CLAIMS_FIXTURES]
SRC = Path(__file__).resolve().parents[2] / "src"


def _model_for(name: str) -> type[BaseModel]:
    module, cls = MODEL_FOR_PREFIX[prefix_of(name)]
    model: type[BaseModel] = getattr(importlib.import_module(module), cls)
    return model


def _parse(name: str, text: str) -> ContractModel:
    obj = _model_for(name).model_validate_json(text)
    assert isinstance(obj, ContractModel)
    return obj


def _shuffle(node: Any, rng: random.Random) -> Any:
    if isinstance(node, dict):
        items = list(node.items())
        rng.shuffle(items)
        return {k: _shuffle(v, rng) for k, v in items}
    if isinstance(node, list):
        return [_shuffle(v, rng) for v in node]
    return node


def test_golden_hashes_cover_every_fixture() -> None:
    assert sorted(GOLDEN_HASHES) == sorted(p.name for p in ALL)


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_hash_matches_the_pinned_golden_value(path: Path) -> None:
    obj = _parse(path.name, path.read_text(encoding="utf-8"))
    assert content_hash(obj) == GOLDEN_HASHES[path.name]


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
@pytest.mark.parametrize("seed", range(5))
def test_hash_is_independent_of_key_order_and_whitespace(path: Path, seed: int) -> None:
    data = load(path.name)
    shuffled = _shuffle(data, random.Random(seed))
    assert list(shuffled) != list(data) or seed == 0 or len(data) < 3
    for text in (json.dumps(shuffled), json.dumps(shuffled, indent=4), json.dumps(data)):
        assert content_hash(_parse(path.name, text)) == GOLDEN_HASHES[path.name]


def test_hash_is_stable_across_interpreter_runs() -> None:
    """Fresh interpreters with different str-hash seeds produce the pinned hashes."""
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
            [sys.executable, "-c", code, str(FIXTURES), json.dumps(MODEL_FOR_PREFIX)],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        assert json.loads(res.stdout) == GOLDEN_HASHES, seed


def test_canonical_form_is_sorted_compact_utf8() -> None:
    obj = _parse(ALL[0].name, ALL[0].read_text(encoding="utf-8"))
    text = canonical_json(obj)
    # Re-serialising with the defined parameters is a fixed point: sorted keys, compact, UTF-8.
    again = json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert text == again
    assert (
        content_hash(obj).removeprefix("sha256:")
        == hashlib.sha256(text.encode("utf-8")).hexdigest()
    )


def test_canonical_form_matches_gridq_run_hashing_convention() -> None:
    # Same algorithm as gridq.provenance.hashing for NFC, non-negative-zero data.
    for path in ALL:
        obj = _parse(path.name, path.read_text(encoding="utf-8"))
        assert content_hash(obj) == hash_json(obj.model_dump(mode="json"))


def test_integer_and_float_spellings_hash_equal() -> None:
    data = load("ic1_archetype_firm.synthetic.json")
    text = json.dumps(data).replace('"firm_import_mw": 20.0', '"firm_import_mw": 20')
    assert '"firm_import_mw": 20,' in text
    obj = _parse("ic1_archetype_firm.synthetic.json", text)
    assert content_hash(obj) == GOLDEN_HASHES["ic1_archetype_firm.synthetic.json"]


def test_unicode_normalisation_and_negative_zero_do_not_change_the_hash() -> None:
    name = "ic1_archetype_hybrid.synthetic.json"
    data = load(name)
    data["pcc"]["description"] = unicodedata.normalize("NFC", "Synthétique 33 kV")
    a = content_hash(_parse(name, json.dumps(data)))
    data["pcc"]["description"] = unicodedata.normalize("NFD", "Synthétique 33 kV")
    assert content_hash(_parse(name, json.dumps(data))) == a
    data["notice_period_s"] = -0.0
    b = content_hash(_parse(name, json.dumps(data)))
    data["notice_period_s"] = 0.0
    assert content_hash(_parse(name, json.dumps(data))) == b


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_any_change_of_value_changes_the_hash(path: Path) -> None:
    data = load(path.name)
    field = ID_FIELD_FOR_PREFIX[prefix_of(path.name)]
    data[field] = data[field] + "-B"
    assert content_hash(_parse(path.name, json.dumps(data))) != GOLDEN_HASHES[path.name]


def test_committed_json_schemas_match_the_models() -> None:
    """The freeze: changing a contract model without a version bump fails here."""
    assert schema_main(["--check"]) == 0
