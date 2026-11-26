"""IC-2 CurtailmentScenarioSet v1.0.0 conformance (roadmap WP-CONN-03).

Acceptance:
1. Contract version is tagged and conformance tests pass.
2. Weights sum to one; the hash is stable under re-serialisation.
3. Consumers can run against fixtures without the engine.

Committed fixtures are synthetic-shape (``ic2_*.synthetic.json``). The real UKPN / NPg sets are
git-ignored under ``fixtures/real/`` (eyes-only gate; see its README); their tests run when the
files are present and skip, saying why, when they are not.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import types
import typing
from fractions import Fraction
from pathlib import Path
from typing import Any

import _ic2_fixtures
import _ic2_real
import pytest
from _contracts_support import GOLDEN_HASHES, IC2_FIXTURES, dumps, load
from pydantic import BaseModel, ValidationError

from gridq.contracts import curtailment
from gridq.contracts._base import ContractModel, canonical_json, content_hash
from gridq.contracts.curtailment import (
    CONTRACT_VERSION,
    GIT_TAG,
    CurtailmentScenarioSet,
    EvidenceClass,
    RestrictionSeries,
    expected_energy_mwh,
    expected_series,
    scenario_set_ref,
)

REPLAY = "ic2_replay_three_years.synthetic.json"
MODELLED = "ic2_modelled_hh_only.synthetic.json"
SRC = Path(__file__).resolve().parents[2] / "src"
REAL = _ic2_real.REAL
MANIFEST = json.loads(_ic2_real.MANIFEST.read_text(encoding="utf-8"))
REAL_PRESENT = all((REAL / s["file"]).exists() for s in MANIFEST["sets"].values())
needs_real = pytest.mark.skipif(
    not REAL_PRESENT,
    reason="real UKPN/NPg fixtures are git-ignored (eyes-only gate, fixtures/real/README.md); "
    "run `python tests/contracts/_ic2_real.py fetch && ... build` locally to create them",
)


def _set(data: dict[str, Any]) -> CurtailmentScenarioSet:
    return CurtailmentScenarioSet.model_validate_json(dumps(data))


def _w(n: int, d: int) -> dict[str, int]:
    return {"numerator": n, "denominator": d}


# ---------------------------------------------------------------- 1. version tagged, fixtures pass


def test_contract_version_is_tagged_in_module_and_on_every_instance() -> None:
    assert CONTRACT_VERSION == "1.1.0"
    assert GIT_TAG == f"contracts/ic2-curtailment-scenario-set/v{CONTRACT_VERSION}"
    for path in IC2_FIXTURES:
        obj = _set(load(path.name))
        assert (obj.contract, obj.contract_version) == ("IC-2", "1.0.0")


@pytest.mark.parametrize(
    "field,value",
    [("contract_version", "1.0.1"), ("contract_version", "2.0.0"), ("contract", "IC-1")],
)
def test_a_payload_of_another_contract_or_version_is_refused(field: str, value: str) -> None:
    data = load(REPLAY)
    data[field] = value
    with pytest.raises(ValidationError):
        _set(data)


def test_committed_synthetic_fixtures_equal_a_fresh_build() -> None:
    assert (_ic2_fixtures.REPLAY.read_text(encoding="utf-8")) == _ic2_real.render(
        _ic2_fixtures.build_replay()
    )
    assert (_ic2_fixtures.MODELLED.read_text(encoding="utf-8")) == _ic2_real.render(
        _ic2_fixtures.build_modelled()
    )


def test_spec_ic2_elements_are_required_fields() -> None:
    # spec §10A IC-2: per-scenario half-hourly/minute restriction series, weights, evidence class
    top = CurtailmentScenarioSet.model_fields
    for f in ("scenarios", "evidence_class", "provenance", "signal_basis", "weight_rule"):
        assert top[f].is_required(), f
    sc = curtailment.Scenario.model_fields
    for f in ("weight", "half_hourly", "minute", "events", "period_start"):
        assert sc[f].is_required(), f


def _allows_none(annotation: Any) -> bool:
    if annotation is None or annotation is type(None):
        return True
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _allows_none(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        return any(_allows_none(a) for a in typing.get_args(annotation))
    return origin is typing.Literal and None in typing.get_args(annotation)


def test_no_field_of_any_ic2_model_admits_none() -> None:
    models = [
        o
        for o in vars(curtailment).values()
        if isinstance(o, type) and issubclass(o, ContractModel) and o is not ContractModel
    ]
    assert CurtailmentScenarioSet in models and len(models) > 10
    for m in models:
        for name, f in m.model_fields.items():
            assert not _allows_none(f.annotation), f"{m.__name__}.{name}"


@pytest.mark.parametrize("field", sorted(CurtailmentScenarioSet.model_fields))
def test_null_is_refused_for_every_top_level_field(field: str) -> None:
    data = load(REPLAY)
    data[field] = None
    with pytest.raises(ValidationError):
        _set(data)


def test_set_cannot_be_subclassed_and_instances_are_immutable() -> None:
    with pytest.raises(TypeError, match="final"):

        class Looser(CurtailmentScenarioSet):  # type: ignore[misc]
            pass

    obj = _set(load(REPLAY))
    with pytest.raises(ValidationError):
        obj.set_id = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------- 2a. weights sum to exactly one


def test_fixture_weights_sum_to_exactly_one_in_rational_arithmetic() -> None:
    for path in IC2_FIXTURES:
        obj = _set(load(path.name))
        assert sum((s.weight.as_fraction() for s in obj.scenarios), Fraction(0)) == 1
    assert [str(s.weight.as_fraction()) for s in _set(load(REPLAY)).scenarios] == ["1/3"] * 3


@pytest.mark.parametrize(
    "weights",
    [
        # what a 6-dp decimal contract would carry for 1/3: sums to 0.999999, refused
        [_w(333333, 1000000)] * 3,
        [_w(1, 3), _w(1, 3), _w(1, 4)],
        [_w(1, 2), _w(1, 2), _w(1, 3)],
    ],
)
def test_weights_not_summing_to_exactly_one_are_refused(weights: list[dict[str, int]]) -> None:
    data = load(MODELLED)
    for s, w in zip(data["scenarios"], weights, strict=True):
        s["weight"] = w
    data["weight_basis"] = "model_probability"
    with pytest.raises(ValidationError, match="not exactly 1"):
        _set(data)


@pytest.mark.parametrize(
    "weight,match",
    [
        (_w(2, 6), "lowest terms"),
        (_w(4, 3), "cannot exceed one"),
        (_w(0, 1), "greater than or equal to 1"),
        (_w(1, 0), "greater than or equal to 1"),
        (0.5, "should be an object"),
        ("1/2", "should be an object"),
    ],
)
def test_malformed_weights_are_refused(weight: Any, match: str) -> None:
    data = load(MODELLED)
    data["scenarios"][0]["weight"] = weight
    with pytest.raises(ValidationError, match=match):
        _set(data)


def test_equal_weight_basis_requires_equal_weights() -> None:
    data = load(MODELLED)
    data["weight_basis"] = "equal_over_observed_periods"
    with pytest.raises(ValidationError, match="weights differ"):
        _set(data)


# ------------------------------------------------------- 2b. hash stable on re-serialisation


@pytest.mark.parametrize("path", IC2_FIXTURES, ids=lambda p: p.name)
def test_dump_and_reparse_is_a_fixed_point(path: Path) -> None:
    obj = _set(load(path.name))
    again = CurtailmentScenarioSet.model_validate_json(canonical_json(obj))
    assert again == obj
    assert content_hash(again) == GOLDEN_HASHES[path.name]
    via_python = CurtailmentScenarioSet.model_validate_json(obj.model_dump_json(indent=3))
    assert content_hash(via_python) == GOLDEN_HASHES[path.name]


def test_utc_offset_spelling_does_not_change_the_hash() -> None:
    data = load(REPLAY)
    ev = data["scenarios"][0]["events"][0]
    assert ev["start"].endswith("Z")
    t = ev["start"].replace("T11:52:00Z", "T12:52:00+01:00")
    assert t != ev["start"]
    ev["start"] = t
    data["scenarios"][0]["period_start"] = "2031-06-11T01:00:00+01:00"
    assert content_hash(_set(data)) == GOLDEN_HASHES[REPLAY]


def test_a_naive_time_is_refused() -> None:
    data = load(REPLAY)
    data["scenarios"][0]["period_start"] = "2031-06-11T00:00:00"
    with pytest.raises(ValidationError, match="UTC offset"):
        _set(data)


def test_step_encoding_is_unique() -> None:
    dense = [0.0, 0.0, 1.5, 1.5, 3.0, 0.0]
    s = RestrictionSeries.from_dense(1800, dense)
    assert s.dense() == tuple(dense)
    assert [st.index for st in s.steps] == [0, 2, 4, 5]
    bad = s.model_dump(mode="json")
    bad["steps"].insert(2, {"index": 3, "mw": 1.5})  # same value split in two: non-canonical
    with pytest.raises(ValidationError, match="same value"):
        RestrictionSeries.model_validate_json(json.dumps(bad))
    bad = s.model_dump(mode="json")
    bad["steps"][0]["index"] = 1
    with pytest.raises(ValidationError, match="index 0"):
        RestrictionSeries.model_validate_json(json.dumps(bad))


def test_scenario_order_is_canonical() -> None:
    data = load(REPLAY)
    data["scenarios"].reverse()
    with pytest.raises(ValidationError, match="sorted by scenario_id"):
        _set(data)


def test_scenario_set_ref_points_at_the_content_hash() -> None:
    obj = _set(load(REPLAY))
    ref = scenario_set_ref(obj)
    assert ref.scenario_set_hash == obj.content_hash() == GOLDEN_HASHES[REPLAY]


# ---------------------------------------------------------------- consistency checks


def _first_restricted(data: dict[str, Any]) -> dict[str, Any]:
    sc: dict[str, Any] = next(s for s in data["scenarios"] if s["events"])
    return sc


def test_minute_series_must_average_to_the_half_hourly_series() -> None:
    data = load(REPLAY)
    sc = _first_restricted(data)
    sc["half_hourly"]["steps"][1]["mw"] += 0.01
    with pytest.raises(ValidationError, match="averages"):
        _set(data)


def test_a_restricted_interval_must_be_covered_by_an_event() -> None:
    data = load(MODELLED)
    sc = _first_restricted(data)
    sc["events"] = sc["events"][:-1]
    with pytest.raises(ValidationError, match="not covered by any listed event"):
        _set(data)


def test_series_energy_must_match_event_energy() -> None:
    data = load(MODELLED)
    sc = _first_restricted(data)
    sc["events"][0]["mean_depth_mw"] += 0.1  # peak is mean + 0.25, so the peak check holds
    with pytest.raises(ValidationError, match="MWh"):
        _set(data)


def test_events_must_not_overlap_or_leave_the_period() -> None:
    data = load(MODELLED)
    sc = data["scenarios"][2]
    sc["events"][1]["start"] = sc["events"][0]["start"]
    with pytest.raises(ValidationError, match="overlap"):
        _set(data)
    data = load(MODELLED)
    data["scenarios"][2]["events"][1]["end"] = "2031-01-17T00:00:00Z"
    with pytest.raises(ValidationError, match="outside"):
        _set(data)


def test_period_start_must_be_on_a_half_hour() -> None:
    data = load(MODELLED)
    for s in data["scenarios"]:
        s["period_start"] = "2031-01-14T00:15:00Z"
    with pytest.raises(ValidationError, match="half-hour boundary"):
        _set(data)


def test_depth_cannot_exceed_a_known_reference_capacity() -> None:
    data = load(REPLAY)
    data["reference_capacity_mw"] = 1.0
    with pytest.raises(ValidationError, match="reference_capacity_mw"):
        _set(data)


def test_minute_series_is_all_or_none() -> None:
    data = load(REPLAY)
    data["scenarios"][1]["minute"] = {"kind": "not_applicable", "reason": "x"}
    with pytest.raises(ValidationError, match="every scenario"):
        _set(data)


def test_evidence_class_must_match_provenance_and_site_id() -> None:
    data = load(REPLAY)
    data["evidence_class"] = "modelled"
    with pytest.raises(ValidationError, match="needs provenance"):
        _set(data)
    data = load(REPLAY)
    data["site_id"] = "REAL-LOOKING-SITE"
    with pytest.raises(ValidationError, match="SYNTHETIC-"):
        _set(data)


def test_a_modelled_set_carries_its_engine_and_input_hash() -> None:
    data = load(MODELLED)
    data["evidence_class"] = "modelled"
    data["site_id"] = "SITE-X"
    data["provenance"] = {
        "kind": "modelled",
        "engine": "curtailment_scenario_engine",
        "engine_version": "0.0.0-fixture",
        "input_hash": "sha256:" + "a" * 64,
        "seed": 7,
        "evidence": [],
    }
    assert _set(data).evidence_class is EvidenceClass.MODELLED
    data["provenance"]["input_hash"] = "not-a-hash"
    with pytest.raises(ValidationError):
        _set(data)


def test_a_replayed_source_needs_a_recorded_response() -> None:
    src = {
        "publisher": "X",
        "publisher_role": "dno",
        "dataset": "d",
        "url": "https://example.invalid/d",
        "retrieved_at": "2026-11-17T00:00:00Z",
        "response_sha256": "sha256:" + hashlib.sha256(b"").hexdigest(),
        "licence": "CC BY 4.0",
        "attribution": "X",
        "distribution_gate": {"kind": "not_applicable", "reason": "open"},
        "registered_evidence": {"kind": "unknown", "reason": "not registered"},
    }
    with pytest.raises(ValidationError, match="zero bytes"):
        curtailment.PublishedSource.model_validate_json(json.dumps(src))
    src["publisher_role"] = "neura"
    with pytest.raises(ValidationError):
        curtailment.PublishedSource.model_validate_json(json.dumps(src))


# ---------------------------------------------------------------- 3. consumers without the engine


def test_consumer_helpers_use_exact_weights() -> None:
    obj = _set(load(MODELLED))
    exp = expected_series(obj)
    assert len(exp) == obj.n_half_hours
    by_hand = sum(s.weight.as_float() * s.half_hourly.energy_mwh() for s in obj.scenarios)
    assert expected_energy_mwh(obj) == pytest.approx(by_hand, rel=1e-12)
    assert sum(exp) * 0.5 == pytest.approx(expected_energy_mwh(obj), rel=1e-12)
    with pytest.raises(ValueError, match="no minute series"):
        expected_series(obj, "minute")
    replay = _set(load(REPLAY))
    assert len(expected_series(replay, "minute")) == replay.n_half_hours * 30


_CONSUMER = """
import json, pathlib, sys
from gridq.contracts.curtailment import CurtailmentScenarioSet, expected_energy_mwh, expected_series
out = {}
for p in map(pathlib.Path, sys.argv[1:]):
    s = CurtailmentScenarioSet.model_validate_json(p.read_text(encoding="utf-8"))
    hh = expected_series(s)
    out[p.name] = {
        "hash": s.content_hash(),
        "n": len(hh),
        "energy": expected_energy_mwh(s),
        "peak": max(sc.half_hourly.peak_mw() for sc in s.scenarios),
        "minute": s.has_minute_series and len(expected_series(s, "minute")),
    }
engine = sorted(
    m for m in sys.modules if m.startswith("gridq.") and not m.startswith("gridq.contracts")
)
heavy = sorted(m for m in ("numpy", "pandas", "linopy", "highspy", "xarray") if m in sys.modules)
print(json.dumps({"results": out, "engine_modules": engine, "heavy": heavy}))
"""


def _run_consumer(paths: list[Path]) -> dict[str, Any]:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    res = subprocess.run(
        [sys.executable, "-c", _CONSUMER, *map(str, paths)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    out: dict[str, Any] = json.loads(res.stdout)
    return out


def test_a_consumer_runs_on_the_fixtures_without_the_engine() -> None:
    """Fresh interpreter, contract package only: no GridQ engine module, no solver, no pandas."""
    out = _run_consumer(IC2_FIXTURES)
    assert out["engine_modules"] == [], out["engine_modules"]
    assert out["heavy"] == [], out["heavy"]
    for path in IC2_FIXTURES:
        r = out["results"][path.name]
        assert r["hash"] == GOLDEN_HASHES[path.name]
        assert r["n"] > 0 and r["energy"] > 0


# ---------------------------------------------------------------- real-data fixtures (git-ignored)


def test_real_fixture_folder_is_git_ignored_except_its_readme_and_manifest() -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git not available")
    root = Path(__file__).resolve().parents[2]

    def ignored(rel: str) -> bool:
        r = subprocess.run([git, "check-ignore", "-q", rel], cwd=root, check=False)
        return r.returncode == 0

    base = "tests/contracts/fixtures/real/"
    for name in [*_ic2_real.FILES.values(), *_ic2_real.SETS.values(), "retrieval.json"]:
        assert ignored(base + name), name
    for name in ("README.md", "MANIFEST.json", ".gitignore"):
        assert not ignored(base + name), name


def test_manifest_records_provenance_and_the_gate_without_data() -> None:
    assert "Eyes-only" in MANIFEST["gate"]
    for role, r in MANIFEST["responses"].items():
        assert r["url"].startswith("https://") and "apikey" not in r["url"].lower(), role
        assert r["sha256"].startswith("sha256:") and r["retrieved_at"].endswith("Z")
        assert r["http_status"] == 200
    licences = {name: s["licence"] for name, s in MANIFEST["sets"].items()}
    assert licences == {"ukpn": "CC BY 4.0", "npg": "Northern Powergrid Open Data Licence v1.0"}
    assert all(
        set(s)
        <= {"file", "file_sha256", "content_hash", "scenarios", "weights", "licence", "attribution"}
        for s in MANIFEST["sets"].values()
    )


@needs_real
@pytest.mark.parametrize("name", sorted(MANIFEST["sets"]))
def test_real_fixture_matches_its_manifest_and_validates(name: str) -> None:
    entry = MANIFEST["sets"][name]
    raw = (REAL / entry["file"]).read_bytes()
    assert "sha256:" + hashlib.sha256(raw).hexdigest() == entry["file_sha256"]
    obj = CurtailmentScenarioSet.model_validate_json(raw)
    assert obj.content_hash() == entry["content_hash"]
    assert obj.evidence_class is EvidenceClass.REPLAYED_REAL_DATA
    assert [f"{s.weight.numerator}/{s.weight.denominator}" for s in obj.scenarios] == entry[
        "weights"
    ]
    assert obj.has_minute_series
    for src in obj.provenance.sources:  # type: ignore[union-attr]
        assert src.distribution_gate != "" and "Eyes-only" in str(src.distribution_gate)
        assert src.publisher_role.value == "dno"


@needs_real
def test_real_fixtures_rebuild_from_the_recorded_responses() -> None:
    """The derivation rule is reproducible: recorded bytes -> the same set, the same hash."""
    for name, data in _ic2_real.build_all().items():
        built = CurtailmentScenarioSet.model_validate_json(json.dumps(data))
        assert built.content_hash() == MANIFEST["sets"][name]["content_hash"], name


@needs_real
def test_real_weights_follow_the_stated_rule() -> None:
    rec = json.loads(_ic2_real.RETRIEVAL.read_text(encoding="utf-8"))
    years = json.loads((REAL / rec["ukpn_years"]["file"]).read_bytes())["results"]
    ukpn = CurtailmentScenarioSet.model_validate_json((REAL / _ic2_real.SETS["ukpn"]).read_bytes())
    assert [s.scenario_id for s in ukpn.scenarios] == [
        f"y{r['year(start_time_utc)']}" for r in years
    ]
    assert {s.weight.as_fraction() for s in ukpn.scenarios} == {Fraction(1, len(years))}
    npg_rows = json.loads((REAL / rec["npg_events"]["file"]).read_bytes())
    npg = CurtailmentScenarioSet.model_validate_json((REAL / _ic2_real.SETS["npg"]).read_bytes())
    assert len(npg.scenarios) == len(npg_rows)
    assert {s.weight.as_fraction() for s in npg.scenarios} == {Fraction(1, len(npg_rows))}


@needs_real
def test_a_consumer_runs_on_the_real_fixtures_without_the_engine() -> None:
    paths = [REAL / s["file"] for s in MANIFEST["sets"].values()]
    out = _run_consumer(paths)
    assert out["engine_modules"] == [] and out["heavy"] == []
    for s in MANIFEST["sets"].values():
        assert out["results"][s["file"]]["hash"] == s["content_hash"]


def test_contract_models_reference_ic4_concepts() -> None:
    # evidence class reuses IC-4: the publisher role is IC-4's IssuerRole (Neura cannot be a
    # publisher) and registration is an IC-4 EvidenceRef.
    from gridq.contracts.evidence import EvidenceRef, IssuerRole

    f = curtailment.PublishedSource.model_fields
    assert f["publisher_role"].annotation is IssuerRole
    assert EvidenceRef in typing.get_args(f["registered_evidence"].annotation)
    assert issubclass(curtailment.PublishedSource, BaseModel)
