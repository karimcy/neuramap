"""Tests for the provenance layer (hashing, code version, run context)."""

from pathlib import Path

from gridq import provenance as p
from gridq.provenance import RunContext


def test_stable_json_is_deterministic_and_key_order_independent() -> None:
    a = {"b": 1, "a": 2, "nested": {"y": 1, "x": 2}}
    b = {"a": 2, "nested": {"x": 2, "y": 1}, "b": 1}
    assert p.stable_json_dumps(a) == p.stable_json_dumps(b)


def test_hash_json_same_object_same_hash() -> None:
    obj = {"power_kw": 500, "duration_h": 2}
    assert p.hash_json(obj) == p.hash_json(dict(obj))
    assert p.hash_json(obj).startswith("sha256:")


def test_hash_json_changed_object_different_hash() -> None:
    assert p.hash_json({"power_kw": 500}) != p.hash_json({"power_kw": 501})


def test_hash_file_is_stable(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_text("hello gridq")
    assert p.hash_file(f) == p.hash_file(f)
    assert p.hash_file(f).startswith("sha256:")


def test_git_and_code_version_return_strings() -> None:
    assert isinstance(p.get_git_commit(), str)
    assert p.get_git_commit()  # non-empty ("unknown" if not a git checkout)
    assert isinstance(p.get_code_version(), str)
    assert p.get_code_version()


def test_build_run_context_populates_and_is_deterministic() -> None:
    rc = p.build_run_context({"x": 1}, "gb_v0_1", "0.1.0")
    assert isinstance(rc, RunContext)
    assert rc.input_hash.startswith("sha256:")
    assert rc.assumption_version == "gb_v0_1"
    assert rc.schema_version == "0.1.0"
    assert rc.code_version  # git:... or pkg:...
    assert rc.warnings == []
    # run_id derives from the pins, not the clock -> deterministic for identical inputs
    rc2 = p.build_run_context({"x": 1}, "gb_v0_1", "0.1.0")
    assert rc.run_id == rc2.run_id
    # different input -> different run_id
    rc3 = p.build_run_context({"x": 2}, "gb_v0_1", "0.1.0")
    assert rc.run_id != rc3.run_id
