"""IC-9 compute turn-down declaration v1.0.0 conformance (roadmap WP-SITE-07, B8-01).

Acceptance:
1. Contract version is tagged and conformance tests pass (this file, the annotated git tag).
2. With no declared envelope, IT load is untouched in every model path: proved against the load
   model in ``tests/test_datacentre_ic9_untouched.py``.
3. The API specification is published: the JSON Schemas, and a human-readable specification
   generated from them and from the module, checked here and by ``python -m gridq.contracts
   --check``.

Plus the guards the design rests on (D122 to D127): the availability template compiles
onto UTC half-hours exactly across both clock changes, and the load model's naive-local reader
follows PEP 495 ``fold=0``; envelopes combine only through `stack_at` (merit order, exclusion
groups, a concurrent cap and a retained IT floor: no double-booking as arithmetic, D57); a call is
checked over its whole event against the shared budget of its SLA class; the SLA class's workload
action bounds whether energy returns, and the rebound follows it; a price is structured, never
zero, and a fixture's price is a labelled placeholder; no ``None`` anywhere (D45). Then mutation
checks proving each of those guards is load-bearing.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from functools import cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import _ic9_fixtures
import pytest
from _contracts_support import (
    FIXTURES_IC9,
    GOLDEN_HASHES,
    GOLDEN_HASHES_IC9,
    IC9_FIXTURES,
    load_ic9,
    run_mutant,
)
from pydantic import ValidationError

from gridq.contracts import __main__ as cli
from gridq.contracts import _specdoc, compute_turndown
from gridq.contracts.__main__ import FROZEN, render, schema_path
from gridq.contracts._base import NotApplicable, Unknown
from gridq.contracts.compute_turndown import (
    CONTRACT_VERSION,
    GIT_TAG,
    SUPPORTED_VERSIONS,
    CallReason,
    ComputeTurndownDeclaration,
    NotInForce,
    Offered,
    PastCall,
    ProposedCall,
    ResolvedTimeline,
    TimeBase,
    Unavailable,
    active_at,
    availability_cost_pence,
    call_budget_used,
    call_cost_pence,
    check_call,
    declaration_ref,
    declared_at_local,
    deliverable_against,
    offered_at,
    pence_to_pounds,
    price_basis_label,
    resolve,
    resolve_sla_evidence,
    resolved_hash_of,
    stack_at,
    timeline_matches,
    timeline_state_at,
    turn_down_kw,
)

CAMPUS = "ic9_declaration_gpu_campus.synthetic.json"
SUCCESSOR = "ic9_declaration_gpu_campus_successor.synthetic.json"
RELATIVE = "ic9_declaration_relative_curve.synthetic.json"
SPRING = "ic9_declaration_clock_change_spring.synthetic.json"
AUTUMN = "ic9_declaration_clock_change_autumn.synthetic.json"
TIMELINE = "ic9_timeline_clock_change_spring.synthetic.json"
DECLARATIONS = (CAMPUS, SUCCESSOR, RELATIVE, SPRING, AUTUMN)
CHECKPOINT, POWER_CAP, MIGRATE = (
    _ic9_fixtures.CHECKPOINT,
    _ic9_fixtures.POWER_CAP,
    _ic9_fixtures.MIGRATE,
)
BATCH, MOVABLE = _ic9_fixtures.BATCH, _ic9_fixtures.MOVABLE
STEMS = ("ic9_compute_turndown_declaration", "ic9_resolved_timeline")
LONDON = ZoneInfo("Europe/London")
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


# ------------------------------------------------------------------ helpers
# Fixtures are read inside tests, never at import: a mutant that breaks loading must turn its test
# red, not fail collection.


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate(data: dict[str, Any]) -> ComputeTurndownDeclaration:
    return ComputeTurndownDeclaration.model_validate_json(json.dumps(data))


@cache
def _fixture(name: str) -> ComputeTurndownDeclaration:
    return ComputeTurndownDeclaration.model_validate_json(
        (FIXTURES_IC9 / name).read_text(encoding="utf-8")
    )


def _sealed(data: dict[str, Any]) -> dict[str, Any]:
    """Re-seal ``resolved_hash`` after an edit, so only the guard under test can refuse. An edit
    that breaks the time base itself cannot be sealed; it is validated as it stands."""
    try:
        data["resolved_hash"] = resolved_hash_of(data)
    except ValidationError:
        pass
    return data


def _refused(data: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        _validate(data)


def _walk(node: Any) -> Any:
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _envelope(data: dict[str, Any], envelope_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(e for e in data["envelopes"] if e["envelope_id"] == envelope_id)
    return found


def _segments(timeline: ResolvedTimeline) -> list[tuple[str, str, str, Any]]:
    steps = list(timeline.steps)
    ends = [s.start for s in steps[1:]] + [timeline.valid_until]
    return [(_iso(s.start), _iso(e), s.origin, s.state) for s, e in zip(steps, ends, strict=True)]


def _as_registered(data: dict[str, Any], *, indicative: bool) -> dict[str, Any]:
    """The campus fixture relabelled as a registered record (ids without the synthetic prefix),
    with its prices left as placeholders or restated as indicative."""
    strip = lambda s: s.removeprefix("SYNTHETIC-")  # noqa: E731
    data["record_class"] = "registered"
    data["site_id"], data["operator_id"] = strip(data["site_id"]), strip(data["operator_id"])
    for c in data["sla_classes"]:
        c["class_id"], c["operator_label"] = strip(c["class_id"]), strip(c["operator_label"])
    for e in data["envelopes"]:
        e["sla_class_id"] = strip(e["sla_class_id"])
        if indicative and e["price"]["kind"] == "price_offer":
            e["price"]["price_basis"] = {"kind": "indicative", "stated_on": "2026-12-01"}
    return _sealed(data)


# ------------------------------------------------------------------ 1. version tagged, conformance


def test_the_contract_version_is_tagged() -> None:
    assert CONTRACT_VERSION == "1.0.0"
    assert SUPPORTED_VERSIONS == ("1.0.0",) and SUPPORTED_VERSIONS[-1] == CONTRACT_VERSION
    assert GIT_TAG == f"contracts/ic9-compute-turndown/v{CONTRACT_VERSION}"
    assert compute_turndown.CONTRACT_ID == "IC-9"
    assert "SUPPORTED_VERSIONS" in compute_turndown.__all__


def test_the_git_tag_is_annotated_and_carries_the_frozen_schemas_and_spec() -> None:
    """Acceptance 1 against git itself. A checkout without the tag (a tagless CI clone) skips."""

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False
        )

    try:
        kind = git("cat-file", "-t", f"refs/tags/{GIT_TAG}")
    except FileNotFoundError:
        pytest.skip("git is not installed")
    if kind.returncode != 0:
        pytest.skip(f"{GIT_TAG} is not in this checkout")
    assert kind.stdout.strip() == "tag", "the tag must be annotated, not lightweight"
    paths = [schema_path(stem, CONTRACT_VERSION) for stem in STEMS]
    paths.append(_specdoc.spec_path(STEMS[0], CONTRACT_VERSION))
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        at_tag = git("show", f"{GIT_TAG}:{rel}")
        assert at_tag.returncode == 0, rel
        assert at_tag.stdout == path.read_text(encoding="utf-8"), rel


@pytest.mark.parametrize(
    "stem,model",
    [(STEMS[0], ComputeTurndownDeclaration), (STEMS[1], ResolvedTimeline)],
)
def test_the_frozen_schema_is_committed_and_matches_the_model(stem: str, model: type) -> None:
    assert (stem, model, CONTRACT_VERSION) in FROZEN
    path = schema_path(stem, CONTRACT_VERSION)
    assert path.read_text(encoding="utf-8") == render(model)


@pytest.mark.parametrize("path", IC9_FIXTURES, ids=lambda p: p.name)
def test_every_fixture_validates_at_1_0_0_with_its_pinned_hash(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    model = ResolvedTimeline if "_timeline_" in path.name else ComputeTurndownDeclaration
    obj = model.model_validate_json(text)
    assert (obj.contract, obj.contract_version) == ("IC-9", CONTRACT_VERSION)
    assert obj.content_hash() == GOLDEN_HASHES_IC9[path.name]
    again = model.model_validate_json(obj.model_dump_json(indent=2))
    assert again == obj and again.content_hash() == obj.content_hash()


@pytest.mark.parametrize("name", [*DECLARATIONS, TIMELINE])
@pytest.mark.parametrize(
    "field,value",
    [
        ("contract_version", "1.0.1"),
        ("contract_version", "1.1.0"),
        ("contract_version", "2.0.0"),
        ("contract", "IC-3"),
        ("contract", "IC-11"),
    ],
)
def test_another_version_or_contract_is_refused(name: str, field: str, value: str) -> None:
    data = load_ic9(name)
    data[field] = value
    model = ResolvedTimeline if name == TIMELINE else ComputeTurndownDeclaration
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(data))


def test_the_committed_fixtures_equal_a_fresh_build() -> None:
    assert sorted(p.name for p in _ic9_fixtures.BUILDERS) == sorted(GOLDEN_HASHES_IC9)
    assert sorted(p.name for p in IC9_FIXTURES) == sorted(GOLDEN_HASHES_IC9)
    for path, build in _ic9_fixtures.BUILDERS.items():
        assert path.read_text(encoding="utf-8") == _ic9_fixtures.render(build()), path.name


# ------------------------------------------------------------------ 3. the published specification


def _spec_text() -> str:
    return _specdoc.spec_path(STEMS[0], CONTRACT_VERSION).read_text(encoding="utf-8")


def test_the_committed_specification_is_a_fresh_render() -> None:
    # Every generated spec renders here (IC-14 publishes one too); this test owns IC-9's.
    rendered = dict(cli.render_specs(cli.SCHEMA_DIR, _specdoc.SPEC_DIR))
    path = _specdoc.spec_path(STEMS[0], CONTRACT_VERSION)
    assert path.read_text(encoding="utf-8") == rendered[path]


def test_the_specification_names_every_schema_object_and_field() -> None:
    doc = _spec_text()
    for stem in STEMS:
        path = schema_path(stem, CONTRACT_VERSION)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert f"`schemas/{path.name}` (sha256 `{digest}`)" in doc
        schema = json.loads(path.read_text(encoding="utf-8"))
        objects = {schema["title"]: schema, **schema["$defs"]}
        for name, node in objects.items():
            section = doc.split(f"### `{name}`\n", 1)[1].split("\n### ", 1)[0]
            for field in node.get("properties", {}):
                assert f"| `{field}` |" in section, (name, field)
            for value in node.get("enum", ()):
                assert f"`{value}`" in section, (name, value)


def test_the_specification_states_identity_readers_and_the_rules_a_reader_must_know() -> None:
    doc = _spec_text()
    assert doc.startswith("# IC-9 specification, version 1.0.0\n")
    assert f"| git tag | `{GIT_TAG}` |" in doc and "Do not edit" in doc
    for name in compute_turndown.__all__:
        if callable(getattr(compute_turndown, name)) and name[0].islower():
            assert f"### `{name}(" in doc, name
    # The two rules a consumer cannot infer from the schema: how a naive local half-hour is read,
    # and that every declared kW is conditional (never the priced floor).
    assert "fold=0" in doc and "CONDITIONAL" in doc


def test_check_fails_when_the_specification_drifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    specs = tmp_path / "specs"
    specs.mkdir()
    for other in _specdoc.SPEC_DIR.glob("*.md"):  # the other contracts' specs, unchanged
        (specs / other.name).write_text(other.read_text(encoding="utf-8"), encoding="utf-8")
    name, committed = _specdoc.spec_path(STEMS[0], CONTRACT_VERSION).name, _spec_text()
    (specs / name).write_text(committed.replace("CONDITIONAL", "FIRM", 1), encoding="utf-8")
    monkeypatch.setattr(_specdoc, "SPEC_DIR", specs)
    assert cli.main(["--check"]) == 1
    assert f"DRIFT: {name} is not the specification its contract renders" in capsys.readouterr().out
    (specs / name).write_text(committed, encoding="utf-8")
    assert cli.main(["--check"]) == 0


# ------------------------------------------------------------------ consumers without the producer


_CONSUMER = """
import json, pathlib, sys
from datetime import UTC, date, datetime, timedelta
from gridq.contracts.compute_turndown import (
    ComputeTurndownDeclaration, PastCall, ProposedCall, ResolvedTimeline, active_at,
    call_cost_pence, check_call, declared_at_local, offered_at, stack_at, timeline_matches,
    timeline_state_at,
)
fx = pathlib.Path(sys.argv[1])
read = lambda p: pathlib.Path(p).read_text(encoding="utf-8")
decl = {
    p.name: ComputeTurndownDeclaration.model_validate_json(read(p))
    for p in sorted(fx.glob("ic9_declaration_*.synthetic.json"))
}
out = {"hashes": {n: d.content_hash() for n, d in decl.items()}}
# The edge: a compiled timeline trusted by hash, read with no clock logic of its own.
spring = decl["ic9_declaration_clock_change_spring.synthetic.json"]
timeline = ResolvedTimeline.model_validate_json(
    read(fx / "ic9_timeline_clock_change_spring.synthetic.json")
)
out["timeline_matches"] = timeline_matches(spring, timeline)
half = [timeline.valid_from + timedelta(minutes=30 * i) for i in range(timeline.n_periods)]
out["edge_agrees"] = all(timeline_state_at(timeline, t) == offered_at(spring, t) for t in half)
# The load model's naive-local reader on the spring-forward day.
out["spring_local"] = [
    declared_at_local(spring, date(2027, 3, 28), h).instant.isoformat() for h in (2, 3, 4, 5)
]
# The sizer and dispatch: which declaration is in force, what stacks, whether a call fits.
pair = [
    decl["ic9_declaration_gpu_campus.synthetic.json"],
    decl["ic9_declaration_gpu_campus_successor.synthetic.json"],
]
out["active"] = {
    t: getattr(active_at(pair, datetime.fromisoformat(t)), "declaration_id", "none")
    for t in ("2027-06-30T23:30:00+00:00", "2027-07-01T00:00:00+00:00",
              "2028-01-01T00:00:00+00:00")
}
night = datetime(2027, 1, 5, 1, tzinfo=UTC)
out["stack"] = [
    [layer.envelope_id, layer.kw] for layer in stack_at(pair[0], night, 30, 20000).layers
]
call = ProposedCall(
    kind="proposed_call", envelope_ids=("env-checkpoint",), issued_at=night - timedelta(hours=1),
    start=night, duration_min=60, turn_down_kw=3000, it_load_kw=6000,
)
past = PastCall(
    kind="past_call", envelope_ids=("env-power-cap",), start=night - timedelta(hours=2),
    duration_min=60, turn_down_kw=1000,
)
out["check"] = [r.value for r in check_call(pair[0], call, [past]).reasons]
out["cost"] = str(call_cost_pence(pair[0].envelopes[0].price, reduction_kw=3000, duration_min=60))
out["engine_modules"] = sorted(
    m for m in sys.modules if m.startswith("gridq.") and not m.startswith("gridq.contracts")
)
out["heavy"] = sorted(
    m for m in ("numpy", "pandas", "linopy", "highspy", "xarray", "scipy") if m in sys.modules
)
print(json.dumps(out))
"""


def test_a_consumer_runs_on_the_fixtures_without_the_producer() -> None:
    """Fresh interpreter, contract package only: no load model, sizer, engine or solver module is
    imported, and no operator adapter exists."""
    res = subprocess.run(
        [sys.executable, "-c", _CONSUMER, str(FIXTURES_IC9)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        check=True,
    )
    out = json.loads(res.stdout)
    assert out["engine_modules"] == [] and out["heavy"] == [], out
    assert out["hashes"] == {n: GOLDEN_HASHES_IC9[n] for n in DECLARATIONS}
    assert out["timeline_matches"] is True and out["edge_agrees"] is True
    assert out["spring_local"] == [
        "2027-03-28T01:00:00+00:00",
        "2027-03-28T01:30:00+00:00",
        "2027-03-28T01:00:00+00:00",
        "2027-03-28T01:30:00+00:00",
    ]
    assert out["active"] == {
        "2027-06-30T23:30:00+00:00": "ic9-synthetic-gpu-campus-2027",
        "2027-07-01T00:00:00+00:00": "ic9-synthetic-gpu-campus-2027-h2",
        "2028-01-01T00:00:00+00:00": "none",
    }
    assert out["stack"] == [[CHECKPOINT, 4000], [MIGRATE, 1000]]
    # The power-cap call ended an hour before: inside the batch class's four-hour rest.
    assert out["check"] == ["rest_too_short"]
    assert out["cost"] == "3000"


# ------------------------------------------------------------------ the fixtures are synthetic


def test_every_fixture_is_labelled_synthetic_and_invents_nothing() -> None:
    placeholder = _ic9_fixtures._placeholder_hash
    pinned = {*GOLDEN_HASHES.values(), *GOLDEN_HASHES_IC9.values()}
    for name in DECLARATIONS:
        d = _fixture(name)
        assert d.record_class == "synthetic_fixture"
        assert d.site_id.startswith("SYNTHETIC-") and d.operator_id.startswith("SYNTHETIC-")
        for c in d.sla_classes:
            assert c.class_id.startswith("SYNTHETIC-")
            assert c.operator_label.startswith("SYNTHETIC-")
        # Every stated price is a placeholder, labelled as one, never an offer.
        for e in d.envelopes:
            assert price_basis_label(e.price) in (
                "SYNTHETIC PLACEHOLDER, not an offer",
                f"price unknown: {getattr(e.price, 'reason', '')}",
            ), (name, e.envelope_id)
        # Evidence hashes are placeholders, never the hash of a real document or a pinned fixture.
        for node in _walk(load_ic9(name)):
            if isinstance(node, dict) and node.get("kind") == "evidence_ref":
                assert node["evidence_hash"] not in pinned, name
    assert _fixture(CAMPUS).sla_classes[0].sla_evidence.evidence_hash == placeholder(  # type: ignore[union-attr]
        "service-level agreement, batch training class"
    )


def test_the_placeholder_hash_is_the_ic11_pattern() -> None:
    text = "SYNTHETIC PLACEHOLDER: x"
    assert (
        _ic9_fixtures._placeholder_hash("x")
        == "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    )


@pytest.mark.parametrize("path", IC9_FIXTURES, ids=lambda p: p.name)
def test_no_fixture_holds_a_null_anywhere(path: Path) -> None:
    assert None not in list(_walk(json.loads(path.read_text(encoding="utf-8"))))


@pytest.mark.parametrize("field", list(ComputeTurndownDeclaration.model_fields))
def test_null_is_refused_for_every_declaration_field(field: str) -> None:
    data = load_ic9(CAMPUS)
    data[field] = None
    with pytest.raises(ValidationError):
        _validate(data)


def test_the_models_are_final_and_frozen() -> None:
    for model in (ComputeTurndownDeclaration, ResolvedTimeline):
        with pytest.raises(TypeError, match="final"):
            type("Sub", (model,), {})
    d = _fixture(CAMPUS)
    with pytest.raises(ValidationError):
        d.site_id = "SYNTHETIC-OTHER"  # type: ignore[misc]


def test_the_contract_carries_no_firmness_and_no_gate() -> None:
    """B8-03 and D57: every IC-9 kW is CONDITIONAL to a consumer, so the contract states no
    firmness, rank or stream, and nothing in it names a revenue stream to gate."""
    schema = json.loads(schema_path(STEMS[0], CONTRACT_VERSION).read_text(encoding="utf-8"))
    fields = {
        f.lower()
        for node in (schema, *schema["$defs"].values())
        for f in node.get("properties", {})
    }
    for word in ("firm", "floor_price", "priced", "rank", "stream", "eligib", "gate"):
        assert not [f for f in fields if word in f], word
    assert "firm_offer" in json.dumps(schema)  # a price basis, not a firmness: kept by name


def test_a_synthetic_label_is_exactly_the_record_class() -> None:
    data = load_ic9(CAMPUS)
    data["site_id"] = "DC-GPU-CAMPUS"
    _refused(_sealed(data), "site_id starts with SYNTHETIC- exactly when")
    data = load_ic9(CAMPUS)
    data["record_class"] = "registered"
    _refused(_sealed(data), "starts with SYNTHETIC- exactly when the record is synthetic")


def test_a_placeholder_price_is_exactly_a_synthetic_record() -> None:
    _refused(_as_registered(load_ic9(CAMPUS), indicative=False), "synthetic placeholder exactly")
    registered = _validate(_as_registered(load_ic9(CAMPUS), indicative=True))
    assert registered.record_class == "registered"
    assert price_basis_label(registered.envelopes[0].price) == (
        "indicative, stated on 2026-12-01; not a binding offer"
    )


# ------------------------------------------------------------------ the time base


def _oracle(data: dict[str, Any], instant: datetime) -> tuple[str, Any]:
    """(origin, state) at ``instant``, read straight off the JSON (independent of the module): an
    override if one holds it, else the cell of the local season, weekday and minute."""
    tpl = data["availability"]
    for o in tpl["overrides"]:
        if _at(o["start"]) <= instant < _at(o["end"]):
            return "override", o["state"]
    local = instant.astimezone(LONDON)
    minute = local.hour * 60 + local.minute
    season = next(s["season_id"] for s in tpl["seasons"] if local.month in s["months"])
    group = next(
        g["day_group_id"] for g in tpl["day_groups"] if local.isoweekday() in g["iso_weekdays"]
    )
    band = next(b["band_id"] for b in tpl["bands"] if b["start_minute"] <= minute < b["end_minute"])
    return "template", next(
        c["state"]
        for c in tpl["cells"]
        if (c["season_id"], c["day_group_id"], c["band_id"]) == (season, group, band)
    )


@pytest.mark.parametrize("name", DECLARATIONS)
def test_the_resolver_matches_an_independent_oracle_at_every_half_hour(name: str) -> None:
    """Every UTC half-hour of the validity (a year, half a year, or a clock-change week), both
    clock changes included, against a reading that shares no code with `resolve`."""
    data = load_ic9(name)
    timeline = resolve(TimeBase.model_validate_json(json.dumps(data)))
    steps = list(timeline.steps)
    k = 0
    for i in range(timeline.n_periods):
        instant = timeline.valid_from + i * timedelta(minutes=30)
        while k + 1 < len(steps) and steps[k + 1].start <= instant:
            k += 1
        origin, state = _oracle(data, instant)
        got = (steps[k].origin, steps[k].state.model_dump(mode="json"))
        assert got == (origin, state), (name, _iso(instant))
    assert timeline.content_hash() == data["resolved_hash"]


def _offered_spans(name: str) -> list[tuple[str, str]]:
    return [
        (a, b)
        for a, b, _, state in _segments(resolve(_fixture(name)))
        if isinstance(state, Offered)
    ]


def test_the_23_hour_day_has_a_six_hour_night_in_utc() -> None:
    assert _offered_spans(SPRING) == [
        ("2027-03-25T00:00:00Z", "2027-03-25T07:00:00Z"),
        ("2027-03-26T00:00:00Z", "2027-03-26T07:00:00Z"),
        ("2027-03-27T00:00:00Z", "2027-03-27T07:00:00Z"),
        ("2027-03-28T00:00:00Z", "2027-03-28T06:00:00Z"),
        ("2027-03-28T23:00:00Z", "2027-03-29T06:00:00Z"),
        ("2027-03-29T23:00:00Z", "2027-03-30T06:00:00Z"),
        ("2027-03-30T23:00:00Z", "2027-03-31T06:00:00Z"),
        ("2027-03-31T23:00:00Z", "2027-04-01T00:00:00Z"),
    ]
    assert resolve(_fixture(SPRING)).n_periods == 7 * 48


def test_the_25_hour_day_has_an_eight_hour_night_in_utc() -> None:
    spans = _offered_spans(AUTUMN)
    assert ("2027-10-30T23:00:00Z", "2027-10-31T07:00:00Z") in spans
    assert spans[0] == ("2027-10-28T00:00:00Z", "2027-10-28T06:00:00Z")  # BST: 01:00 to 07:00


def _fold0(local_date: date, half_hour: int) -> datetime:
    """Independent reading of PEP 495 fold=0: try the offsets in force either side of the local
    midnight and keep the first (earliest-offset-first) that round-trips, else (spring gap) the
    pre-transition offset applied to the naive time."""
    naive = datetime(local_date.year, local_date.month, local_date.day) + timedelta(
        minutes=30 * half_hour
    )
    before = datetime(local_date.year, local_date.month, local_date.day, tzinfo=LONDON)
    for offset in (before.utcoffset(), timedelta(hours=1), timedelta(0)):
        assert offset is not None
        candidate = (naive - offset).replace(tzinfo=UTC)
        if candidate.astimezone(LONDON).replace(tzinfo=None) == naive:
            return candidate
    offset = before.utcoffset()
    assert offset is not None
    return (naive - offset).replace(tzinfo=UTC)


@pytest.mark.parametrize(
    "name,day", [(SPRING, date(2027, 3, 28)), (AUTUMN, date(2027, 10, 31))], ids=["23h", "25h"]
)
def test_the_naive_local_reader_matches_an_oracle_across_the_clock_change(
    name: str, day: date
) -> None:
    d = _fixture(name)
    for local_day in (day - timedelta(days=1), day, day + timedelta(days=1)):
        for h in range(48):
            answer = declared_at_local(d, local_day, h)
            instant = _fold0(local_day, h)
            assert answer.instant == instant, (local_day, h)
            assert not isinstance(answer, NotInForce)
            assert (answer.origin, answer.state.model_dump(mode="json")) == _oracle(
                load_ic9(name), instant
            ), (local_day, h)


def test_the_clock_change_hours_read_as_the_specification_says() -> None:
    spring, autumn = _fixture(SPRING), _fixture(AUTUMN)
    # Spring: naive 01:00 and 01:30 do not exist; they read UTC 01:00 and 01:30, as 02:00 and
    # 02:30 (BST) do. So the gap hour reads the hour after it, not "not in force".
    read = [declared_at_local(spring, date(2027, 3, 28), h).instant for h in (2, 3, 4, 5)]
    assert [_iso(t) for t in read] == [
        "2027-03-28T01:00:00Z",
        "2027-03-28T01:30:00Z",
        "2027-03-28T01:00:00Z",
        "2027-03-28T01:30:00Z",
    ]
    # Autumn: naive 01:00 and 01:30 occur twice; the first (BST) occurrence is read.
    read = [declared_at_local(autumn, date(2027, 10, 31), h).instant for h in (2, 3, 4)]
    assert [_iso(t) for t in read] == [
        "2027-10-31T00:00:00Z",
        "2027-10-31T00:30:00Z",
        "2027-10-31T02:00:00Z",
    ]


def test_the_naive_local_reader_is_not_in_force_outside_the_validity() -> None:
    d = _fixture(SPRING)
    assert isinstance(declared_at_local(d, date(2027, 3, 24), 47), NotInForce)
    assert not isinstance(declared_at_local(d, date(2027, 3, 25), 0), NotInForce)
    assert not isinstance(declared_at_local(d, date(2027, 4, 1), 1), NotInForce)  # 23:30 UTC
    assert isinstance(declared_at_local(d, date(2027, 4, 1), 2), NotInForce)  # 00:00 UTC
    with pytest.raises(ValueError, match="0 to 47"):
        declared_at_local(d, date(2027, 3, 26), 48)


def test_the_edge_reads_the_committed_timeline_and_trusts_it_by_hash() -> None:
    d = _fixture(SPRING)
    timeline = ResolvedTimeline.model_validate_json(
        (FIXTURES_IC9 / TIMELINE).read_text(encoding="utf-8")
    )
    assert timeline_matches(d, timeline) and timeline == resolve(d)
    for i in range(timeline.n_periods):
        t = timeline.valid_from + i * timedelta(minutes=30)
        assert timeline_state_at(timeline, t) == offered_at(d, t)
    data = json.loads((FIXTURES_IC9 / TIMELINE).read_text(encoding="utf-8"))
    data["steps"][1]["start"] = "2027-03-25T07:30:00Z"
    assert not timeline_matches(d, ResolvedTimeline.model_validate_json(json.dumps(data)))


@pytest.mark.parametrize(
    "edit,match",
    [
        (lambda d: d["steps"][0].__setitem__("start", "2027-03-25T00:30:00Z"), "starts at"),
        (lambda d: d["steps"].insert(2, d["steps"][1]), "strictly increase"),
        (lambda d: d["steps"][2].__setitem__("state", d["steps"][1]["state"]), "one step"),
        (lambda d: d.__setitem__("n_periods", 335), "n_periods"),
        (lambda d: d["steps"][-1].__setitem__("start", "2027-04-01T00:00:00Z"), "after the"),
        (lambda d: d["steps"][1].__setitem__("start", "2027-03-25T07:10:00Z"), "grid"),
    ],
    ids=["first-step", "repeat", "adjacent-same", "n-periods", "past-end", "off-grid"],
)
def test_a_malformed_timeline_is_refused(edit: Callable[[Any], None], match: str) -> None:
    data = json.loads((FIXTURES_IC9 / TIMELINE).read_text(encoding="utf-8"))
    edit(data)
    with pytest.raises(ValidationError, match=match):
        ResolvedTimeline.model_validate_json(json.dumps(data))


def test_the_resolved_hash_must_be_the_compiled_timeline() -> None:
    data = load_ic9(CAMPUS)
    data["resolved_hash"] = "sha256:" + "4" * 64
    _refused(data, "resolved_hash is not the hash")


def _cells(data: dict[str, Any]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = data["availability"]["cells"]
    return cells


def _same_weekend_as_weekday(d: dict[str, Any]) -> None:
    for c in _cells(d):
        if c["day_group_id"] == "weekend":
            twin = next(
                x
                for x in _cells(d)
                if (x["season_id"], x["day_group_id"], x["band_id"])
                == (c["season_id"], "weekday", c["band_id"])
            )
            c["state"] = twin["state"]


_TEMPLATE_EDITS: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    (
        "months",
        lambda d: d["availability"]["seasons"][1].__setitem__("months", [4, 5, 6, 7, 8]),
        "partition the twelve months",
    ),
    (
        "weekdays",
        lambda d: d["availability"]["day_groups"][1].__setitem__("iso_weekdays", [6]),
        "partition the ISO week",
    ),
    (
        "band-gap",
        lambda d: d["availability"]["bands"][1].__setitem__("start_minute", 450),
        "partition the day",
    ),
    ("missing-cell", lambda d: _cells(d).pop(), "exactly one state per"),
    ("redundant-group", _same_weekend_as_weekday, "day groups weekday and weekend"),
    (
        "unsorted-offer",
        lambda d: _cells(d)[0]["state"]["envelope_ids"].reverse(),
        "envelope_ids must be sorted",
    ),
    (
        "undeclared-offer",
        lambda d: _cells(d)[0]["state"].__setitem__("envelope_ids", ["env-ghost"]),
        "offers undeclared envelope",
    ),
    (
        "override-overlap",
        lambda d: d["availability"]["overrides"][1].update(
            start="2027-07-06T14:00:00Z", end="2027-07-07T00:00:00Z"
        ),
        "overlap",
    ),
    (
        "override-outside",
        lambda d: d["availability"]["overrides"][1].__setitem__("end", "2028-01-02T00:00:00Z"),
        "lies outside the validity",
    ),
    ("override-unsorted", lambda d: d["availability"]["overrides"].reverse(), "sorted by start"),
    (
        "override-adjacent-same",
        lambda d: d["availability"]["overrides"].append(
            {
                **d["availability"]["overrides"][1],
                "override_id": "ovr-blackout-2027-12-27",
                "start": "2027-12-27T00:00:00Z",
                "end": "2027-12-28T00:00:00Z",
            }
        ),
        "adjacent with the same state",
    ),
    (
        "override-off-grid",
        lambda d: d["availability"]["overrides"][0].__setitem__("start", "2027-07-06T07:10:00Z"),
        "ovr-dated-offer-2027-07-06 must fall on the 30-minute UTC grid",
    ),
    (
        "validity-off-grid",
        lambda d: d.__setitem__("valid_from", "2027-01-01T00:10:00Z"),
        "valid_from and valid_until must fall",
    ),
    (
        "validity-reversed",
        lambda d: d.__setitem__("valid_until", "2026-06-01T00:00:00Z"),
        "valid_until must be after valid_from",
    ),
]


@pytest.mark.parametrize(
    "edit,match", [e[1:] for e in _TEMPLATE_EDITS], ids=[e[0] for e in _TEMPLATE_EDITS]
)
def test_the_template_and_overrides_are_complete_canonical_and_on_the_grid(
    edit: Callable[[dict[str, Any]], None], match: str
) -> None:
    data = load_ic9(CAMPUS)
    edit(data)
    _refused(_sealed(data), match)


def test_a_redundant_band_is_refused() -> None:
    data = load_ic9(RELATIVE)
    _cells(data)[1]["state"] = _cells(data)[0]["state"]
    _refused(_sealed(data), "bands night and day carry the same states everywhere")


def test_an_override_replaces_the_cell_wholesale_and_reads_as_a_blackout() -> None:
    d = _fixture(CAMPUS)
    offer = offered_at(d, _at("2027-07-06T08:00:00Z"))  # a protected summer weekday daytime
    assert offer.origin == "override" and offer.state == Offered(
        kind="offered", envelope_ids=(MIGRATE,)
    )
    template = offered_at(d, _at("2027-07-06T06:00:00Z"))  # 07:00 BST, before the override
    assert template.origin == "template" and isinstance(template.state, Unavailable)
    blackout = offered_at(d, _at("2027-12-24T01:00:00Z"))  # a night that would offer everything
    assert blackout.origin == "override" and isinstance(blackout.state, Unavailable)


# ------------------------------------------------------------------ the envelope, class and price


def _edit(name: str, edit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    data = load_ic9(name)
    edit(data)
    return _sealed(data)


def _unknown(reason: str) -> dict[str, str]:
    return {"kind": "unknown", "reason": reason}


_GUARDS: list[tuple[str, str, Callable[[dict[str, Any]], None], str]] = [
    (
        "ramp",
        CAMPUS,
        lambda d: _envelope(d, CHECKPOINT)["response"].__setitem__("ramp_s", 1800),
        "ramp_s must finish inside the shortest hold",
    ),
    (
        "holds",
        CAMPUS,
        lambda d: _envelope(d, CHECKPOINT)["curve"]["points"][1].__setitem__("max_hold_min", 30),
        "max_hold_min must strictly increase",
    ),
    (
        "amounts",
        CAMPUS,
        lambda d: _envelope(d, CHECKPOINT)["curve"]["points"][1].__setitem__("turn_down_kw", 4000),
        "the amount must strictly decrease",
    ),
    (
        "relative-amounts",
        RELATIVE,
        lambda d: d["envelopes"][0]["curve"]["points"][1].__setitem__("reduction_bp", 3000),
        "the amount must strictly decrease",
    ),
    (
        "hold-cap",
        CAMPUS,
        lambda d: _envelope(d, CHECKPOINT)["curve"]["points"][2].__setitem__("max_hold_min", 1441),
        "less than or equal to 1440",
    ),
    (
        "zero-kw",
        CAMPUS,
        lambda d: _envelope(d, CHECKPOINT)["curve"]["points"][2].__setitem__("turn_down_kw", 0),
        "greater than or equal to 1",
    ),
    (
        "zero-price",
        CAMPUS,
        lambda d: _envelope(d, CHECKPOINT)["price"]["utilisation"].__setitem__("pence_per_mwh", 0),
        "greater than or equal to 1",
    ),
    (
        "price-all-unknown",
        CAMPUS,
        lambda d: _envelope(d, CHECKPOINT)["price"].update(
            availability=_unknown("x"), utilisation=_unknown("x"), per_event=_unknown("x")
        ),
        "every component is unknown",
    ),
    (
        "firm-offer-unknown",
        CAMPUS,
        lambda d: _envelope(d, CHECKPOINT)["price"].update(
            per_event=_unknown("x"),
            price_basis={
                "kind": "firm_offer",
                "evidence": _ic9_fixtures._evidence("price offer document"),
            },
        ),
        "a firm offer states every component",
    ),
    (
        "deferred-no-return",
        CAMPUS,
        lambda d: d["sla_classes"][0].__setitem__("energy_return", {"kind": "no_return"}),
        "deferred work returns",
    ),
    (
        "moved-returns",
        CAMPUS,
        lambda d: d["sla_classes"][1].__setitem__(
            "energy_return", {"kind": "returns_later", "return_window_min": 60}
        ),
        "work does not return at this site",
    ),
    (
        "rebound-no-return",
        CAMPUS,
        lambda d: _envelope(d, MIGRATE).__setitem__("rebound", _envelope(d, CHECKPOINT)["rebound"]),
        "returns no energy, so rebound is not_applicable",
    ),
    (
        "rebound-returns-na",
        CAMPUS,
        lambda d: _envelope(d, CHECKPOINT).__setitem__("rebound", _envelope(d, MIGRATE)["rebound"]),
        "returns energy, so rebound is a size or unknown",
    ),
    (
        "rebound-unknown-class",
        RELATIVE,
        lambda d: d["envelopes"][0].__setitem__(
            "rebound",
            {
                "kind": "rebound_size",
                "recovery_permille": 1000,
                "max_rebound_kw": _unknown("x"),
            },
        ),
        "is unknown, so rebound is unknown",
    ),
    (
        "dangling-class",
        CAMPUS,
        lambda d: _envelope(d, CHECKPOINT).__setitem__("sla_class_id", "SYNTHETIC-CLASS-NONE"),
        "names no declared class",
    ),
    (
        "unused-class",
        CAMPUS,
        lambda d: d["sla_classes"].insert(
            0, {**d["sla_classes"][0], "class_id": "SYNTHETIC-CLASS-AAA"}
        ),
        "named by no envelope",
    ),
    ("unsorted-classes", CAMPUS, lambda d: d["sla_classes"].reverse(), "sorted by class_id"),
    ("unsorted-envelopes", CAMPUS, lambda d: d["envelopes"].reverse(), "sorted by envelope_id"),
    (
        "merit-order",
        CAMPUS,
        lambda d: d["stacking"].__setitem__("merit_order", [CHECKPOINT, POWER_CAP]),
        "list every envelope exactly once",
    ),
    (
        "group-of-one",
        CAMPUS,
        lambda d: _envelope(d, POWER_CAP).__setitem__("exclusion_group", "other-pool"),
        "has one member",
    ),
    (
        "cap-cannot-bind",
        CAMPUS,
        lambda d: d["stacking"].__setitem__("max_concurrent_turn_down_kw", 5501),
        r"can ever stack to \(5500 kW\)",
    ),
    (
        "never-offered",
        CAMPUS,
        lambda d: (
            d["envelopes"].append({**_envelope(d, MIGRATE), "envelope_id": "env-zzz-spare"}),
            d["stacking"]["merit_order"].append("env-zzz-spare"),
        ),
        "offered by no cell or override",
    ),
]


@pytest.mark.parametrize("name,edit,match", [g[1:] for g in _GUARDS], ids=[g[0] for g in _GUARDS])
def test_each_envelope_class_and_price_guard_refuses(
    name: str, edit: Callable[[dict[str, Any]], None], match: str
) -> None:
    _refused(_edit(name, edit), match)


def test_slowed_work_may_return_or_not() -> None:
    for ret, rebound in (
        ({"kind": "no_return"}, {"kind": "not_applicable", "reason": "x"}),
        (
            {"kind": "returns_later", "return_window_min": _unknown("x")},
            _unknown("x"),
        ),
    ):
        data = load_ic9(RELATIVE)
        data["sla_classes"][0]["energy_return"] = ret
        data["envelopes"][0]["rebound"] = rebound
        assert _validate(_sealed(data)).sla_classes[0].workload_action == "slowed"


def test_a_cap_is_checked_only_when_every_curve_is_absolute() -> None:
    # A share of load has no fixed ceiling without one, so a relative curve leaves any cap stated.
    data = load_ic9(RELATIVE)
    data["envelopes"][0]["curve"]["cap_kw"] = {"kind": "not_applicable", "reason": "x"}
    data["stacking"]["max_concurrent_turn_down_kw"] = 50000
    assert _validate(_sealed(data)).stacking.max_concurrent_turn_down_kw == 50000


# ------------------------------------------------------------------ kW: one reader per question


def test_turn_down_reads_the_first_point_covering_the_duration_never_above_the_load() -> None:
    d = _fixture(CAMPUS)
    curve = next(e for e in d.envelopes if e.envelope_id == CHECKPOINT).curve
    assert [turn_down_kw(curve, m, 10000) for m in (1, 30, 31, 120, 121, 240, 241)] == [
        4000, 4000, 3000, 3000, 2000, 2000, 0,
    ]  # fmt: skip
    assert turn_down_kw(curve, 30, 2500) == 2500
    rel = _fixture(RELATIVE).envelopes[0].curve
    assert turn_down_kw(rel, 30, 10000) == 3000
    assert turn_down_kw(rel, 30, 3333) == 999  # floor(3000 bp x 3333 / 10,000)
    assert turn_down_kw(rel, 30, 40000) == 8000  # the kW cap
    assert turn_down_kw(rel, 600, 10000) == 1000 and turn_down_kw(rel, 601, 10000) == 0
    for bad in (0, 1441):
        with pytest.raises(ValueError, match="duration_min"):
            turn_down_kw(curve, bad, 1000)
    with pytest.raises(ValueError, match="floor a float load"):
        turn_down_kw(curve, 30, 1000.5)  # type: ignore[arg-type]


_NIGHT = datetime(2027, 1, 5, 1, tzinfo=UTC)  # a winter Tuesday night: every envelope offered


@pytest.mark.parametrize(
    "duration,load,layers",
    [
        # Merit order: checkpoint, then (power-cap excluded: same group) migrate, to the 5,000 cap.
        (30, 20000, [(CHECKPOINT, 4000, 4000), (MIGRATE, 1000, 5000)]),
        # Checkpoint cannot hold 300 min, so its group is still free for power-cap.
        (300, 20000, [(POWER_CAP, 1500, 1500), (MIGRATE, 1000, 2500)]),
        # The retained IT floor: 3,000 running, 2,000 kept, so 1,000 at most.
        (30, 3000, [(CHECKPOINT, 1000, 1000)]),
        (30, 2000, []),
        (481, 20000, [(POWER_CAP, 1500, 1500)]),
    ],
    ids=["cap", "group-falls-through", "floor", "at-floor", "long"],
)
def test_stack_at_combines_by_merit_order_exclusion_cap_and_floor(
    duration: int, load: int, layers: list[tuple[str, int, int]]
) -> None:
    answer = stack_at(_fixture(CAMPUS), _NIGHT, duration, load)
    assert [(x.envelope_id, x.kw, x.cumulative_kw) for x in answer.layers] == layers
    assert answer.total_kw == (layers[-1][2] if layers else 0)


def test_stack_at_offers_nothing_where_nothing_is_offered() -> None:
    d = _fixture(CAMPUS)
    protected = stack_at(d, _at("2027-01-05T10:00:00Z"), 30, 20000)
    assert protected.layers == () and protected.total_kw == 0
    assert isinstance(protected.state, Unavailable)
    peak = stack_at(d, _at("2027-07-06T17:00:00Z"), 30, 20000)  # summer weekday peak: migrate
    assert [x.envelope_id for x in peak.layers] == [MIGRATE] and peak.total_kw == 1500


def test_deliverable_against_keeps_the_retained_it_floor() -> None:
    d = _fixture(CAMPUS)
    assert [deliverable_against(d, 3000, it_load_kw=x) for x in (1000, 2000, 4000, 9000)] == [
        0, 0, 2000, 3000,
    ]  # fmt: skip


def _call(**over: Any) -> ProposedCall:
    spec: dict[str, Any] = dict(
        kind="proposed_call",
        envelope_ids=(CHECKPOINT,),
        issued_at=_NIGHT - timedelta(hours=1),
        start=_NIGHT,
        duration_min=60,
        turn_down_kw=3000,
        it_load_kw=6000,
    )
    spec.update(over)
    return ProposedCall(**spec)


def _past(start: datetime, minutes: int = 30, kw: int = 1000, *ids: str) -> PastCall:
    return PastCall(
        kind="past_call",
        envelope_ids=tuple(sorted(ids or (CHECKPOINT,))),
        start=start,
        duration_min=minutes,
        turn_down_kw=kw,
    )


def test_a_call_inside_the_declaration_fits() -> None:
    check = check_call(_fixture(CAMPUS), _call(), [])
    assert check.ok and check.reasons == () and check.unchecked == ()


_REASONS: list[tuple[str, dict[str, Any], list[PastCall], tuple[CallReason, ...]]] = [
    (
        "outside-validity",
        {"start": _at("2028-01-01T01:00:00Z"), "issued_at": _at("2028-01-01T00:00:00Z")},
        [],
        (CallReason.OUTSIDE_VALIDITY,),
    ),
    (
        "template-unavailable",
        {"start": _at("2027-01-05T08:00:00Z"), "issued_at": _at("2027-01-05T07:00:00Z")},
        [],
        (CallReason.NOT_OFFERED,),
    ),
    (
        "runs-into-unavailable",  # the whole event: 06:30 to 07:30 crosses into the day band
        {"start": _at("2027-01-05T06:30:00Z"), "issued_at": _at("2027-01-05T05:30:00Z")},
        [],
        (CallReason.NOT_OFFERED,),
    ),
    (
        "envelope-not-offered",  # a winter Saturday daytime offers the training envelopes only
        {
            "envelope_ids": (MIGRATE,),
            "start": _at("2027-01-09T10:00:00Z"),
            "issued_at": _at("2027-01-09T09:00:00Z"),
            "turn_down_kw": 1000,
        },
        [],
        (CallReason.NOT_OFFERED,),
    ),
    (
        "blackout",
        {"start": _at("2027-12-24T01:00:00Z"), "issued_at": _at("2027-12-24T00:00:00Z")},
        [],
        (CallReason.BLACKOUT,),
    ),
    (
        "unknown",  # a summer Saturday evening is unassessed
        {"start": _at("2027-07-10T20:00:00Z"), "issued_at": _at("2027-07-10T19:00:00Z")},
        [],
        (CallReason.AVAILABILITY_UNKNOWN,),
    ),
    ("notice", {"issued_at": _NIGHT - timedelta(seconds=899)}, [], (CallReason.NOTICE_TOO_SHORT,)),
    (
        "duration",
        {"duration_min": 300},
        [],
        (CallReason.DURATION_EXCEEDS_CURVE, CallReason.KW_EXCEEDS_CURVE),
    ),
    ("kw", {"turn_down_kw": 3001}, [], (CallReason.KW_EXCEEDS_CURVE,)),
    (
        "two-alternatives",
        {"envelope_ids": (CHECKPOINT, POWER_CAP)},
        [],
        (CallReason.EXCLUSION_CONFLICT,),
    ),
    (
        "alternative-in-flight",
        {},
        [_past(_NIGHT - timedelta(minutes=30), 60, 1000, POWER_CAP)],
        (CallReason.EXCLUSION_CONFLICT, CallReason.REST_TOO_SHORT),
    ),
    (
        "stack-cap",
        {"duration_min": 30, "turn_down_kw": 4000, "it_load_kw": 9000},
        [_past(_NIGHT - timedelta(minutes=30), 120, 1500, MIGRATE)],
        (CallReason.STACK_CAP_EXCEEDED,),
    ),
    ("floor", {"it_load_kw": 4000}, [], (CallReason.RETAINED_IT_FLOOR,)),
    (
        "event-limit",
        {},
        [_past(_NIGHT - timedelta(days=k)) for k in range(1, 13)],
        (CallReason.EVENT_LIMIT,),
    ),
    ("rest", {}, [_past(_NIGHT - timedelta(minutes=150), 30)], (CallReason.REST_TOO_SHORT,)),
    (
        "budget",
        {},
        [_past(_NIGHT - timedelta(days=k), 240) for k in range(1, 7)],
        (CallReason.BUDGET_EXHAUSTED,),
    ),
]


@pytest.mark.parametrize(
    "over,history,reasons", [r[1:] for r in _REASONS], ids=[r[0] for r in _REASONS]
)
def test_each_breach_of_the_declaration_is_named(
    over: dict[str, Any], history: list[PastCall], reasons: tuple[CallReason, ...]
) -> None:
    check = check_call(_fixture(CAMPUS), _call(**over), history)
    assert check.reasons == reasons and check.ok is (not reasons)


def test_the_budget_window_is_half_open_and_the_class_is_shared() -> None:
    d = _fixture(CAMPUS)
    edge = _past(_NIGHT - timedelta(days=30))  # exactly 30 days before: outside the window
    inside = _past(_NIGHT - timedelta(days=30) + timedelta(minutes=30), 60, 1000, POWER_CAP)
    other = _past(_NIGHT - timedelta(days=2), 60, 1000, MIGRATE)  # another class's call
    used = call_budget_used(d, BATCH, [edge, inside, other], as_of=_NIGHT)
    assert used.events_in_window == 1 and used.minutes_in_window == 60
    assert used.last_end == inside.start + timedelta(minutes=60)
    none = call_budget_used(d, MOVABLE, [], as_of=_NIGHT)
    assert isinstance(none.events_in_window, NotApplicable)  # an unknown limit is not counted
    assert isinstance(none.last_end, NotApplicable)
    with pytest.raises(ValueError, match="no sla class"):
        call_budget_used(d, "SYNTHETIC-CLASS-NONE", [], as_of=_NIGHT)


def test_an_unknown_limit_is_named_unchecked_never_assumed() -> None:
    call = _call(
        envelope_ids=(MIGRATE,),
        issued_at=_NIGHT - timedelta(hours=1),
        turn_down_kw=1500,
    )
    check = check_call(_fixture(CAMPUS), call, [])
    assert check.ok and check.unchecked == (f"sla_classes[{MOVABLE}].call_limits.max_events",)


def test_a_call_naming_an_undeclared_envelope_is_an_error() -> None:
    with pytest.raises(ValueError, match="undeclared"):
        check_call(_fixture(CAMPUS), _call(envelope_ids=("env-ghost",)), [])


# ------------------------------------------------------------------ price


def test_the_price_is_exact_and_an_unknown_component_is_never_zero() -> None:
    d = _fixture(CAMPUS)
    by_id = {e.envelope_id: e for e in d.envelopes}
    # 1,000 p/MWh x 3,000 kW x 60 min / 60,000 = 3,000 p, no event fee.
    assert call_cost_pence(by_id[CHECKPOINT].price, reduction_kw=3000, duration_min=60) == 3000
    assert pence_to_pounds(Fraction(3000)) == 30
    # No utilisation fee, a 2,000 p event fee; 500 p/MW/h held for 1,500 kW x 120 min = 1,500 p.
    assert call_cost_pence(by_id[MIGRATE].price, reduction_kw=1500, duration_min=120) == 2000
    assert availability_cost_pence(by_id[MIGRATE].price, held_kw=1500, held_min=120) == 1500
    assert call_cost_pence(by_id[CHECKPOINT].price, reduction_kw=1, duration_min=1) == Fraction(
        1, 60
    )
    assert isinstance(
        call_cost_pence(by_id[POWER_CAP].price, reduction_kw=1000, duration_min=60), Unknown
    )
    unknown_fee = by_id[CHECKPOINT].price.model_copy(  # type: ignore[union-attr]
        update={"per_event": Unknown(kind="unknown", reason="x")}
    )
    assert isinstance(call_cost_pence(unknown_fee, reduction_kw=1, duration_min=1), Unknown)
    assert isinstance(
        availability_cost_pence(by_id[CHECKPOINT].price, held_kw=1, held_min=1), Fraction
    )


# ------------------------------------------------------------------ references, supersession


def test_active_at_follows_supersession() -> None:
    pair = [_fixture(CAMPUS), _fixture(SUCCESSOR)]
    assert _fixture(SUCCESSOR).supersedes == _fixture(CAMPUS).content_hash()
    got = {
        t: getattr(active_at(pair, _at(t)), "declaration_id", "none")
        for t in ("2027-06-30T23:30:00Z", "2027-07-01T00:00:00Z", "2028-01-01T00:00:00Z")
    }
    assert got == {
        "2027-06-30T23:30:00Z": "ic9-synthetic-gpu-campus-2027",
        "2027-07-01T00:00:00Z": "ic9-synthetic-gpu-campus-2027-h2",
        "2028-01-01T00:00:00Z": "none",
    }
    assert isinstance(active_at([], _at("2027-03-01T00:00:00Z")), NotApplicable)


def test_active_at_refuses_ambiguity() -> None:
    twin = load_ic9(CAMPUS)
    twin["declaration_id"] = "ic9-synthetic-gpu-campus-2027-twin"
    pair = [_fixture(CAMPUS), _validate(_sealed(twin))]
    with pytest.raises(ValueError, match="none supersedes the others"):
        active_at(pair, _at("2027-03-01T00:00:00Z"))
    with pytest.raises(ValueError, match="one site's declarations"):
        active_at([_fixture(CAMPUS), _fixture(SPRING)], _at("2027-03-26T00:00:00Z"))
    early = load_ic9(SUCCESSOR)
    early["valid_from"] = "2026-12-01T00:00:00Z"
    with pytest.raises(ValueError, match="starts before the declaration it supersedes"):
        active_at([_fixture(CAMPUS), _validate(_sealed(early))], _at("2027-03-01T00:00:00Z"))


def test_a_declaration_is_referenced_by_its_content_hash() -> None:
    d = _fixture(CAMPUS)
    ref = declaration_ref(d)
    assert ref.contract == "IC-9" and ref.contract_version == CONTRACT_VERSION
    assert ref.declaration_hash == GOLDEN_HASHES_IC9[CAMPUS]
    assert ref.declaration_id == d.declaration_id


def test_sla_evidence_resolves_as_a_service_level_agreement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d = _fixture(CAMPUS)
    assert isinstance(resolve_sla_evidence(d.sla_classes[1], []), Unknown)
    seen: dict[str, Any] = {}

    def spy(ref: Any, objects: Any, **kw: Any) -> str:
        seen.update(kw, ref=ref)
        return "resolved"

    monkeypatch.setattr(compute_turndown, "resolve_ref", spy)
    assert resolve_sla_evidence(d.sla_classes[0], []) == "resolved"
    assert seen["expected_type"].value == "service_level_agreement"
    assert seen["include_synthetic"] is False and "admit_self_issued" not in seen


def test_a_declaration_copy_with_a_new_id_is_a_new_hash() -> None:
    data = copy.deepcopy(load_ic9(CAMPUS))
    data["declaration_id"] = "ic9-synthetic-gpu-campus-2027-b"
    assert _validate(_sealed(data)).content_hash() != GOLDEN_HASHES_IC9[CAMPUS]


# ------------------------------------------------------------------ mutation checks

_T = "test_each_envelope_class_and_price_guard_refuses"
_TPL = "test_the_template_and_overrides_are_complete_canonical_and_on_the_grid"
_BREACH = "test_each_breach_of_the_declaration_is_named"
_STACK = "test_stack_at_combines_by_merit_order_exclusion_cap_and_floor"

MUTATIONS = [
    (
        "ramp finishes inside the shortest hold",
        "        if self.response.ramp_s >= shortest * 60:\n",
        "        if False:\n",
        f"{_T}[ramp]",
    ),
    (
        "curve holds strictly increase",
        "    if any(b <= a for a, b in zip(holds, holds[1:], strict=False)):\n",
        "    if False:\n",
        f"{_T}[holds]",
    ),
    (
        "curve amounts strictly decrease",
        "    if any(b >= a for a, b in zip(amounts, amounts[1:], strict=False)):\n",
        "    if False:\n",
        f"{_T}[relative-amounts]",
    ),
    (
        "an all-unknown price is the whole price unknown",
        "        if all(isinstance(p, Unknown) for p in parts):\n",
        "        if False:\n",
        f"{_T}[price-all-unknown]",
    ),
    (
        "a firm offer states every component",
        "        if isinstance(self.price_basis, FirmOffer) and any(isinstance(p, Unknown) for p "
        "in parts):\n",
        "        if False:\n",
        f"{_T}[firm-offer-unknown]",
    ),
    (
        "deferred work returns",
        "        if action == WorkloadAction.DEFERRED and isinstance(ret, NoReturn):\n",
        "        if False:\n",
        f"{_T}[deferred-no-return]",
    ),
    (
        "moved or dropped work does not return",
        "        if action in (WorkloadAction.MOVED, WorkloadAction.DROPPED) and isinstance(\n",
        "        if False and isinstance(\n",
        f"{_T}[moved-returns]",
    ),
    (
        "no return, no rebound",
        "        if isinstance(ret, NoReturn) and not isinstance(e.rebound, NotApplicable):\n",
        "        if False:\n",
        f"{_T}[rebound-no-return]",
    ),
    (
        "energy returns, so a rebound is stated or unknown",
        "        if isinstance(ret, ReturnsLater) and isinstance(e.rebound, NotApplicable):\n",
        "        if False:\n",
        f"{_T}[rebound-returns-na]",
    ),
    (
        "an unknown return makes the rebound unknown",
        "        if isinstance(ret, Unknown) and not isinstance(e.rebound, Unknown):\n",
        "        if False:\n",
        f"{_T}[rebound-unknown-class]",
    ),
    (
        "synthetic labels follow the record class",
        "        if text.startswith(_SYNTHETIC) != synthetic:\n",
        "        if False:\n",
        "test_a_synthetic_label_is_exactly_the_record_class",
    ),
    (
        "a placeholder price is exactly a synthetic record",
        "            if placeholder != synthetic:\n",
        "            if False:\n",
        "test_a_placeholder_price_is_exactly_a_synthetic_record",
    ),
    (
        "an envelope names a declared class",
        "        if e.sla_class_id not in by_id:\n",
        "        if False:\n",
        f"{_T}[dangling-class]",
    ),
    (
        "every class is named",
        "    if unused:\n",
        "    if False:\n",
        f"{_T}[unused-class]",
    ),
    (
        "the merit order lists every envelope once",
        "    if sorted(d.stacking.merit_order) != ids:\n",
        "    if False:\n",
        f"{_T}[merit-order]",
    ),
    (
        "an exclusion group has two members",
        "        if len(es) < 2:\n",
        "        if False:\n",
        f"{_T}[group-of-one]",
    ),
    (
        "the concurrent cap can bind",
        "        if d.stacking.max_concurrent_turn_down_kw > sum(best.values()):\n",
        "        if False:\n",
        f"{_T}[cap-cannot-bind]",
    ),
    (
        "an offer names declared envelopes",
        "            if missing:\n",
        "            if False:\n",
        f"{_TPL}[undeclared-offer]",
    ),
    (
        "every envelope is offered somewhere",
        "    if never:\n",
        "    if False:\n",
        f"{_T}[never-offered]",
    ),
    (
        "overrides do not overlap",
        "        if b.start < a.end:\n",
        "        if False:\n",
        f"{_TPL}[override-overlap]",
    ),
    (
        "overrides lie inside the validity",
        "        if not (d.valid_from <= o.start and o.end <= d.valid_until):\n",
        "        if False:\n",
        f"{_TPL}[override-outside]",
    ),
    (
        "the months are partitioned",
        "        if sorted(months) != list(range(1, 13)):\n",
        "        if False:\n",
        f"{_TPL}[months]",
    ),
    (
        "the template is canonical",
        "        if all(row[(s, g1, b)] == row[(s, g2, b)] for s in seasons for b in bands):\n",
        "        if False:\n",
        f"{_TPL}[redundant-group]",
    ),
    (
        "the resolved hash binds the compilation",
        "        if resolve(self).content_hash() != self.resolved_hash:\n",
        "        if False:\n",
        "test_the_resolved_hash_must_be_the_compiled_timeline",
    ),
    (
        "the resolver walks the clock change a half-hour at a time",
        "        if nxt.astimezone(_TZ).utcoffset() != t.astimezone(_TZ).utcoffset():\n",
        "        if False:\n",
        "test_the_resolver_matches_an_independent_oracle_at_every_half_hour[ic9_declaration_"
        "clock_change_spring.synthetic.json]",
    ),
    (
        "the naive-local reader is fold=0",
        "    instant = naive.replace(tzinfo=_TZ, fold=0).astimezone(UTC)\n",
        "    instant = naive.replace(tzinfo=_TZ, fold=1).astimezone(UTC)\n",
        "test_the_clock_change_hours_read_as_the_specification_says",
    ),
    (
        "timeline steps that do not change are one step",
        "            if (b.origin, b.state) == (a.origin, a.state):\n",
        "            if False:\n",
        "test_a_malformed_timeline_is_refused[adjacent-same]",
    ),
    (
        "turn-down never exceeds the running load",
        "                return min(p.turn_down_kw, it_load_kw)\n",
        "                return p.turn_down_kw\n",
        "test_turn_down_reads_the_first_point_covering_the_duration_never_above_the_load",
    ),
    (
        "a relative curve keeps its kW cap",
        "            return kw if isinstance(curve.cap_kw, NotApplicable) else "
        "min(kw, curve.cap_kw)\n",
        "            return kw\n",
        "test_turn_down_reads_the_first_point_covering_the_duration_never_above_the_load",
    ),
    (
        "stack_at takes one envelope per exclusion group",
        "            if _group_of(e) in groups:\n",
        "            if False:\n",
        f"{_STACK}[cap]",
    ),
    (
        "stack_at stops at the concurrent cap",
        "            declaration, declaration.stacking.max_concurrent_turn_down_kw, it_load_kw\n",
        "            declaration, 10**9, it_load_kw\n",
        f"{_STACK}[cap]",
    ),
    (
        "the retained IT floor is kept",
        "    return min(total_kw, max(0, it_load_kw - floor))\n",
        "    return total_kw\n",
        f"{_STACK}[floor]",
    ),
    (
        "a dated withdrawal reads as a blackout",
        '                    CallReason.BLACKOUT if a.origin == "override" else '
        "CallReason.NOT_OFFERED\n",
        "                    CallReason.NOT_OFFERED\n",
        f"{_BREACH}[blackout]",
    ),
    (
        "the call needs the envelope's notice",
        "    if any(notice_s < e.response.min_notice_s for e in named):\n",
        "    if False:\n",
        f"{_BREACH}[notice]",
    ),
    (
        "a call never names alternatives at once",
        "    if len(set(groups)) != len(groups) or busy & set(groups):\n",
        "    if False:\n",
        f"{_BREACH}[alternative-in-flight]",
    ),
    (
        "concurrent calls stay under the cap",
        "    if concurrent > declaration.stacking.max_concurrent_turn_down_kw:\n",
        "    if False:\n",
        f"{_BREACH}[stack-cap]",
    ),
    (
        "the class's event budget is shared",
        "            if used.events_in_window + 1 > limits.max_events.max_events:\n",
        "            if False:\n",
        f"{_BREACH}[event-limit]",
    ),
    (
        "the class's minimum rest",
        "            if isinstance(last, datetime) and call.start - last < timedelta(\n",
        "            if False and isinstance(last, datetime) and call.start - last < timedelta(\n",
        f"{_BREACH}[rest]",
    ),
    (
        "the class's duration budget",
        "            if used.minutes_in_window + call.duration_min > "
        "limits.max_cumulative.max_minutes:\n",
        "            if False:\n",
        f"{_BREACH}[budget]",
    ),
    (
        "the budget window is half-open",
        "        return [c for c in mine if as_of - timedelta(days=days) < c.start]\n",
        "        return [c for c in mine if as_of - timedelta(days=days) <= c.start]\n",
        "test_the_budget_window_is_half_open_and_the_class_is_shared",
    ),
    (
        "an unknown price component is never zero",
        '            return unknown(f"a component of the price is unknown: {part.reason}")\n',
        "            return Fraction(0)\n",
        "test_the_price_is_exact_and_an_unknown_component_is_never_zero",
    ),
    (
        "two declarations in force are refused",
        "    if len(live) > 1:\n",
        "    if False:\n",
        "test_active_at_refuses_ambiguity",
    ),
]


def _run_mutant(old: str, new: str, test: str) -> int:
    return run_mutant(
        "gridq.contracts.compute_turndown", old, new, f"{Path(__file__).resolve()}::{test}"
    )


@pytest.mark.parametrize("test", sorted({m[3] for m in MUTATIONS}))
def test_mutation_runner_passes_on_the_unmutated_source(test: str) -> None:
    """Control: the harness itself is sound, so a red mutant below means the guard bit."""
    assert _run_mutant("", "", test) == 0, test


@pytest.mark.parametrize("guard,old,new,test", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_reverting_a_guard_turns_its_test_red(guard: str, old: str, new: str, test: str) -> None:
    source = Path(compute_turndown.__file__).read_text(encoding="utf-8")
    assert source.count(old) == 1, guard
    # Exit 1 is pytest's "tests ran and failed": the guard is load-bearing. A mutant that cannot
    # load exits 3, and a collection error exits 2, so neither passes as red.
    assert _run_mutant(old, new, test) == 1, guard
