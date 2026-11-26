"""IC-3 dispatch envelope v1.0.0 conformance (roadmap WP-SIZE-07).

Acceptance:
1. Contract version is tagged and conformance tests pass.
2. An envelope without a source ``input_hash`` is invalid.
3. Consumers can run against fixtures without the sizer.

Plus the guards the design rests on (D85-D89): the template compiles onto UTC
half-hours exactly, across both clock changes, and the edge can trust the compiled timeline by hash;
every partition sums exactly (no double-booking as arithmetic, D57); held energy covers each slice
for its sustain time; the fail-safe is the floor of the whole validity; thermal bands sit inside the
IC-11 binding band and committed flex is provable against the surface; the source binds the run,
the quote and the references; a study basis is never underwriting (D46); no ``None`` (D45). Then
mutation checks proving eighteen of those guards are load-bearing.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import _ic3_fixtures
import pytest
from _contracts_support import (
    FIXTURES,
    FIXTURES_IC3,
    GOLDEN_HASHES,
    GOLDEN_HASHES_IC3,
    GOLDEN_HASHES_V110,
    IC3_FIXTURES,
    load_ic3,
    run_mutant,
)
from pydantic import ValidationError

from gridq.contracts import dispatch_envelope
from gridq.contracts.__main__ import FROZEN, render, schema_path
from gridq.contracts._base import EMPTY_SHA256, NotApplicable, Unknown, content_hash
from gridq.contracts.connection import ConnectionAgreement
from gridq.contracts.dispatch_envelope import (
    CONTRACT_VERSION,
    GIT_TAG,
    RANK,
    SUPPORTED_VERSIONS,
    AgreementReason,
    CommittedService,
    Direction,
    DispatchEnvelope,
    ResolvedTimeline,
    ScheduledPeriod,
    SurfaceReason,
    TimeBase,
    UnderwritingBasisLabel,
    ViolationCode,
    active_at,
    basis_banner,
    basis_label,
    check_against_agreement,
    check_against_surface,
    check_constraint_library,
    check_schedule,
    check_source,
    derive_run_id,
    envelope_ref,
    fail_safe_terms,
    flat_limits,
    merchant_headroom,
    offered_kw,
    reserved_kw,
    resolve,
    resolved_hash_of,
    service_windows,
    terms_at,
    timeline_matches,
    timeline_segments,
    timeline_terms_at,
    underwritten_total,
)
from gridq.contracts.thermal_flex import ThermalFlexAvailability

HYBRID = "ic3_envelope_hybrid_bess.synthetic.json"
SUCCESSOR = "ic3_envelope_hybrid_bess_successor.synthetic.json"
DC = "ic3_envelope_dc_thermal.synthetic.json"
WEEK = "ic3_envelope_clock_change_week.synthetic.json"
TIMELINE = "ic3_timeline_clock_change_week.synthetic.json"
ENVELOPES = (HYBRID, SUCCESSOR, DC, WEEK)
CM, FLEX, DFS = _ic3_fixtures.CM, _ic3_fixtures.FLEX, _ic3_fixtures.DFS
MODELLED_SURFACE = FIXTURES / "ic11_surface_chilled_water_modelled.synthetic.json"
METERED_SURFACE = FIXTURES / "ic11_surface_metered_trial.synthetic.json"
IC1_FIRM = FIXTURES / "ic1_archetype_firm.synthetic.json"
IC1_HYBRID_V110 = FIXTURES / "v1_1_0" / "ic1_hybrid_evidence_backed.synthetic.json"
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


def _validate(data: dict[str, Any]) -> DispatchEnvelope:
    return DispatchEnvelope.model_validate_json(json.dumps(data))


@cache
def _fixture(name: str) -> DispatchEnvelope:
    return DispatchEnvelope.model_validate_json((FIXTURES_IC3 / name).read_text(encoding="utf-8"))


@cache
def _surface(path: Path) -> ThermalFlexAvailability:
    return ThermalFlexAvailability.model_validate_json(path.read_text(encoding="utf-8"))


def _sealed(data: dict[str, Any]) -> dict[str, Any]:
    """Re-seal ``resolved_hash`` after an edit, so only the guard under test can refuse."""
    data["resolved_hash"] = resolved_hash_of(data)
    return data


def _refused(data: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        _validate(data)


def _terms(data: dict[str, Any], terms_id: str) -> dict[str, Any]:
    terms: dict[str, Any] = next(n["terms"] for n in data["terms"] if n["terms_id"] == terms_id)
    return terms


def _hash_of(env: DispatchEnvelope, terms_id: str) -> str:
    return content_hash(next(n.terms for n in env.terms if n.terms_id == terms_id))


def _walk(node: Any) -> Any:
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _with_source(data: dict[str, Any], *, input_hash: str, rederive: bool = True) -> None:
    src = data["source"]
    src["input_hash"] = input_hash
    src["assessment"]["quote_input_hash"] = input_hash
    if rederive:
        run = src["run"]
        run["run_id"] = derive_run_id(
            input_hash, run["assumption_version"], run["code_version"], run["schema_version"]
        )
        src["assessment"]["quote_id"] = "quote_" + run["run_id"]


# ------------------------------------------------------------------ 1. version tagged, conformance


def test_the_contract_version_is_tagged() -> None:
    assert CONTRACT_VERSION == "1.0.0"
    assert SUPPORTED_VERSIONS == ("1.0.0",) and SUPPORTED_VERSIONS[-1] == CONTRACT_VERSION
    assert GIT_TAG == f"contracts/ic3-dispatch-envelope/v{CONTRACT_VERSION}"
    assert "SUPPORTED_VERSIONS" in dispatch_envelope.__all__


def test_the_git_tag_is_annotated_and_carries_the_frozen_schemas() -> None:
    """Acceptance 1 against git itself. A checkout without the tag (a tagless CI clone) skips."""
    root = Path(__file__).resolve().parents[2]

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
        )

    try:
        kind = git("cat-file", "-t", f"refs/tags/{GIT_TAG}")
    except FileNotFoundError:
        pytest.skip("git is not installed")
    if kind.returncode != 0:
        pytest.skip(f"{GIT_TAG} is not in this checkout")
    assert kind.stdout.strip() == "tag", "the tag must be annotated, not lightweight"
    for stem in ("ic3_dispatch_envelope", "ic3_resolved_timeline"):
        path = schema_path(stem, CONTRACT_VERSION)
        rel = path.relative_to(root).as_posix()
        at_tag = git("show", f"{GIT_TAG}:{rel}")
        assert at_tag.returncode == 0, rel
        assert at_tag.stdout == path.read_text(encoding="utf-8"), rel


@pytest.mark.parametrize(
    "stem,model",
    [("ic3_dispatch_envelope", DispatchEnvelope), ("ic3_resolved_timeline", ResolvedTimeline)],
)
def test_the_frozen_schema_is_committed_and_matches_the_model(stem: str, model: type) -> None:
    assert (stem, model, CONTRACT_VERSION) in FROZEN
    path = schema_path(stem, CONTRACT_VERSION)
    assert path.read_text(encoding="utf-8") == render(model)


@pytest.mark.parametrize("path", IC3_FIXTURES, ids=lambda p: p.name)
def test_every_fixture_validates_at_1_0_0_with_its_pinned_hash(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    model = ResolvedTimeline if "_timeline_" in path.name else DispatchEnvelope
    obj = model.model_validate_json(text)
    assert (obj.contract, obj.contract_version) == ("IC-3", CONTRACT_VERSION)
    assert obj.content_hash() == GOLDEN_HASHES_IC3[path.name]
    again = model.model_validate_json(obj.model_dump_json(indent=2))
    assert again == obj and again.content_hash() == obj.content_hash()


@pytest.mark.parametrize("name", [*ENVELOPES, TIMELINE])
@pytest.mark.parametrize(
    "field,value",
    [
        ("contract_version", "1.0.1"),
        ("contract_version", "1.1.0"),
        ("contract_version", "2.0.0"),
        ("contract", "IC-1"),
        ("contract", "IC-11"),
    ],
)
def test_another_version_or_contract_is_refused(name: str, field: str, value: str) -> None:
    data = load_ic3(name)
    data[field] = value
    model = ResolvedTimeline if name == TIMELINE else DispatchEnvelope
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(data))


def test_the_committed_fixtures_equal_a_fresh_build() -> None:
    assert sorted(p.name for p in _ic3_fixtures.BUILDERS) == sorted(GOLDEN_HASHES_IC3)
    for path, build in _ic3_fixtures.BUILDERS.items():
        assert path.read_text(encoding="utf-8") == _ic3_fixtures.render(build()), path.name


# ------------------------------------------------------------------ 2. no input_hash, no envelope

_MISSING = object()


@pytest.mark.parametrize("name", ENVELOPES)
@pytest.mark.parametrize(
    "value",
    [
        _MISSING,
        None,
        {"kind": "unknown", "reason": "not known"},
        {"kind": "not_applicable", "reason": "no run"},
        "",
        "sha256:",
        "sha256:abc",
        "sha256:" + "A" * 64,
        "md5:" + "0" * 32,
        "0" * 64,
    ],
    ids=[
        "missing",
        "null",
        "unknown",
        "not_applicable",
        "empty",
        "bare-prefix",
        "short",
        "upper-case",
        "other-algorithm",
        "no-prefix",
    ],
)
def test_an_envelope_without_a_source_input_hash_is_invalid(name: str, value: object) -> None:
    data = load_ic3(name)
    if value is _MISSING:
        del data["source"]["input_hash"]
    else:
        data["source"]["input_hash"] = value
    with pytest.raises(ValidationError) as caught:
        _validate(data)
    assert any(e["loc"][:2] == ("source", "input_hash") for e in caught.value.errors())


def test_an_input_hash_of_nothing_is_invalid() -> None:
    """The hash of empty input is well-formed and self-consistent here (quote and run_id agree):
    only the rule that an envelope needs an input refuses it."""
    data = load_ic3(HYBRID)
    _with_source(data, input_hash=EMPTY_SHA256)
    _refused(data, "hash of no input")


def test_the_quote_input_hash_must_be_the_envelope_input_hash() -> None:
    data = load_ic3(HYBRID)
    data["source"]["assessment"]["quote_input_hash"] = "sha256:" + "1" * 64
    _refused(data, "quote_input_hash must equal source.input_hash")


def test_run_id_must_derive_from_the_run_pins() -> None:
    data = load_ic3(HYBRID)
    data["source"]["run"]["code_version"] = "0.0.1-synthetic"
    _refused(data, "does not derive from the run pins")
    data = load_ic3(HYBRID)
    data["source"]["run"]["run_id"] = "run_" + "0" * 16
    _refused(data, "does not derive from the run pins")


def test_a_consistent_new_input_hash_is_a_new_envelope() -> None:
    """The refusals above are not over-broad: a re-run with consistent pins validates, and its
    envelope hash and reference move with it."""
    data = load_ic3(HYBRID)
    _with_source(data, input_hash="sha256:" + "2" * 64)
    env = _validate(data)
    ref = envelope_ref(env)
    assert ref.input_hash == "sha256:" + "2" * 64
    assert ref.envelope_hash != GOLDEN_HASHES_IC3[HYBRID]
    assert envelope_ref(_fixture(HYBRID)).envelope_hash == GOLDEN_HASHES_IC3[HYBRID]


def test_derive_run_id_is_the_provenance_derivation(monkeypatch: pytest.MonkeyPatch) -> None:
    from gridq.provenance import run_context

    monkeypatch.setattr(run_context, "get_code_version", lambda: "0.0.0-synthetic+é")
    ctx = run_context.build_run_context({"synthetic": "input"}, "assumptions-é", "0.1.0")
    assert ctx.run_id == derive_run_id(
        ctx.input_hash, ctx.assumption_version, ctx.code_version, ctx.schema_version
    )
    for name in ENVELOPES:
        run = _fixture(name).source.run
        assert run.schema_version == "0.1.0"
        assert run.run_id == derive_run_id(
            _fixture(name).source.input_hash,
            run.assumption_version,
            run.code_version,
            run.schema_version,
        )


def test_check_source_compares_what_the_caller_holds() -> None:
    env = _fixture(HYBRID)
    src = env.source
    ok = check_source(
        env,
        quote_input_hash=src.input_hash,
        quote_id=src.assessment.quote_id,
        scenario_set_hash=_ic3_fixtures.IC2_HYBRID_V110_HASH,
        agreement_hash=_ic3_fixtures.IC1_HYBRID_V110_HASH,
    )
    assert ok.ok and len(ok.compared) == 4 and ok.findings == ()
    bad = check_source(env, quote_input_hash="sha256:" + "3" * 64)
    assert not bad.ok and [f.status for f in bad.findings] == ["mismatch"]
    dc = check_source(_fixture(DC), scenario_set_hash=_ic3_fixtures.IC2_HYBRID_V110_HASH)
    assert not dc.ok and [f.status for f in dc.findings] == ["unverifiable"]
    week = check_source(_fixture(WEEK), scenario_set_hash=_ic3_fixtures.IC2_HYBRID_V110_HASH)
    assert not week.ok and [f.status for f in week.findings] == ["mismatch"]
    with pytest.raises(ValueError, match="at least one"):
        check_source(env)


# ------------------------------------------------------------------ 3. consumers without the sizer


_CONSUMER = """
import json, pathlib, sys
from datetime import UTC, datetime, timedelta
from gridq.contracts.dispatch_envelope import (
    DispatchEnvelope, ResolvedTimeline, ScheduledPeriod, active_at, check_against_surface,
    check_schedule, check_source, flat_limits, terms_at, timeline_matches, timeline_terms_at,
)
from gridq.contracts.thermal_flex import ThermalFlexAvailability
fx, surface_path, ic1_hash, ic2_hash = pathlib.Path(sys.argv[1]), sys.argv[2], *sys.argv[3:5]
read = lambda p: pathlib.Path(p).read_text(encoding="utf-8")
env = {
    p.name: DispatchEnvelope.model_validate_json(read(p))
    for p in sorted(fx.glob("ic3_envelope_*.synthetic.json"))
}
out = {"hashes": {n: e.content_hash() for n, e in env.items()}}
# The edge: a compiled timeline trusted by hash, read with no clock logic of its own.
week = env["ic3_envelope_clock_change_week.synthetic.json"]
timeline = ResolvedTimeline.model_validate_json(
    read(fx / "ic3_timeline_clock_change_week.synthetic.json")
)
out["timeline_matches"] = timeline_matches(week, timeline)
half = [timeline.valid_from + timedelta(minutes=30 * i) for i in range(timeline.n_periods)]
out["edge_agrees"] = all(timeline_terms_at(timeline, t) == terms_at(week, t) for t in half)
out["merchant_at"] = {
    t: flat_limits(timeline_terms_at(timeline, datetime.fromisoformat(t))).merchant_discharge_kw
    for t in ("2027-03-27T06:30:00+00:00", "2027-03-28T06:30:00+00:00")
}
# Dispatch: which envelope is in force, and a schedule checked against it.
pair = [
    env["ic3_envelope_hybrid_bess.synthetic.json"],
    env["ic3_envelope_hybrid_bess_successor.synthetic.json"],
]
out["active"] = {
    t: getattr(active_at(pair, datetime.fromisoformat(t)), "envelope_id", "none")
    for t in ("2027-06-30T23:30:00+00:00", "2027-07-01T00:00:00+00:00",
              "2028-06-30T23:30:00+00:00", "2028-07-01T00:00:00+00:00")
}
period = {
    "start": "2027-01-04T16:00:00Z", "end": "2027-01-04T16:30:00Z",
    "merchant_discharge_kw": 1500, "merchant_charge_kw": 0,
    "service_delivery": [
        {"service_id": "svc-capacity-market", "direction": "discharge", "kw": 1000},
        {"service_id": "svc-local-flex-winter-peak", "direction": "discharge", "kw": 2000},
    ],
    "site_import_kw": 0, "site_export_kw": 4500, "soe_start_kwh": 9000, "soe_end_kwh": 6750,
    "temperature": {"kind": "not_applicable", "reason": "no cooling plant"},
}
bad = {**period, "merchant_discharge_kw": 1600, "site_import_kw": 10001, "soe_end_kwh": 900}
schedule = [ScheduledPeriod.model_validate_json(json.dumps(p)) for p in (period, bad)]
out["schedule"] = [[v.period, v.code.value] for v in check_schedule(pair[0], schedule).violations]
# The dashboard and the ledger: thermal commitments against IC-11, the source against IC-1/IC-2.
surface = ThermalFlexAvailability.model_validate_json(read(surface_path))
out["surface"] = check_against_surface(
    env["ic3_envelope_dc_thermal.synthetic.json"], surface
).ok
out["source"] = check_source(pair[0], agreement_hash=ic1_hash, scenario_set_hash=ic2_hash).ok
out["engine_modules"] = sorted(
    m for m in sys.modules if m.startswith("gridq.") and not m.startswith("gridq.contracts")
)
out["heavy"] = sorted(
    m for m in ("numpy", "pandas", "linopy", "highspy", "xarray", "scipy") if m in sys.modules
)
print(json.dumps(out))
"""


def test_a_consumer_runs_on_the_fixtures_without_the_sizer() -> None:
    """Fresh interpreter, contract package only: no sizer, engine or solver module is imported."""
    res = subprocess.run(
        [
            sys.executable,
            "-c",
            _CONSUMER,
            str(FIXTURES_IC3),
            str(MODELLED_SURFACE),
            _ic3_fixtures.IC1_HYBRID_V110_HASH,
            _ic3_fixtures.IC2_HYBRID_V110_HASH,
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        check=True,
    )
    out = json.loads(res.stdout)
    assert out["engine_modules"] == [] and out["heavy"] == [], out
    assert out["hashes"] == {n: GOLDEN_HASHES_IC3[n] for n in ENVELOPES}
    assert out["timeline_matches"] is True and out["edge_agrees"] is True
    # 06:30 UTC is still night (07:00 local is 07:00 UTC) on the 27th, and day on the 28th (BST).
    assert out["merchant_at"] == {
        "2027-03-27T06:30:00+00:00": 1800,
        "2027-03-28T06:30:00+00:00": 800,
    }
    assert out["active"] == {
        "2027-06-30T23:30:00+00:00": "ic3-synthetic-hybrid-bess-2027",
        "2027-07-01T00:00:00+00:00": "ic3-synthetic-hybrid-bess-2027-h2",
        "2028-06-30T23:30:00+00:00": "ic3-synthetic-hybrid-bess-2027-h2",
        "2028-07-01T00:00:00+00:00": "none",
    }
    assert out["schedule"] == [
        [1, "import_limit"],
        [1, "soe_below_resilience_floor"],
        [1, "merchant_over_slice"],
    ]
    assert out["surface"] is True and out["source"] is True


# ------------------------------------------------------------------ the fixtures are synthetic


def test_every_fixture_is_labelled_synthetic_and_invents_nothing() -> None:
    from gridq.extensions import study_bases

    placeholder = _ic3_fixtures._placeholder_hash
    pinned = {*GOLDEN_HASHES.values(), *GOLDEN_HASHES_V110.values()}
    for name in ENVELOPES:
        env = _fixture(name)
        assert env.record_class == "synthetic_fixture" and env.site_id.startswith("SYNTHETIC-")
        # The banner is the registered study basis's own, verbatim (D46/D55).
        basis = env.source.basis
        assert basis.kind == "study" and basis.basis_id in study_bases.ids()
        assert basis.label == study_bases.get(basis.basis_id).LABEL
        assert "NOT AN UNDERWRITING OUTPUT" in basis_banner(env)
        # Input and evidence hashes are placeholders; every reference is to a pinned fixture.
        assert env.source.input_hash.startswith("sha256:")
        assert env.source.input_hash not in pinned
        for ref in (env.source.connection_agreement, env.source.scenario_set):
            h = getattr(ref, "agreement_hash", getattr(ref, "scenario_set_hash", None))
            assert h is None or h in pinned, name
        if not isinstance(env.thermal, NotApplicable | Unknown):
            assert env.thermal.surface.availability_hash in pinned
        for s in env.services:
            assert isinstance(s.underwritten, Unknown | NotApplicable), (name, s.service_id)
            assert isinstance(s.reference, Unknown)
            if s.basis.kind == "evidenced":
                assert s.basis.evidence.evidence_hash == placeholder(
                    "capacity agreement, hybrid site"
                )
    assert _fixture(HYBRID).source.input_hash == placeholder("assessment run inputs, hybrid site")


@pytest.mark.parametrize("path", IC3_FIXTURES, ids=lambda p: p.name)
def test_no_fixture_holds_a_null_anywhere(path: Path) -> None:
    assert None not in list(_walk(json.loads(path.read_text(encoding="utf-8"))))


@pytest.mark.parametrize("field", list(load_ic3(HYBRID)))
def test_null_is_refused_for_every_envelope_field(field: str) -> None:
    data = load_ic3(HYBRID)
    data[field] = None
    with pytest.raises(ValidationError):
        _validate(data)


def test_the_models_are_final_and_frozen() -> None:
    for model in (DispatchEnvelope, ResolvedTimeline):
        with pytest.raises(TypeError, match="final"):
            type("Sub", (model,), {})
    env = _fixture(HYBRID)
    with pytest.raises(ValidationError):
        env.site_id = "SYNTHETIC-OTHER"  # type: ignore[misc]


# ------------------------------------------------------------------ the partition (D57 arithmetic)


def test_the_discharge_partition_sums_to_the_rating() -> None:
    """Booking one more kW to a service without taking it from merchant is double-booking."""
    data = load_ic3(HYBRID)
    t = _terms(data, "cm-day")
    t["discharge"]["services"][0]["kw"] = 1001
    t["energy"]["held_up"][0]["kwh"] = 1001
    t["energy"]["merchant_kwh"] -= 1
    _refused(_sealed(data), "discharge partition sums to 5001 kW")


def test_the_charge_partition_sums_to_the_rating() -> None:
    data = load_ic3(HYBRID)
    _terms(data, "base")["charge"]["merchant_kw"] = 4999
    _refused(_sealed(data), "charge partition sums to 4999 kW")


def test_the_energy_stack_sums_to_the_usable_energy() -> None:
    data = load_ic3(HYBRID)
    _terms(data, "base")["energy"]["merchant_kwh"] += 1
    _refused(_sealed(data), "energy stack sums to 10001 kWh")


def test_held_energy_covers_each_slice_for_its_sustain_time() -> None:
    data = load_ic3(HYBRID)
    t = _terms(data, "cm-day")
    t["energy"]["held_up"][0]["kwh"] = 999
    t["energy"]["merchant_kwh"] += 1
    _refused(_sealed(data), "holds 999 kWh, below 1000 kW for 60 min")


def test_a_slice_needs_held_energy_and_held_energy_needs_a_slice() -> None:
    data = load_ic3(HYBRID)
    t = _terms(data, "cm-day")
    t["energy"]["merchant_kwh"] += t["energy"]["held_up"][0]["kwh"]
    t["energy"]["held_up"] = []
    _refused(data, "every discharge service slice has held energy")


def test_a_zero_slice_is_absence_not_a_slice() -> None:
    data = load_ic3(HYBRID)
    t = _terms(data, "cm-day")
    t["discharge"]["services"][0]["kw"] = 0
    _refused(data, "greater than or equal to 1")


def test_withheld_energy_needs_a_withheld_power_tranche() -> None:
    data = load_ic3(HYBRID)
    e = _terms(data, "base")["energy"]
    e["merchant_kwh"] -= 100
    e["withheld_kwh"] = 100
    _refused(data, "withheld energy needs a withheld power tranche")


def test_a_cooling_only_service_has_no_battery_slice() -> None:
    data = load_ic3(DC)
    t = _terms(data, "winter-dfs")
    t["discharge"]["services"] = [{"service_id": DFS, "kw": 100}]
    t["discharge"]["merchant_kw"] -= 100
    t["energy"]["held_up"] = [{"service_id": DFS, "kwh": 100}]
    t["energy"]["merchant_kwh"] -= 100
    _refused(_sealed(data), "has no sustain_min, so no battery slice")


def test_every_slice_and_commitment_is_for_a_declared_service_and_none_is_idle() -> None:
    data = load_ic3(HYBRID)
    t = _terms(data, "cm-day")
    t["discharge"]["services"][0]["service_id"] = "svc-undeclared"
    t["energy"]["held_up"][0]["service_id"] = "svc-undeclared"
    _refused(_sealed(data), "slice for undeclared service svc-undeclared")
    data = load_ic3(HYBRID)
    extra = copy.deepcopy(data["services"][0])
    extra["service_id"] = "svc-z-never-sliced"
    data["services"].append(extra)
    _refused(data, r"no slice or commitment anywhere: \['svc-z-never-sliced'\]")


def test_a_firm_import_limit_cannot_exceed_the_fail_safe_firm_limit() -> None:
    data = load_ic3(HYBRID)
    _terms(data, "base")["import_limit_kw"] = 10001
    _refused(_sealed(data), "firm import limit of 10001 kW exceeds the fail-safe")
    # A non-firm limit may (the fixture's night rests on the 20 MW non-firm tranche).
    night = _fixture(HYBRID).terms[-1]
    assert night.terms_id == "night-non-firm"
    assert night.terms.import_limit_kw > _fixture(HYBRID).fail_safe.firm_import_limit_kw


def test_the_fail_safe_floor_is_the_lowest_floor_in_force() -> None:
    data = load_ic3(HYBRID)
    data["fail_safe"]["resilience_floor_kwh"] = 900
    _refused(data, "lowest resilience floor in force")
    # Raising one cell's floor does not move it: the fail-safe is the lowest one.
    data = load_ic3(HYBRID)
    e = _terms(data, "cm-day")["energy"]
    e["resilience_floor_kwh"] += 10
    e["merchant_kwh"] -= 10
    assert _validate(_sealed(data)).fail_safe.resilience_floor_kwh == 1000


def test_the_basis_of_a_service_never_gates_its_partition() -> None:
    """D57: an assumed-approved or unknown service partitions exactly like an evidenced one, and a
    new revenue stream is a new string, never a contract change."""
    base = _fixture(HYBRID)
    for basis in (
        {"kind": "assumed_approved", "confirmation_ask": "synthetic fixture: confirm"},
        {"kind": "unknown", "reason": "synthetic fixture: not known"},
    ):
        data = load_ic3(HYBRID)
        data["services"][0]["basis"] = basis
        data["services"][0]["stream"] = "A_STREAM_NOT_YET_NAMED"
        env = _validate(data)
        assert env.resolved_hash == base.resolved_hash
        assert [n.terms for n in env.terms] == [n.terms for n in base.terms]


# ------------------------------------------------------------------ the time base


def _oracle(data: dict[str, Any], instant: datetime) -> str:
    """The terms id in force at ``instant``, read straight off the JSON (independent of the
    module): an override if one holds it, else the cell of the local season, weekday and minute."""
    for o in data["overrides"]:
        if _at(o["start"]) <= instant < _at(o["end"]):
            return str(o["terms_id"])
    local = instant.astimezone(LONDON)
    minute = local.hour * 60 + local.minute
    season = next(s["season_id"] for s in data["seasons"] if local.month in s["months"])
    group = next(
        g["day_group_id"] for g in data["day_groups"] if local.isoweekday() in g["iso_weekdays"]
    )
    band = next(
        b["band_id"] for b in data["bands"] if b["start_minute"] <= minute < b["end_minute"]
    )
    return str(
        next(
            c["terms_id"]
            for c in data["cells"]
            if (c["season_id"], c["day_group_id"], c["band_id"]) == (season, group, band)
        )
    )


@pytest.mark.parametrize("name", ENVELOPES)
def test_the_resolver_matches_an_independent_oracle_at_every_half_hour(name: str) -> None:
    """Every UTC half-hour of the validity (a year, or the clock-change week), both clock changes
    included, against a reading that shares no code with `resolve`."""
    data = load_ic3(name)
    timeline = resolve(TimeBase.model_validate_json(json.dumps(data)))
    hashes = {
        n["terms_id"]: content_hash(
            dispatch_envelope.CellTerms.model_validate_json(json.dumps(n["terms"]))
        )
        for n in data["terms"]
    }
    steps = list(timeline.steps)
    k = 0
    for i in range(timeline.n_periods):
        while k + 1 < len(steps) and steps[k + 1].index <= i:
            k += 1
        instant = timeline.valid_from + i * timedelta(minutes=30)
        assert steps[k].terms_hash == hashes[_oracle(data, instant)], (name, _iso(instant))
    assert timeline.content_hash() == data["resolved_hash"]


def test_the_23_hour_day_has_a_six_hour_night_in_utc() -> None:
    env = _fixture(WEEK)
    night = _hash_of(env, "night")
    nights = [
        (_iso(s.start), _iso(s.end))
        for s in timeline_segments(resolve(env))
        if s.terms_hash == night
    ]
    assert nights == [
        ("2027-03-25T00:00:00Z", "2027-03-25T07:00:00Z"),
        ("2027-03-26T00:00:00Z", "2027-03-26T07:00:00Z"),
        ("2027-03-27T00:00:00Z", "2027-03-27T07:00:00Z"),
        ("2027-03-28T00:00:00Z", "2027-03-28T06:00:00Z"),
        ("2027-03-28T23:00:00Z", "2027-03-29T06:00:00Z"),
        ("2027-03-29T23:00:00Z", "2027-03-30T06:00:00Z"),
        ("2027-03-30T23:00:00Z", "2027-03-31T06:00:00Z"),
        ("2027-03-31T23:00:00Z", "2027-04-01T00:00:00Z"),
    ]
    assert resolve(env).n_periods == 7 * 48


def test_the_25_hour_day_has_an_eight_hour_night_in_utc() -> None:
    env = _fixture(HYBRID)
    night = _hash_of(env, "night-non-firm")
    segs = [s for s in timeline_segments(resolve(env)) if s.terms_hash == night]
    assert any(
        (_iso(s.start), _iso(s.end)) == ("2027-10-30T23:00:00Z", "2027-10-31T07:00:00Z")
        for s in segs
    )


def test_the_edge_reads_the_committed_timeline_and_trusts_it_by_hash() -> None:
    env = _fixture(WEEK)
    timeline = ResolvedTimeline.model_validate_json(
        (FIXTURES_IC3 / TIMELINE).read_text(encoding="utf-8")
    )
    assert timeline_matches(env, timeline) and timeline == resolve(env)
    for i in range(timeline.n_periods):
        t = timeline.valid_from + i * timedelta(minutes=30)
        assert timeline_terms_at(timeline, t) == terms_at(env, t)
    # A timeline edited and re-hashed consistently is still not this envelope's.
    data = json.loads((FIXTURES_IC3 / TIMELINE).read_text(encoding="utf-8"))
    data["steps"][1]["index"] += 1
    assert not timeline_matches(env, ResolvedTimeline.model_validate_json(json.dumps(data)))


@pytest.mark.parametrize(
    "edit,match",
    [
        (lambda d: d["steps"].__setitem__(0, {**d["steps"][0], "index": 1}), "index 0"),
        (lambda d: d["steps"].insert(2, d["steps"][1]), "strictly increase"),
        (lambda d: d["terms"][0]["terms"].__setitem__("export_limit_kw", 1), "not the hash"),
        (lambda d: d.__setitem__("n_periods", 335), "n_periods"),
        (lambda d: d["steps"].__setitem__(-1, {**d["steps"][-1], "index": 336}), "after the"),
        (lambda d: d["terms"].append(d["terms"][0]), "no repeats"),
    ],
    ids=["first-step", "repeat", "terms-hash", "n-periods", "past-end", "duplicate-terms"],
)
def test_a_malformed_timeline_is_refused(edit: Callable[[Any], None], match: str) -> None:
    data = json.loads((FIXTURES_IC3 / TIMELINE).read_text(encoding="utf-8"))
    edit(data)
    with pytest.raises(ValidationError, match=match):
        ResolvedTimeline.model_validate_json(json.dumps(data))


def test_the_resolved_hash_must_be_the_compiled_timeline() -> None:
    data = load_ic3(HYBRID)
    data["resolved_hash"] = "sha256:" + "4" * 64
    _refused(data, "resolved_hash does not match the compiled timeline")


@pytest.mark.parametrize(
    "edit,match",
    [
        (lambda d: d["seasons"][0]["months"].remove(10), "partition the twelve months"),
        (lambda d: d["seasons"].reverse(), "sorted by their first month"),
        (lambda d: d["day_groups"][1]["iso_weekdays"].remove(7), "partition the week"),
        (lambda d: d["bands"][1].__setitem__("start_minute", 450), "partition the day"),
        (lambda d: d["cells"].pop(), "one entry per"),
        (lambda d: d["cells"].reverse(), "one entry per"),
        (lambda d: d["cells"][1].__setitem__("terms_id", "no-such-terms"), "undeclared terms"),
        (lambda d: d["terms"].reverse(), "sorted by terms_id"),
        (lambda d: d.__setitem__("valid_from", "2027-01-01T00:15:00Z"), "30-minute UTC grid"),
        (lambda d: d.__setitem__("valid_until", "2027-01-01T00:00:00Z"), "after valid_from"),
        (lambda d: d["services"].reverse(), "sorted by service_id"),
    ],
    ids=[
        "month-missing",
        "seasons-unsorted",
        "weekday-missing",
        "band-gap",
        "cell-missing",
        "cells-unordered",
        "undeclared-terms",
        "terms-unsorted",
        "off-grid",
        "empty-validity",
        "services-unsorted",
    ],
)
def test_the_template_is_complete_and_canonical(
    edit: Callable[[dict[str, Any]], None], match: str
) -> None:
    data = load_ic3(HYBRID)
    edit(data)
    _refused(data, match)


def test_a_redundant_band_day_group_or_season_is_refused() -> None:
    """One encoding per policy, so two envelopes with the same policy hash the same."""
    data = load_ic3(DC)
    data["bands"][0:1] = [
        {"band_id": "off-peak", "start_minute": 0, "end_minute": 480},
        {"band_id": "off-peak-2", "start_minute": 480, "end_minute": 960},
    ]
    data["cells"] = _ic3_fixtures._cells(
        data["seasons"],
        data["day_groups"],
        data["bands"],
        {
            "winter": {"all-week": ["base", "base", "winter-peak", "winter-dfs", "base"]},
            "shoulder": {"all-week": ["base", "base", "base", "shoulder-dfs", "base"]},
            "summer": {"all-week": ["base", "base", "base", "base", "base"]},
        },
    )
    _refused(data, "bands off-peak and off-peak-2 carry the same terms everywhere")
    data = load_ic3(HYBRID)
    for c in data["cells"]:
        if c["season_id"] == "winter" and c["day_group_id"] == "weekend":
            c["terms_id"] = next(
                x["terms_id"]
                for x in data["cells"]
                if (x["season_id"], x["day_group_id"], x["band_id"])
                == ("winter", "weekday", c["band_id"])
            )
    _refused(data, "day groups weekday and weekend carry the same terms everywhere")
    data = load_ic3(HYBRID)
    winter = {(c["day_group_id"], c["band_id"]): c["terms_id"] for c in data["cells"][:8]}
    for c in data["cells"][8:]:
        c["terms_id"] = winter[(c["day_group_id"], c["band_id"])]
    _refused(data, "seasons winter and summer carry the same terms everywhere")


@pytest.mark.parametrize(
    "overrides,match",
    [
        ([("2027-07-06T07:15:00Z", "2027-07-06T15:00:00Z", "maintenance")], "30-minute UTC grid"),
        ([("2027-07-06T15:00:00Z", "2027-07-06T07:00:00Z", "maintenance")], "after start"),
        ([("2027-12-31T23:00:00Z", "2028-01-01T00:30:00Z", "maintenance")], "outside the"),
        (
            [
                ("2027-07-06T07:00:00Z", "2027-07-06T15:00:00Z", "maintenance"),
                ("2027-07-06T14:00:00Z", "2027-07-06T16:00:00Z", "base"),
            ],
            "overlap",
        ),
        (
            [
                ("2027-07-07T07:00:00Z", "2027-07-07T15:00:00Z", "maintenance"),
                ("2027-07-06T07:00:00Z", "2027-07-06T15:00:00Z", "maintenance"),
            ],
            "sorted by start",
        ),
        (
            [
                ("2027-07-06T07:00:00Z", "2027-07-06T15:00:00Z", "maintenance"),
                ("2027-07-06T15:00:00Z", "2027-07-06T17:00:00Z", "maintenance"),
            ],
            "adjacent with the same terms",
        ),
    ],
    ids=["off-grid", "reversed", "outside", "overlap", "unsorted", "adjacent-same"],
)
def test_overrides_are_on_the_grid_inside_the_validity_sorted_and_disjoint(
    overrides: list[tuple[str, str, str]], match: str
) -> None:
    data = load_ic3(HYBRID)
    data["overrides"] = [
        {
            "override_id": f"ovr-{i}",
            "start": start,
            "end": end,
            "terms_id": terms_id,
            "reason": "synthetic fixture: test",
        }
        for i, (start, end, terms_id) in enumerate(overrides)
    ]
    _refused(data, match)


def test_an_override_replaces_the_cell_wholesale() -> None:
    env = _fixture(HYBRID)
    inside, before = _at("2027-07-06T07:00:00Z"), _at("2027-07-06T06:30:00Z")
    assert terms_at(env, inside) == next(n.terms for n in env.terms if n.terms_id == "maintenance")
    assert isinstance(terms_at(env, before).discharge.withheld, NotApplicable)
    limits = flat_limits(terms_at(env, inside))
    assert (limits.merchant_discharge_kw, limits.soe_ceiling_kwh) == (2000, 5000)
    assert flat_limits(terms_at(env, _at("2027-07-06T15:00:00Z"))).soe_ceiling_kwh == 10000


# ------------------------------------------------------------------ thermal (D88)


def test_the_dc_commitments_are_provable_against_the_ic11_surface() -> None:
    assert check_against_surface(_fixture(DC), _surface(MODELLED_SURFACE)).ok


def test_a_cell_carries_thermal_terms_exactly_when_the_envelope_names_a_surface() -> None:
    data = load_ic3(HYBRID)
    _terms(data, "base")["thermal"] = {
        "kind": "thermal_terms",
        "band": {"point": "it_inlet", "min_tenths_c": 180, "max_tenths_c": 250},
        "commitments": [],
    }
    _refused(_sealed(data), "carries thermal terms exactly when the envelope names")
    data = load_ic3(DC)
    _terms(data, "base")["thermal"] = {"kind": "not_applicable", "reason": "synthetic: test"}
    _refused(_sealed(data), "carries thermal terms exactly when the envelope names")


def test_a_cell_band_sits_inside_the_binding_band_at_its_point() -> None:
    data = load_ic3(DC)
    _terms(data, "winter-dfs")["thermal"]["band"]["max_tenths_c"] = 260
    _refused(_sealed(data), "wider than the binding band")
    data = load_ic3(DC)
    _terms(data, "winter-dfs")["thermal"]["band"]["point"] = "supply_air"
    _refused(_sealed(data), "measured at the binding point")


def test_a_thermal_commitment_above_the_surface_is_refused() -> None:
    data = load_ic3(DC)
    _terms(data, "winter-dfs")["thermal"]["commitments"][0]["reduction_kw"] = 700
    env = _validate(_sealed(data))
    check = check_against_surface(env, _surface(MODELLED_SURFACE))
    assert check.reasons == (SurfaceReason.COMMITMENT_EXCEEDS_DELIVERABLE,)


def _dc_on_metered(valid_from: str, valid_until: str, *, commit_at_peak: bool) -> DispatchEnvelope:
    """The DC envelope re-pointed at the IC-11 metered-trial surface (another synthetic site)."""
    surface = _surface(METERED_SURFACE)
    data = load_ic3(DC)
    data["site_id"] = surface.site_id
    data["valid_from"], data["valid_until"] = valid_from, valid_until
    data["thermal"]["surface"]["availability_hash"] = surface.content_hash()
    data["thermal"]["binding"] = surface.limits.binding.model_dump(mode="json")
    for n in data["terms"]:
        n["terms"]["thermal"]["band"] = surface.limits.binding.model_dump(mode="json")
    if commit_at_peak:
        for c in data["cells"]:
            if c["season_id"] == "winter":
                c["terms_id"] = {"winter-peak": "winter-dfs", "winter-dfs": "winter-peak"}.get(
                    c["terms_id"], c["terms_id"]
                )
    return _validate(_sealed(data))


def test_a_commitment_that_runs_into_an_unavailable_cell_is_refused() -> None:
    env = _dc_on_metered("2027-01-01T00:00:00Z", "2027-04-01T00:00:00Z", commit_at_peak=True)
    check = check_against_surface(env, _surface(METERED_SURFACE))
    assert check.reasons == (SurfaceReason.COMMITMENT_IN_UNAVAILABLE,)


def test_a_commitment_in_an_unknown_cell_is_refused() -> None:
    env = _dc_on_metered("2026-10-01T00:00:00Z", "2027-01-01T00:00:00Z", commit_at_peak=False)
    check = check_against_surface(env, _surface(METERED_SURFACE))
    assert check.reasons == (SurfaceReason.COMMITMENT_IN_UNKNOWN,)


def test_the_surface_check_names_the_wrong_surface_and_no_surface() -> None:
    assert check_against_surface(_fixture(HYBRID), _surface(MODELLED_SURFACE)).reasons == (
        SurfaceReason.NO_THERMAL_SURFACE,
    )
    assert check_against_surface(_fixture(DC), _surface(METERED_SURFACE)).reasons == (
        SurfaceReason.WRONG_SITE_OR_SURFACE,
    )


# ------------------------------------------------------------------ labelling (D45, D46)


def test_a_live_envelope_may_hold_no_unknown_reference() -> None:
    data = load_ic3(HYBRID)
    data["record_class"], data["site_id"] = "live", "TEST-ONLY-NOT-A-SITE"
    _refused(_sealed(data), "live envelope may hold no unknown reference")
    data["record_class"] = "modelled"
    assert _validate(data).record_class == "modelled"


def test_synthetic_exactly_when_the_site_says_so() -> None:
    data = load_ic3(HYBRID)
    data["record_class"] = "modelled"
    _refused(data, "SYNTHETIC- exactly when the record is synthetic")
    data = load_ic3(HYBRID)
    data["site_id"] = "TEST-ONLY-NOT-A-SITE"
    _refused(_sealed(data), "SYNTHETIC- exactly when the record is synthetic")


def test_an_underwriting_label_cannot_name_a_study_basis() -> None:
    with pytest.raises(ValidationError, match="never presented as underwriting"):
        UnderwritingBasisLabel.model_validate_json(
            json.dumps({"kind": "underwriting", "basis_id": "all_streams_enabled_study_v1"})
        )
    ok = UnderwritingBasisLabel.model_validate_json(
        json.dumps({"kind": "underwriting", "basis_id": "underwriting_basis_v1"})
    )
    data = load_ic3(HYBRID)
    data["source"]["basis"] = ok.model_dump(mode="json")
    assert basis_banner(_validate(data)) == "UNDERWRITING BASIS (underwriting_basis_v1)"


# ------------------------------------------------------------------ IC-1 and the constraint library


def test_the_import_limits_rest_on_the_named_ic1_agreement() -> None:
    hybrid = ConnectionAgreement.model_validate_json(IC1_HYBRID_V110.read_text(encoding="utf-8"))
    firm = ConnectionAgreement.model_validate_json(IC1_FIRM.read_text(encoding="utf-8"))
    assert check_against_agreement(_fixture(HYBRID), hybrid).ok
    assert check_against_agreement(_fixture(WEEK), firm).ok
    assert check_against_agreement(_fixture(HYBRID), firm).reasons == (
        AgreementReason.WRONG_SITE_OR_AGREEMENT,
    )
    assert check_against_agreement(_fixture(DC), firm).reasons == (
        AgreementReason.NO_AGREEMENT_REFERENCE,
    )
    data = load_ic3(HYBRID)
    data["fail_safe"]["firm_import_limit_kw"] = 10001
    assert check_against_agreement(_validate(data), hybrid).reasons == (
        AgreementReason.FAIL_SAFE_ABOVE_FIRM,
    )
    data = load_ic3(HYBRID)
    _terms(data, "night-non-firm")["import_limit_kw"] = 30001
    assert check_against_agreement(_validate(_sealed(data)), hybrid).reasons == (
        AgreementReason.LIMIT_ABOVE_AGREEMENT,
    )


def test_megawatts_are_read_as_their_decimal_text() -> None:
    to_kw = dispatch_envelope._mw_to_kw_floor
    assert (to_kw(0.3), to_kw(10.0), to_kw(20.0), to_kw(0.0005), to_kw(1.2345)) == (
        300,
        10000,
        20000,
        0,
        1234,
    )


def test_the_constraint_library_pin_is_compared_never_assumed() -> None:
    dc = _fixture(DC)
    assert check_constraint_library(
        dc, library_version="0.0.0-synthetic", library_hash="x"
    ).status == ("unverifiable")
    assert check_constraint_library(dc, library_version="1.0.0", library_hash="x").status == (
        "mismatch"
    )
    assert check_constraint_library(
        _fixture(HYBRID), library_version="0.0.0-synthetic", library_hash="x"
    ).status == ("unverifiable")


# ------------------------------------------------------------------ views and references


def test_the_flat_and_per_service_views_of_a_winter_peak() -> None:
    env = _fixture(HYBRID)
    peak = _at("2027-01-04T16:00:00Z")  # a Monday, 16:00 GMT
    t = terms_at(env, peak)
    limits = flat_limits(t)
    assert (limits.import_limit_kw, limits.export_limit_kw) == (10000, 5000)
    assert (limits.merchant_discharge_kw, limits.merchant_charge_kw) == (1500, 5000)
    assert (limits.soe_floor_kwh, limits.soe_ceiling_kwh) == (6000, 10000)
    assert merchant_headroom(t, Direction.DISCHARGE) == 1500
    assert reserved_kw(t, Direction.DISCHARGE) == 3500 and reserved_kw(t, Direction.CHARGE) == 0
    assert offered_kw(env, CM, peak).discharge_kw == 1000
    assert offered_kw(env, FLEX, peak).discharge_kw == 2000
    assert offered_kw(env, FLEX, _at("2027-01-04T19:00:00Z")).discharge_kw == 0
    with pytest.raises(ValueError, match="not a declared service"):
        offered_kw(env, "svc-unknown", peak)
    assert fail_safe_terms(env).markets == "declared_unavailable"


def test_service_windows_merge_adjacent_cells_with_the_same_offer() -> None:
    env = _fixture(HYBRID)
    cm = service_windows(env, CM)
    # Friday 1 January 2027: day, peak and evening all offer CM 1000 kW, so one window.
    assert (_iso(cm[0].start), _iso(cm[0].end)) == ("2027-01-01T07:00:00Z", "2027-01-02T00:00:00Z")
    assert cm[0].offer.discharge_kw == 1000
    flex = service_windows(env, FLEX)
    assert (_iso(flex[0].start), _iso(flex[0].end)) == (
        "2027-01-01T16:00:00Z",
        "2027-01-01T19:00:00Z",
    )
    dfs = service_windows(_fixture(DC), DFS)
    assert dfs[0].offer.thermal_reduction_kw == 400 and dfs[0].offer.discharge_kw == 0


def test_underwritten_value_is_carried_by_basis_never_blended() -> None:
    totals = underwritten_total(_fixture(HYBRID))
    assert (totals.evidenced_pence_per_year, totals.assumed_approved_pence_per_year) == (0, 0)
    assert totals.unknown_services == (CM,) and totals.not_applicable_services == (FLEX,)
    data = load_ic3(HYBRID)
    data["services"][0]["underwritten"] = {
        "kind": "underwritten_value",
        "pence_per_year": 1,
        "value_basis": {"kind": "assumed_approved", "confirmation_ask": "synthetic: test"},
    }
    totals = underwritten_total(_validate(data))
    assert (totals.evidenced_pence_per_year, totals.assumed_approved_pence_per_year) == (0, 1)


def test_basis_labels_read_evidenced_assumed_or_unknown() -> None:
    labels = [basis_label(s) for s in _fixture(HYBRID).services]
    assert labels[0].startswith("evidenced: IC-4 sha256:")
    assert labels[1].startswith("assumed approved, to confirm: ")
    week_cm = _fixture(WEEK).services[0]
    assert isinstance(week_cm, CommittedService) and basis_label(week_cm).startswith("unknown: ")


def test_active_at_follows_supersession_and_refuses_ambiguity() -> None:
    first, second = _fixture(HYBRID), _fixture(SUCCESSOR)
    assert second.supersedes == first.content_hash()
    assert active_at([second, first], _at("2027-03-01T00:00:00Z")) == first
    assert active_at([first, second, first], _at("2027-07-01T00:00:00Z")) == second
    assert isinstance(active_at([first, second], _at("2026-12-31T23:30:00Z")), NotApplicable)
    with pytest.raises(ValueError, match="one site's envelopes"):
        active_at([first, _fixture(WEEK)], _at("2027-03-26T00:00:00Z"))
    with pytest.raises(ValueError, match="timezone-aware"):
        active_at([first], datetime(2027, 3, 1))


def test_two_envelopes_in_force_with_no_supersession_are_refused() -> None:
    first = _fixture(HYBRID)
    data = load_ic3(SUCCESSOR)
    data["supersedes"] = {"kind": "not_applicable", "reason": "synthetic: test"}
    rival = _validate(data)
    assert active_at([first, rival], _at("2027-03-01T00:00:00Z")) == first
    with pytest.raises(ValueError, match="in force at"):
        active_at([first, rival], _at("2027-07-01T00:00:00Z"))


def test_a_successor_cannot_start_before_what_it_supersedes() -> None:
    first = _fixture(SUCCESSOR)
    data = load_ic3(HYBRID)
    data["supersedes"] = first.content_hash()
    early = _validate(data)
    with pytest.raises(ValueError, match="starts before the envelope it supersedes"):
        active_at([first, early], _at("2027-07-01T00:00:00Z"))


# ------------------------------------------------------------------ schedules (the edge's check)


def _period(start: str, minutes: int = 30, **fields: Any) -> ScheduledPeriod:
    data = {
        "start": start,
        "end": _iso(_at(start) + timedelta(minutes=minutes)),
        "merchant_discharge_kw": 0,
        "merchant_charge_kw": 0,
        "service_delivery": [],
        "site_import_kw": 0,
        "site_export_kw": 0,
        "soe_start_kwh": 7000,
        "soe_end_kwh": 7000,
        "temperature": {"kind": "not_applicable", "reason": "no cooling plant"},
        **fields,
    }
    return ScheduledPeriod.model_validate_json(json.dumps(data))


def _codes(env: DispatchEnvelope, *periods: ScheduledPeriod) -> list[str]:
    return [v.code.value for v in check_schedule(env, periods).violations]


PEAK = "2027-01-04T16:00:00Z"


def test_a_schedule_inside_the_envelope_passes() -> None:
    env = _fixture(HYBRID)
    ok = _period(
        PEAK,
        merchant_discharge_kw=1500,
        service_delivery=[
            {"service_id": CM, "direction": "discharge", "kw": 1000},
            {"service_id": FLEX, "direction": "discharge", "kw": 2000},
        ],
        site_export_kw=4500,
        soe_start_kwh=9000,
        soe_end_kwh=6750,
    )
    assert check_schedule(env, [ok, _period("2027-01-04T16:30:00Z", minutes=5)]).ok


@pytest.mark.parametrize(
    "fields,code",
    [
        ({"site_import_kw": 10001}, "import_limit"),
        ({"site_export_kw": 5001}, "export_limit"),
        ({"soe_end_kwh": 999}, "soe_below_resilience_floor"),
        ({"soe_end_kwh": 5999}, "soe_below_service_floor"),
        ({"soe_end_kwh": 10001}, "soe_above_ceiling"),
        (
            {"service_delivery": [{"service_id": CM, "direction": "discharge", "kw": 1001}]},
            "service_over_slice",
        ),
        (
            {"service_delivery": [{"service_id": CM, "direction": "charge", "kw": 1}]},
            "service_over_slice",
        ),
        ({"merchant_discharge_kw": 1501}, "merchant_over_slice"),
    ],
)
def test_each_breach_of_the_envelope_is_named(fields: dict[str, Any], code: str) -> None:
    assert _codes(_fixture(HYBRID), _period(PEAK, **fields)) == [code]


def test_violations_come_most_severe_first() -> None:
    env = _fixture(HYBRID)
    merchant = _period(PEAK, merchant_discharge_kw=1501)
    worse = _period("2027-01-04T16:30:00Z", site_import_kw=10001, soe_end_kwh=900)
    outside = _period("2028-01-01T00:00:00Z")
    found = check_schedule(env, [merchant, worse, outside]).violations
    assert [(v.period, v.code.value) for v in found] == [
        (2, "outside_validity"),
        (1, "import_limit"),
        (1, "soe_below_resilience_floor"),
        (0, "merchant_over_slice"),
    ]
    assert [v.rank for v in found] == sorted(v.rank for v in found)
    assert RANK[ViolationCode.THERMAL_BAND] < RANK[ViolationCode.IMPORT_LIMIT]


def test_a_thermal_cell_needs_a_stated_temperature_inside_its_band() -> None:
    env = _fixture(DC)
    window = "2027-01-04T19:00:00Z"  # a winter turn-down window: band 18.0-24.0 °C

    def dc(start: str, **fields: Any) -> ScheduledPeriod:
        return _period(start, soe_start_kwh=1000, soe_end_kwh=1000, **fields)

    def temp(low: int, high: int, point: str = "it_inlet") -> dict[str, Any]:
        return {"point": point, "low_tenths_c": low, "high_tenths_c": high}

    assert _codes(env, dc(window)) == ["thermal_not_stated"]
    assert _codes(env, dc(window, temperature=temp(200, 240))) == []
    assert _codes(env, dc(window, temperature=temp(200, 245))) == ["thermal_band"]
    assert _codes(env, dc(window, temperature=temp(200, 220, "supply_air"))) == ["thermal_band"]
    assert _codes(env, dc("2027-01-04T12:00:00Z", temperature=temp(200, 245))) == []


def test_a_period_lies_inside_one_half_hour_of_the_grid() -> None:
    with pytest.raises(ValidationError, match="inside one half-hour"):
        _period("2027-01-04T16:15:00Z", minutes=30)
    with pytest.raises(ValidationError, match="whole minutes"):
        _period("2027-01-04T16:00:30Z", minutes=5)


# ------------------------------------------------------------------ mutation checks

#: (guard, exact source text, its in-memory replacement, the test that must go red).
MUTATIONS = [
    (
        "an envelope needs an input: not the hash of nothing",
        "        if self.input_hash == EMPTY_SHA256:\n",
        "        if False:\n",
        "test_an_input_hash_of_nothing_is_invalid",
    ),
    (
        "the quote's input hash is the envelope's",
        "        if self.assessment.quote_input_hash != self.input_hash:\n",
        "        if False:\n",
        "test_the_quote_input_hash_must_be_the_envelope_input_hash",
    ),
    (
        "run_id re-derives from the pins",
        "        if r.run_id != expected:\n",
        "        if False:\n",
        "test_run_id_must_derive_from_the_run_pins",
    ),
    (
        "the discharge partition is exact (no double-booking)",
        "            if t.discharge.total_kw() != self.rated_discharge_kw:\n",
        "            if False:\n",
        "test_the_discharge_partition_sums_to_the_rating",
    ),
    (
        "held energy covers slice x sustain",
        "                    if h.kwh * 60 < s.kw * sustain:\n",
        "                    if False:\n",
        "test_held_energy_covers_each_slice_for_its_sustain_time",
    ),
    (
        "a firm limit is within the fail-safe firm limit",
        "                and t.import_limit_kw > self.fail_safe.firm_import_limit_kw\n",
        "                and False\n",
        "test_a_firm_import_limit_cannot_exceed_the_fail_safe_firm_limit",
    ),
    (
        "the fail-safe floor is the lowest floor in force",
        "        if self.fail_safe.resilience_floor_kwh != min(floors):\n",
        "        if False:\n",
        "test_the_fail_safe_floor_is_the_lowest_floor_in_force",
    ),
    (
        "resolved_hash is the compiled timeline's",
        "        if timeline.content_hash() != self.resolved_hash:\n",
        "        if False:\n",
        "test_the_resolved_hash_must_be_the_compiled_timeline",
    ),
    (
        "the resolver walks a clock change a half-hour at a time",
        "        if nxt.astimezone(_TZ).utcoffset() != t.astimezone(_TZ).utcoffset():\n",
        "        if False:\n",
        "test_the_resolver_matches_an_independent_oracle_at_every_half_hour",
    ),
    (
        "overrides do not overlap",
        "        if b.start < a.end:\n",
        "        if False:\n",
        "test_overrides_are_on_the_grid_inside_the_validity_sorted_and_disjoint",
    ),
    (
        "a cell band sits inside the binding band",
        "                if not cell.band.within(th.binding):\n",
        "                if False:\n",
        "test_a_cell_band_sits_inside_the_binding_band_at_its_point",
    ),
    (
        "thermal terms exactly when there is a surface",
        "            if isinstance(th, ThermalEnvelope) != isinstance(cell, ThermalTerms):\n",
        "            if False:\n",
        "test_a_cell_carries_thermal_terms_exactly_when_the_envelope_names_a_surface",
    ),
    (
        "a commitment above the surface's firm kW is refused",
        "                if total > kw:\n",
        "                if False:\n",
        "test_a_thermal_commitment_above_the_surface_is_refused",
    ),
    (
        "a commitment may not run into an unavailable cell",
        "                reasons.add(SurfaceReason.COMMITMENT_IN_UNAVAILABLE)\n",
        "                pass\n",
        "test_a_commitment_that_runs_into_an_unavailable_cell_is_refused",
    ),
    (
        "a live envelope holds no unknown reference",
        "        if self.record_class is RecordClass.LIVE:\n",
        "        if False:\n",
        "test_a_live_envelope_may_hold_no_unknown_reference",
    ),
    (
        "a study basis is never underwriting (D46)",
        '        if "study" in self.basis_id:\n',
        "        if False:\n",
        "test_an_underwriting_label_cannot_name_a_study_basis",
    ),
    (
        "a schedule below the resilience floor is named as such",
        "    if low < t.energy.resilience_floor_kwh:\n",
        "    if False:\n",
        "test_each_breach_of_the_envelope_is_named",
    ),
    (
        "two envelopes in force at once are refused",
        "    if len(live) > 1:\n",
        "    if False:\n",
        "test_two_envelopes_in_force_with_no_supersession_are_refused",
    ),
]


def _run_mutant(old: str, new: str, test: str) -> int:
    return run_mutant(
        "gridq.contracts.dispatch_envelope", old, new, f"{Path(__file__).resolve()}::{test}"
    )


@pytest.mark.parametrize("test", sorted({m[3] for m in MUTATIONS}))
def test_mutation_runner_passes_on_the_unmutated_source(test: str) -> None:
    """Control: the harness itself is sound, so a red mutant below means the guard bit."""
    assert _run_mutant("", "", test) == 0, test


@pytest.mark.parametrize("guard,old,new,test", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_reverting_a_guard_turns_its_test_red(guard: str, old: str, new: str, test: str) -> None:
    source = Path(dispatch_envelope.__file__).read_text(encoding="utf-8")
    assert source.count(old) == 1, guard
    # Exit 1 is pytest's "tests ran and failed": the guard is load-bearing. A mutant that cannot
    # load exits 3, and a collection error exits 2, so neither passes as red.
    assert _run_mutant(old, new, test) == 1, guard


def test_the_placeholder_hash_is_the_ic11_pattern() -> None:
    text = "SYNTHETIC PLACEHOLDER: x"
    assert (
        _ic3_fixtures._placeholder_hash("x")
        == "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    )
