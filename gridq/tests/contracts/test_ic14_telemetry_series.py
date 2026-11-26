"""IC-14 telemetry series v1.0.0 conformance (roadmap WP-TEL-01, NEW-IC-14).

Acceptance:
1. The contract version is tagged (annotated git tag) and the conformance tests pass: the four
   frozen schemas, the generated specification, the pinned fixture hashes, a gateway that validates
   from the schema files alone, and a consumer that runs on the fixtures without the producer.
2. A missing sample is represented as a gap with a quality flag, never as zero: property tests
   P1 to P14 over the producer's block, the curated transforms (resampling and aggregation at all
   three curated resolutions), the tracker and the data-age reader, each over seeded random input.

Plus the design's guards (D148 to D153): names and units, the time base, OPC UA quality
mapping, the raw and curated tiers, tenancy over IC-13, and IC-5 alignment; then mutation checks
proving each guard is load-bearing.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import random
import subprocess
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from functools import cache
from pathlib import Path
from typing import Annotated, Any

import _ic14_fixtures as fx
import pytest
from _contracts_support import (
    FIXTURES_IC14,
    GOLDEN_HASHES_IC5,
    GOLDEN_HASHES_IC14,
    IC14_FIXTURES,
    load_ic14,
    run_mutant,
)
from pydantic import Field, TypeAdapter, ValidationError

from gridq.contracts import __main__ as cli
from gridq.contracts import _specdoc, edge_cloud, telemetry_series
from gridq.contracts.__main__ import FROZEN, render, schema_path
from gridq.contracts._base import NotApplicable, not_applicable
from gridq.contracts.audit import GENESIS_PREV_HASH, AuditAction, audit_access
from gridq.contracts.dispatch_envelope import RESOLUTION_MIN
from gridq.contracts.evidence import EvidenceRef
from gridq.contracts.identity import IdentityClaims, ResourceKind, ResourceRef
from gridq.contracts.telemetry_series import (
    BLOCK_SPAN_MS,
    CONTRACT_VERSION,
    GIT_TAG,
    PERIOD_MS,
    QUALITY_MAP,
    QUANTITY_TABLE,
    SUPPORTED_VERSIONS,
    TRIGGER_GAP_REASON,
    AgeReading,
    Aggregate,
    Aspect,
    AssetKind,
    BatchRefused,
    ChainBroken,
    CuratedGap,
    CuratedSeries,
    CurationMethod,
    DqAnnotation,
    GapReason,
    GapRun,
    MeasurementRole,
    MixedTenants,
    NotCertified,
    PointName,
    Quantity,
    ReadDecision,
    SeriesDescriptor,
    SlotGap,
    SlotValue,
    TelemetryBatch,
    TenantKey,
    TenantReadRefused,
    TrackState,
    TrackStatus,
    ValueQuality,
    age_at,
    check_batch,
    check_batch_size,
    check_supersession,
    clock_drift_ms,
    counterparts,
    curate,
    current_revision,
    effective_annotations,
    from_opcua,
    kw_to_w,
    kwh_to_wh,
    millideg_to_tenths_c,
    parse_point_key,
    point_key,
    read_guard,
    require_certified,
    resample,
    resource_ref,
    settlement_period_of,
    slot_instant,
    split_import_export_w,
    tenths_c_to_millideg,
    to_opcua,
    track,
    values_in,
    verify_batch_chain,
    verify_curated,
    w_to_kw,
    wh_to_kwh,
)

STEMS = (
    "ic14_series_descriptor",
    "ic14_telemetry_batch",
    "ic14_curated_series",
    "ic14_dq_annotation",
)
MODELS = (SeriesDescriptor, TelemetryBatch, CuratedSeries, DqAnnotation)
SPEC_STEM = "ic14_telemetry_series"
METER = "ic14_descriptor_site_power_meter.synthetic.json"
SCADA = "ic14_descriptor_site_power_scada.synthetic.json"
SOC = "ic14_descriptor_bess_soc.synthetic.json"
BATCHES = "ic14_batches_site_power_meter.synthetic.jsonl"
ANNOTATION = "ic14_annotation_flatline.synthetic.json"
REV0 = "ic14_curated_site_power_1min_rev0.synthetic.json"
REV1 = "ic14_curated_site_power_1min_rev1.synthetic.json"
DESCRIPTORS = (METER, SCADA, SOC)
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
FIXTURES = HERE / "fixtures"
WINDOW = fx.WINDOW
MS = timedelta(milliseconds=1)
RAW_GAPS = [g for g in GapReason if g is not GapReason.INSUFFICIENT_COVERAGE]


# ------------------------------------------------------------------ helpers
# Fixtures are read inside tests, never at import: a mutant that breaks loading must turn its test
# red, not fail collection.


def _model_for(name: str) -> type[Any]:
    for part, model in (
        ("_descriptor_", SeriesDescriptor),
        ("_batches_", TelemetryBatch),
        ("_annotation_", DqAnnotation),
        ("_curated_", CuratedSeries),
    ):
        if part in name:
            return model
    raise AssertionError(name)


@cache
def _descriptor(name: str = METER) -> SeriesDescriptor:
    return SeriesDescriptor.model_validate_json((FIXTURES_IC14 / name).read_text("utf-8"))


@cache
def _batches() -> tuple[TelemetryBatch, ...]:
    lines = (FIXTURES_IC14 / BATCHES).read_text("utf-8").splitlines()
    return tuple(TelemetryBatch.model_validate_json(line) for line in lines)


def _epoch(n: int) -> list[TelemetryBatch]:
    return [b for b in _batches() if b.stream_epoch == n]


@cache
def _annotation() -> DqAnnotation:
    return DqAnnotation.model_validate_json((FIXTURES_IC14 / ANNOTATION).read_text("utf-8"))


@cache
def _curated(name: str) -> CuratedSeries:
    return CuratedSeries.model_validate_json((FIXTURES_IC14 / name).read_text("utf-8"))


def _method(**changes: Any) -> CurationMethod:
    return CurationMethod.model_validate_json(json.dumps({**fx.method(), **changes}))


def _claims(name: str) -> IdentityClaims:
    path = FIXTURES / f"ic13_claims_{name}.synthetic.json"
    return IdentityClaims.model_validate_json(path.read_text("utf-8"))


def _tenant(org: str = fx.ORG, site: str = fx.SITE, kind: str = "customer") -> TenantKey:
    return TenantKey(kind="tenant_key", org_id=org, org_kind=kind, site_id=site)  # type: ignore[arg-type]


def _allowed() -> ReadDecision:
    """Neura ops may read every tenant's telemetry (its IC-13 fixture scope)."""
    return read_guard(_claims("neura_ops"), _tenant())


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _ms(t: datetime) -> int:
    return (t - datetime(1970, 1, 1, tzinfo=UTC)) // MS


def _walk(node: Any) -> Any:
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _cases(rows: list[tuple[Any, ...]]) -> list[Any]:
    """Parametrize rows whose first element is the case id (mutation checks name tests by it)."""
    return [pytest.param(*row, id=row[0]) for row in rows]


Slot = tuple[str, Any, Any]  # ("v", quality, value) or ("g", reason, None)


def _key(s: Slot) -> tuple[str, Any]:
    return (s[0], s[1])


def _segments(slots: list[Slot]) -> list[dict[str, Any]]:
    """Run-length encode a slot stream the way a producer must: maximal runs, every slot once."""
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(slots):
        j = i
        while j + 1 < len(slots) and _key(slots[j + 1]) == _key(slots[i]):
            j += 1
        kind, what, _ = slots[i]
        if kind == "g":
            out.append({"kind": "gap", "first_slot": i, "last_slot": j, "reason": str(what)})
        else:
            values = [s[2] for s in slots[i : j + 1]]
            out.append({"kind": "values", "first_slot": i, "values": values, "quality": str(what)})
        i = j + 1
    return out


def _batch_data(
    slots: list[Slot],
    origin: datetime,
    *,
    resolution: str = "operational_1s",
    seq: int = 0,
    prev: str = GENESIS_PREV_HASH,
    epoch: int = 0,
    synced: bool = True,
    point: str = fx.METER_POINT,
    tenant: dict[str, str] | None = None,
) -> dict[str, Any]:
    span = BLOCK_SPAN_MS[resolution]
    head = fx._head(point)
    if tenant is not None:
        head["tenant"], head["site_id"] = tenant, tenant["site_id"]
        head["series_id"] = f"{tenant['org_id']}/{tenant['site_id']}/{point}"
        head["record_class"] = (
            "synthetic_fixture" if tenant["org_id"].startswith("SYNTHETIC-") else "live"
        )
    return {
        **head,
        "kind": "telemetry_batch",
        "descriptor_hash": GOLDEN_HASHES_IC14[METER],
        "sender_id": fx.SENDER,
        "stream_epoch": epoch,
        "seq": seq,
        "prev_batch_hash": prev,
        "prior_epoch_head": (
            fx._na("epoch 0") if epoch == 0 else "sha256:" + hashlib.sha256(b"prior").hexdigest()
        ),
        "origin": _iso(origin),
        "resolution": resolution,
        "slot_count": span // PERIOD_MS[resolution],
        "segments": _segments(slots),
        "clock": {
            "kind": "clock_statement",
            "stamped_by": "edge_acquisition",
            "clock_state": "synchronised" if synced else "unsynchronised",
            "clock_offset_bound_ms": 20 if synced else fx._unknown("not synchronised"),
            "boot_id": "SYNTHETIC-BOOT-T",
            "mono_origin_ms": 0,
            "mono_end_ms": 0,
            "receipt_lag_ms": fx._na("edge stamped"),
        },
        "sealed_at": _iso(origin + span * MS),
        "ic5": fx._na("not sent under an IC-5 link in this test"),
    }


def _batch(slots: list[Slot], origin: datetime, **kw: Any) -> TelemetryBatch:
    return TelemetryBatch.model_validate_json(json.dumps(_batch_data(slots, origin, **kw)))


def _random_slots(rng: random.Random, n: int) -> list[Slot]:
    """A random slot stream: runs of good values (zeros included, as real readings), other
    qualities, and gaps of every raw reason."""
    out: list[Slot] = []
    while len(out) < n:
        length = rng.randint(1, max(1, n // 5))
        r = rng.random()
        if r < 0.25:
            out += [("g", rng.choice(RAW_GAPS), None)] * length
        else:
            q = ValueQuality.GOOD if r < 0.7 else rng.choice(list(ValueQuality))
            for _ in range(length):
                v = 0 if rng.random() < 0.2 else rng.randint(-(10**7), 10**7)
                out.append(("v", q, v))
    return out[:n]


def _expand(b: TelemetryBatch) -> list[Slot]:
    out: list[Slot] = []
    for s in b.segments:
        if isinstance(s, GapRun):
            out += [("g", s.reason, None)] * (s.last_slot - s.first_slot + 1)
        else:
            out += [("v", s.quality, v) for v in s.values]
    return out


#: (raw resolution, curated resolution): every curated resolution, each from a finer raw one.
CASCADES = (
    ("event_20hz", "operational_1s"),
    ("operational_1s", "standard_1min"),
    ("standard_1min", "settlement_30min"),
)


def _random_window(
    rng: random.Random, raw: str, curated: str, *, keep: float = 0.8
) -> tuple[datetime, list[TelemetryBatch]]:
    """One curated window's raw batches: a random subset of its blocks, each a random stream."""
    origin = datetime(2027, 1, 4, tzinfo=UTC) + rng.randrange(4) * BLOCK_SPAN_MS[curated] * MS
    n_blocks = BLOCK_SPAN_MS[curated] // BLOCK_SPAN_MS[raw]
    slots = BLOCK_SPAN_MS[raw] // PERIOD_MS[raw]
    batches = []
    for k in range(n_blocks):
        if n_blocks > 1 and rng.random() > keep:
            continue
        o = origin + k * BLOCK_SPAN_MS[raw] * MS
        synced = rng.random() > 0.1
        batches.append(
            _batch(_random_slots(rng, slots), o, resolution=raw, seq=k + 1, prev=_H, synced=synced)
        )
    return origin, batches


_H = "sha256:" + hashlib.sha256(b"a predecessor").hexdigest()


def _random_method(rng: random.Random, period: int, raw_period: int) -> CurationMethod:
    aggregation = rng.choice(["mean", "min", "max", "last"])
    usable = [ValueQuality.GOOD] + [
        q
        for q in ValueQuality
        if q not in (ValueQuality.GOOD, ValueQuality.HELD) and rng.random() < 0.5
    ]
    return _method(
        aggregation=aggregation,
        min_usable_ms=rng.choice([raw_period, period // 2, period]),
        usable=[q.value for q in usable],
        applied_findings=[],
        rounding=(
            rng.choice(["floor", "ceil", "half_even"])
            if aggregation == "mean"
            else fx._na("not a mean")
        ),
    )


def _oracle(
    batches: list[TelemetryBatch], method: CurationMethod, origin: datetime, curated: str
) -> list[dict[str, Any]]:
    """An independent slot-by-slot reading of the method, for comparison with `resample`."""
    period, base = PERIOD_MS[curated], _ms(origin)
    n = BLOCK_SPAN_MS[curated] // period
    cells: list[dict[str, Any]] = [
        {"values": [], "rejected": 0, "gaps": Counter()} for _ in range(n)
    ]
    for b in batches:
        raw_p = PERIOD_MS[b.resolution]
        for k, (kind, what, v) in enumerate(_expand(b)):
            cell = cells[(_ms(b.origin) + k * raw_p - base) // period]
            if b.clock.clock_state == "unsynchronised":
                cell["gaps"][GapReason.CLOCK_UNTRUSTED] += raw_p
            elif kind == "g":
                cell["gaps"][what] += raw_p
            elif what in method.usable:
                cell["values"].append((v, what))
            else:
                cell["rejected"] += raw_p
    raw_p = PERIOD_MS[batches[0].resolution] if batches else 1
    out = []
    for cell in cells:
        usable = len(cell["values"]) * raw_p
        gap = period - usable - cell["rejected"]
        missing = gap - sum(cell["gaps"].values())
        if missing:
            cell["gaps"][GapReason.NOT_RECORDED] += missing
        row = {"usable_ms": usable, "rejected_ms": cell["rejected"], "gap_ms": gap}
        if usable >= method.min_usable_ms and usable:
            vals = [v for v, _ in cell["values"]]
            exact = Fraction(sum(vals), len(vals))
            rounding = method.rounding if isinstance(method.rounding, str) else "floor"
            means = {
                "floor": math.floor(exact),
                "ceil": math.ceil(exact),
                "half_even": round(exact),
            }
            row["value"] = {
                "min": min(vals),
                "max": max(vals),
                "last": vals[-1],
                "mean": means[rounding],
            }[method.aggregation]
            order = list(ValueQuality)
            row["quality"] = max((q for _, q in cell["values"]), key=order.index)
        else:
            gaps = cell["gaps"]
            row["reason"] = (
                next(iter(gaps))
                if gap == period and len(gaps) == 1
                else GapReason.INSUFFICIENT_COVERAGE
            )
        out.append(row)
    return out


# ------------------------------------------------------------------ 1. version tagged, conformance


def test_the_contract_version_is_tagged() -> None:
    assert CONTRACT_VERSION == "1.0.0"
    assert SUPPORTED_VERSIONS == ("1.0.0",) and SUPPORTED_VERSIONS[-1] == CONTRACT_VERSION
    assert GIT_TAG == f"contracts/ic14-telemetry-series/v{CONTRACT_VERSION}"
    assert telemetry_series.CONTRACT_ID == "IC-14"
    assert "SUPPORTED_VERSIONS" in telemetry_series.__all__


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
    paths.append(_specdoc.spec_path(SPEC_STEM, CONTRACT_VERSION))
    paths.append(Path(telemetry_series.__file__))
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        at_tag = git("show", f"{GIT_TAG}:{rel}")
        assert at_tag.returncode == 0, rel
        assert at_tag.stdout == path.read_text(encoding="utf-8"), rel


@pytest.mark.parametrize("stem,model", list(zip(STEMS, MODELS, strict=True)))
def test_the_frozen_schema_is_committed_and_matches_the_model(stem: str, model: type) -> None:
    assert (stem, model, CONTRACT_VERSION) in FROZEN
    path = schema_path(stem, CONTRACT_VERSION)
    assert path.read_text(encoding="utf-8") == render(model)


@pytest.mark.parametrize("path", IC14_FIXTURES, ids=lambda p: p.name)
def test_every_fixture_validates_at_1_0_0_with_its_pinned_hash(path: Path) -> None:
    model = _model_for(path.name)
    lines = path.read_text(encoding="utf-8").splitlines()
    texts = lines if path.suffix == ".jsonl" else ["\n".join(lines)]
    objs = [model.model_validate_json(t) for t in texts]
    for obj in objs:
        assert (obj.contract, obj.contract_version) == ("IC-14", CONTRACT_VERSION)
        again = model.model_validate_json(obj.model_dump_json(indent=2))
        assert again == obj and again.content_hash() == obj.content_hash()
    assert objs[-1].content_hash() == GOLDEN_HASHES_IC14[path.name]


def test_each_chain_line_is_the_canonical_json_of_its_batch() -> None:
    from gridq.contracts._base import canonical_json

    for line, batch in zip(
        (FIXTURES_IC14 / BATCHES).read_text("utf-8").splitlines(), _batches(), strict=True
    ):
        assert line == canonical_json(batch)
        assert "sha256:" + hashlib.sha256(line.encode()).hexdigest() == batch.content_hash()


@pytest.mark.parametrize("name", [*DESCRIPTORS, ANNOTATION, REV0])
@pytest.mark.parametrize(
    "field,value",
    [
        ("contract_version", "1.0.1"),
        ("contract_version", "1.1.0"),
        ("contract_version", "2.0.0"),
        ("contract", "IC-5"),
        ("contract", "IC-13"),
    ],
)
def test_another_version_or_contract_is_refused(name: str, field: str, value: str) -> None:
    data = load_ic14(name)
    data[field] = value
    with pytest.raises(ValidationError):
        _model_for(name).model_validate_json(json.dumps(data))


def test_the_committed_fixtures_equal_a_fresh_build() -> None:
    built = fx.build_all()
    assert sorted(p.name for p in built) == sorted(GOLDEN_HASHES_IC14)
    assert sorted(p.name for p in IC14_FIXTURES) == sorted(GOLDEN_HASHES_IC14)
    for path, text in built.items():
        assert path.read_text(encoding="utf-8") == text, path.name


# ------------------------------------------------------------------ the published specification


def _spec_text() -> str:
    return _specdoc.spec_path(SPEC_STEM, CONTRACT_VERSION).read_text(encoding="utf-8")


def test_the_committed_specification_is_a_fresh_render() -> None:
    rendered = dict(cli.render_specs(cli.SCHEMA_DIR, _specdoc.SPEC_DIR))
    path = _specdoc.spec_path(SPEC_STEM, CONTRACT_VERSION)
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
    assert doc.startswith("# IC-14 specification, version 1.0.0\n")
    assert f"| git tag | `{GIT_TAG}` |" in doc and "Do not edit" in doc
    for name in telemetry_series.__all__:
        obj = getattr(telemetry_series, name)
        if callable(obj) and name[0].islower():
            assert f"### `{name}(" in doc, name
    # What a consumer cannot infer from the schema: a gap is never a zero, the sign convention,
    # and that the GB clock never appears on the wire.
    assert "A missing sample is a gap, never a zero" in doc
    assert "load convention" in doc and "The GB clock never appears on the wire" in doc


def test_check_fails_when_the_specification_drifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    specs = tmp_path / "specs"
    specs.mkdir()
    for other in _specdoc.SPEC_DIR.glob("*.md"):
        (specs / other.name).write_text(other.read_text(encoding="utf-8"), encoding="utf-8")
    name, committed = _specdoc.spec_path(SPEC_STEM, CONTRACT_VERSION).name, _spec_text()
    drifted = committed.replace("never a zero", "or a zero", 1)
    (specs / name).write_text(drifted, encoding="utf-8")
    monkeypatch.setattr(_specdoc, "SPEC_DIR", specs)
    assert cli.main(["--check"]) == 1
    assert f"DRIFT: {name} is not the specification its contract renders" in capsys.readouterr().out
    (specs / name).write_text(committed, encoding="utf-8")
    assert cli.main(["--check"]) == 0


# ------------------------------------------------------------------ gateways without the producer

#: A gateway with no GridQ install: the committed schema files and the standard library only.
_SCHEMA_ONLY = """
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from _schema_subset import SchemaError, validate
schemas, fixtures = pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
load = lambda p: json.loads(p.read_text(encoding="utf-8"))
stem = {"descriptor": "ic14_series_descriptor", "batches": "ic14_telemetry_batch",
        "annotation": "ic14_dq_annotation", "curated": "ic14_curated_series"}
schema = {k: load(schemas / f"{v}.v1.0.0.schema.json") for k, v in stem.items()}
n = 0
for p in sorted(fixtures.glob("ic14_*.synthetic.json*")):
    kind = next(k for k in stem if f"_{k}_" in p.name)
    lines = p.read_text().splitlines() if p.suffix == ".jsonl" else [p.read_text()]
    for line in lines:
        validate(json.loads(line), schema[kind])
        n += 1
batch = json.loads((fixtures / "ic14_batches_site_power_meter.synthetic.jsonl").read_text()
                   .splitlines()[7])
desc = load(fixtures / "ic14_descriptor_site_power_meter.synthetic.json")
def gap_with_a_value(b):
    b["segments"][1]["values"] = [0] * 20
def aspect_outside_the_table(d):
    d["name"]["aspect"] = "phase_l1"
    d["name"]["quantity"] = "frequency"
refused = []
for label, doc, kind, edit in (
    ("a gap carrying a value", batch, "batches", gap_with_a_value),
    ("a value that is not an integer", batch, "batches",
     lambda b: b["segments"][0]["values"].__setitem__(0, 1.5)),
    ("a value beyond 2^53 - 1", batch, "batches",
     lambda b: b["segments"][0]["values"].__setitem__(0, 2**53)),
    ("an unknown quality", batch, "batches", lambda b: b["segments"][0].update(quality="bad")),
    ("an unknown gap reason", batch, "batches", lambda b: b["segments"][1].update(reason="x")),
    ("null", batch, "batches", lambda b: b.update(seq=None)),
    ("unknown field", batch, "batches", lambda b: b.update(extra=1)),
    ("naive time", batch, "batches", lambda b: b.update(origin="2027-01-04T16:07:00")),
    ("another version", batch, "batches", lambda b: b.update(contract_version="1.1.0")),
    ("bool for int", batch, "batches", lambda b: b.update(seq=True)),
    ("a quantity on an aspect outside the table", desc, "descriptor", aspect_outside_the_table),
    ("an unknown unit", desc, "descriptor", lambda d: d.update(unit="kW")),
    ("a tenant org with a slash", desc, "descriptor",
     lambda d: d["tenant"].update(org_id="SYNTHETIC-A/B")),
):
    bad = json.loads(json.dumps(doc))
    edit(bad)
    try:
        validate(bad, schema[kind])
    except SchemaError:
        refused.append(label)
print(json.dumps({
    "validated": n,
    "refused": refused,
    "gridq": sorted(m for m in sys.modules if m == "gridq" or m.startswith("gridq.")),
    "pydantic": "pydantic" in sys.modules,
}))
"""


def test_a_gateway_validates_ic14_from_the_json_schema_files_alone() -> None:
    """No GridQ, no pydantic, no jsonschema; the stdlib only. A gap run has no value field."""
    res = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _SCHEMA_ONLY,
            str(HERE),
            str(schema_path(STEMS[0], CONTRACT_VERSION).parent),
            str(FIXTURES_IC14),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    out = json.loads(res.stdout)
    assert out["validated"] == 6 + len(_batches())
    assert out["refused"] == [
        "a gap carrying a value",
        "a value that is not an integer",
        "a value beyond 2^53 - 1",
        "an unknown quality",
        "an unknown gap reason",
        "null",
        "unknown field",
        "naive time",
        "another version",
        "bool for int",
        "a quantity on an aspect outside the table",
        "an unknown unit",
        "a tenant org with a slash",
    ]
    assert out["gridq"] == [] and out["pydantic"] is False


#: A consumer with the contracts package only: reads, chains, tracks, curates, verifies and ages.
_CONSUMER = """
import json, pathlib, sys
from datetime import datetime
from gridq.contracts.identity import IdentityClaims
from gridq.contracts.telemetry_series import (
    CuratedSeries, DqAnnotation, SeriesDescriptor, TelemetryBatch, TrackState, age_at,
    check_batch, check_supersession, current_revision, read_guard, track, values_in,
    verify_batch_chain, verify_curated,
)
fx, ic13 = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
read = lambda n: (fx / n).read_text(encoding="utf-8")
desc = SeriesDescriptor.model_validate_json(read("ic14_descriptor_site_power_meter.synthetic.json"))
lines = read("ic14_batches_site_power_meter.synthetic.jsonl").splitlines()
batches = [TelemetryBatch.model_validate_json(l) for l in lines]
ann = DqAnnotation.model_validate_json(read("ic14_annotation_flatline.synthetic.json"))
rev0 = CuratedSeries.model_validate_json(read("ic14_curated_site_power_1min_rev0.synthetic.json"))
rev1 = CuratedSeries.model_validate_json(read("ic14_curated_site_power_1min_rev1.synthetic.json"))
for b in batches:
    check_batch(desc, b)
e0 = [b for b in batches if b.stream_epoch == 0]
e1 = [b for b in batches if b.stream_epoch == 1]
head0 = verify_batch_chain(e0)
head1 = verify_batch_chain(e1, prior_epoch_head=head0)
state, verdicts = TrackState(desc.series_id), []
for b in [*e0, *e1]:
    state, v = track(state, b)
    verdicts.append(v.status.value)
verify_curated(rev0, desc, e0, [ann])
verify_curated(rev1, desc, batches, [ann])
check_supersession(rev0, rev1)
claims = IdentityClaims.model_validate_json(
    (ic13 / "ic13_claims_neura_ops.synthetic.json").read_text(encoding="utf-8")
)
decision = read_guard(claims, desc.tenant)
slots = values_in(decision, batches)
age = age_at(decision, desc, batches, datetime.fromisoformat("2027-01-04T16:30:01+00:00"), 250)
print(json.dumps({
    "head1": head1,
    "verdicts": sorted(set(verdicts)),
    "current": current_revision([rev1, rev0]).revision,
    "gaps": [[g.start.isoformat(), g.end.isoformat(), g.reason.value]
             for g in slots if g.kind == "gap"],
    "zero_values_from_gaps": sum(1 for s in slots if s.kind == "value" and s.value == 0),
    "age": [age.state, age.age_ms],
    "engine_modules": sorted(
        m for m in sys.modules if m.startswith("gridq.") and not m.startswith("gridq.contracts")
    ),
    "heavy": sorted(
        m for m in ("numpy", "pandas", "linopy", "highspy", "xarray", "scipy") if m in sys.modules
    ),
}))
"""


def test_a_consumer_runs_on_the_fixtures_without_the_producer() -> None:
    """Fresh interpreter, contract package only: no edge, engine, sizer or solver module. (The
    package's ``__init__`` loads every contract; that IC-14 itself never imports IC-5 is the AST
    test below.)"""
    res = subprocess.run(
        [sys.executable, "-c", _CONSUMER, str(FIXTURES_IC14), str(FIXTURES)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        check=True,
    )
    out = json.loads(res.stdout)
    assert out["engine_modules"] == [] and out["heavy"] == [], out
    assert out["head1"] == GOLDEN_HASHES_IC14[BATCHES]
    assert out["verdicts"] == ["in_order", "new_epoch"]
    assert out["current"] == 1
    assert out["gaps"] == [["2027-01-04T16:07:10+00:00", "2027-01-04T16:07:30+00:00", "comms_lost"]]
    assert out["zero_values_from_gaps"] == 0
    assert out["age"] == ["fresh", 2000]


# ------------------------------------------------------------------ the fixtures are synthetic


def test_every_fixture_is_labelled_synthetic_and_invents_nothing() -> None:
    records = [*(_descriptor(n) for n in DESCRIPTORS), *_batches(), _annotation()]
    records += [_curated(REV0), _curated(REV1)]
    for r in records:
        assert r.record_class == "synthetic_fixture"
        assert r.tenant.org_id.startswith("SYNTHETIC-") and r.site_id.startswith("SYNTHETIC-")
    for n in DESCRIPTORS:
        assert _descriptor(n).source.protocol.value == "simulated"  # type: ignore[union-attr]
    # The meter certificate is a placeholder hash, never a real document's or a pinned fixture's.
    cert = require_certified(_descriptor(METER))
    assert cert.evidence_hash == fx.placeholder_hash("meter certificate, site boundary")
    assert cert.evidence_hash not in {*GOLDEN_HASHES_IC14.values(), *GOLDEN_HASHES_IC5.values()}
    # The IC-5 binding names the IC-5 hybrid link profile fixture, the one site both share.
    assert {b.ic5.link_profile_hash for b in _batches()} == {  # type: ignore[union-attr]
        GOLDEN_HASHES_IC5["ic5_link_profile_hybrid.synthetic.json"]
    }


@pytest.mark.parametrize("path", IC14_FIXTURES, ids=lambda p: p.name)
def test_no_fixture_holds_a_null_anywhere(path: Path) -> None:
    for line in (
        path.read_text(encoding="utf-8").splitlines()
        if path.suffix == ".jsonl"
        else [path.read_text(encoding="utf-8")]
    ):
        assert None not in list(_walk(json.loads(line)))


@pytest.mark.parametrize(
    "name,field",
    [
        (n, f)
        for n in (METER, BATCHES, ANNOTATION, REV1)
        for f in _model_for(n).model_fields
        if f not in ("contract", "contract_version")
    ],
)
def test_null_is_refused_for_every_field(name: str, field: str) -> None:
    text = (FIXTURES_IC14 / name).read_text("utf-8")
    data = json.loads(text.splitlines()[0] if name.endswith(".jsonl") else text)
    data[field] = None
    with pytest.raises(ValidationError):
        _model_for(name).model_validate_json(json.dumps(data))


def test_the_models_are_final_and_frozen() -> None:
    for model in MODELS:
        with pytest.raises(TypeError, match="final"):
            type("Sub", (model,), {})
    d = _descriptor()
    with pytest.raises(ValidationError):
        d.site_id = "SYNTHETIC-OTHER"  # type: ignore[misc]


def test_the_module_reads_only_the_contracts_it_names_and_never_ic5() -> None:
    """IC-14 reads IC-13 and IC-4 and the shared base; an IC-5 minor will import IC-14, never the
    reverse (D151)."""
    tree = ast.parse(Path(telemetry_series.__file__).read_text(encoding="utf-8"))
    gridq = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("gridq"):
            gridq.add(node.module)
        if isinstance(node, ast.Import):
            gridq |= {a.name for a in node.names if a.name.startswith("gridq")}
    assert gridq == {
        "gridq.contracts._base",
        "gridq.contracts.audit",
        "gridq.contracts.evidence",
        "gridq.contracts.identity",
    }


# ------------------------------------------------------------------ IC-5 alignment


def test_a_batch_fits_an_ic5_edge_to_cloud_body_union_unchanged() -> None:
    """IC-5 1.0.0 has no telemetry body (IC-5 is untouched). A batch is shaped to become one:
    its ``kind`` does not collide with IC-5's, it parses through a union of IC-5's four bodies plus
    itself, IC-5's header rule reads its ``site_id``, and it fits an IC-5 frame."""
    union = Annotated[
        edge_cloud.ScheduleAck
        | edge_cloud.EdgeHeartbeat
        | edge_cloud.FailSafeEvent
        | edge_cloud.AnmEvent
        | TelemetryBatch,
        Field(discriminator="kind"),
    ]
    adapter: TypeAdapter[Any] = TypeAdapter(union)
    ic5_kinds = {
        m.model_fields["kind"].annotation.__args__[0]  # type: ignore[union-attr]
        for m in (
            edge_cloud.ScheduleAck,
            edge_cloud.EdgeHeartbeat,
            edge_cloud.FailSafeEvent,
            edge_cloud.AnmEvent,
        )
    }
    assert "telemetry_batch" not in ic5_kinds
    profile = edge_cloud.LinkProfile.model_validate_json(
        (FIXTURES / "ic5" / "ic5_link_profile_hybrid.synthetic.json").read_text("utf-8")
    )
    for line in (FIXTURES_IC14 / BATCHES).read_text("utf-8").splitlines():
        body = adapter.validate_json(line)
        assert isinstance(body, TelemetryBatch)
        assert getattr(body, "site_id", profile.site_id) == profile.site_id
        assert body.ic5.link_profile_hash == profile.content_hash()  # type: ignore[union-attr]
        assert len(line.encode()) <= min(profile.max_frame_bytes, _descriptor().max_batch_bytes)
        assert check_batch_size(line.encode(), _descriptor())


def test_the_ic5_trigger_codes_and_clock_literals_match_ic5() -> None:
    codes = {c.value for c in edge_cloud.TriggerCode}
    assert set(TRIGGER_GAP_REASON) <= codes
    assert TRIGGER_GAP_REASON == {
        "site_meter_lost": GapReason.COMMS_LOST,
        "power_device_comms_lost": GapReason.COMMS_LOST,
        "thermal_comms_lost": GapReason.COMMS_LOST,
        "clock_unsynchronised": GapReason.CLOCK_UNTRUSTED,
    }
    # A lost cloud link is not a gap: the edge buffers it (store and forward).
    assert "cloud_link_lost" not in TRIGGER_GAP_REASON
    ic5 = edge_cloud.FailSafeEvent.model_fields["clock_state"].annotation
    ours = telemetry_series.ClockStatement.model_fields["clock_state"].annotation
    assert set(ic5.__args__) == set(ours.__args__)  # type: ignore[union-attr]


def test_the_settlement_resolution_is_the_ic3_grid() -> None:
    assert PERIOD_MS["settlement_30min"] == RESOLUTION_MIN * 60_000
    assert BLOCK_SPAN_MS["settlement_30min"] == 48 * PERIOD_MS["settlement_30min"]


# ------------------------------------------------------------------ names and units


def test_point_keys_name_a_point_and_the_table_fixes_its_unit() -> None:
    d = _descriptor()
    assert point_key(d.name) == fx.METER_POINT
    assert d.series_id == f"{fx.ORG}/{fx.SITE}/{fx.METER_POINT}"
    assert d.unit.value == "W" and QUANTITY_TABLE[Quantity.ACTIVE_POWER].unit is d.unit
    assert {s.unit.value for s in QUANTITY_TABLE.values()} == {
        "W",
        "var",
        "VA",
        "Wh",
        "mHz",
        "mV",
        "mA",
        "basis_point",
        "millidegree_C",
    }
    with pytest.raises(ValueError, match="five"):
        parse_point_key("site_boundary.main.active_power.scada")
    with pytest.raises(ValidationError):
        parse_point_key("site_boundary.Main.active_power.scada.whole")


@pytest.mark.parametrize(
    "case,edit,match",
    _cases(
        [
            ("aspect", {"aspect": "supply"}, "not measured on aspect"),
            ("echo", {"quantity": "state_of_charge", "role": "setpoint_echo"}, "never echoes"),
        ]
    ),
)
def test_a_point_outside_the_table_is_refused(case: str, edit: dict[str, str], match: str) -> None:
    name = {**fx._name(fx.METER_POINT), **edit}
    with pytest.raises(ValidationError, match=match):
        PointName.model_validate_json(json.dumps(name))


def test_a_meter_point_and_its_scada_reading_are_counterparts() -> None:
    names = [_descriptor(n).name for n in DESCRIPTORS]
    assert counterparts(names[0], names) == (names[1],)
    assert counterparts(names[1], names) == (names[0],)
    assert counterparts(names[2], names) == ()


def _descriptor_with(**changes: Any) -> dict[str, Any]:
    return {**load_ic14(METER), **changes}


@pytest.mark.parametrize(
    "case,changes,match",
    _cases(
        [
            ("unit", {"unit": "Wh"}, "measured in W"),
            ("plausible-order", {"plausible_min": 5, "plausible_max": 5}, "plausible_min <"),
            ("plausible-range", {"plausible_min": -(2**53)}, "greater than or equal"),
            ("meter-missing", {"meter": fx._na("x")}, "metered role names its meter"),
            (
                "freshness",
                {
                    "freshness": {
                        "kind": "freshness_policy",
                        "stale_after_ms": 60_000,
                        "lost_after_ms": 600_000,
                    }
                },
                "block span",
            ),
            (
                "freshness-order",
                {
                    "freshness": {
                        "kind": "freshness_policy",
                        "stale_after_ms": 600_000,
                        "lost_after_ms": 120_000,
                    }
                },
                "block span",
            ),
            (
                "simulated-live",
                {
                    "source": {
                        "kind": "source_address",
                        "protocol": "modbus_tcp",
                        "address": "10.0.0.1:502/40001",
                    }
                },
                "simulated source is exactly a synthetic fixture",
            ),
            ("series-point", {"series_id": f"{fx.ORG}/{fx.SITE}/{fx.SCADA_POINT}"}, "name this"),
        ]
    ),
)
def test_a_bad_descriptor_is_refused(case: str, changes: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        SeriesDescriptor.model_validate_json(json.dumps(_descriptor_with(**changes)))


def test_a_derived_point_has_no_source_and_every_other_point_has_one() -> None:
    derived = fx.METER_POINT.replace("revenue_meter", "derived")
    base = {**_descriptor_with(), **fx._head(derived), "name": fx._name(derived)}
    base["meter"] = fx._na("derived")
    with pytest.raises(ValidationError, match="derived point has no source"):
        SeriesDescriptor.model_validate_json(json.dumps(base))
    base["source"], base["source_scaling"] = fx._na("derived"), fx._na("derived")
    assert SeriesDescriptor.model_validate_json(json.dumps(base)).name.role.value == "derived"
    scada = load_ic14(SCADA)
    scada["source"] = fx._na("none")
    with pytest.raises(ValidationError, match="derived point has no source"):
        SeriesDescriptor.model_validate_json(json.dumps(scada))


def test_a_metered_series_states_its_certificate_or_is_not_certified() -> None:
    assert isinstance(require_certified(_descriptor(METER)), EvidenceRef)
    with pytest.raises(NotCertified):
        require_certified(_descriptor(SCADA))
    unknown = _descriptor_with()
    unknown["meter"]["certificate"] = fx._unknown("registry not yet populated")
    with pytest.raises(NotCertified):
        require_certified(SeriesDescriptor.model_validate_json(json.dumps(unknown)))


def test_a_kw_figure_states_its_rounding_side() -> None:
    assert (w_to_kw(1_500, "floor"), w_to_kw(1_500, "ceil")) == (1, 2)
    assert (w_to_kw(-1_500, "floor"), w_to_kw(-1_500, "ceil")) == (-2, -1)
    assert (wh_to_kwh(999, "floor"), wh_to_kwh(999, "ceil")) == (0, 1)
    assert (millideg_to_tenths_c(-50, "floor"), millideg_to_tenths_c(-50, "ceil")) == (-1, 0)
    assert kw_to_w(4_500) == 4_500_000 and kwh_to_wh(3) == 3_000
    assert tenths_c_to_millideg(215) == 21_500


def test_signed_power_splits_into_ic5_import_and_export() -> None:
    assert split_import_export_w(1_500_000) == (1_500_000, 0)
    assert split_import_export_w(-4_500_000) == (0, 4_500_000)
    assert split_import_export_w(0) == (0, 0)


# ------------------------------------------------------------------ quality flags


def test_every_quality_and_gap_reason_maps_to_one_verified_opcua_code() -> None:
    assert set(QUALITY_MAP) == {*ValueQuality, *GapReason}
    codes = [m.opcua_code for m in QUALITY_MAP.values()]
    assert len(set(codes)) == len(codes)
    # Severity bits: Good 00, Uncertain 01, Bad 10 (OPC UA Part 4, StatusCode).
    for q in ValueQuality:
        sev = to_opcua(q) >> 30
        assert sev == (0 if q in (ValueQuality.GOOD, ValueQuality.LOCAL_OVERRIDE) else 1), q
    for g in GapReason:
        assert to_opcua(g) >> 30 == 2, g
    assert QUALITY_MAP[ValueQuality.HELD].opcua_name == "UncertainLastUsableValue"
    assert QUALITY_MAP[GapReason.COMMS_LOST].opcua_code == 0x80310000


def test_an_unlisted_opcua_code_raises() -> None:
    with pytest.raises(ValueError, match="no IC-14 1.0.0 member"):
        from_opcua(0x40910000)  # UncertainSubstituteValue: substitution is not in 1.0.0


# ------------------------------------------------------------------ time base


def test_slots_and_blocks_are_half_open_on_the_utc_grid() -> None:
    b = _batches()[0]
    assert slot_instant(b, 0) == WINDOW and slot_instant(b, 59) == WINDOW + timedelta(seconds=59)
    with pytest.raises(ValueError):
        slot_instant(b, 60)
    for name, (period, span) in telemetry_series.RESOLUTIONS.items():
        assert span % period == 0, name
    assert [PERIOD_MS[r] for r in ("event_20hz", "operational_1s", "standard_1min")] == [
        50,
        1_000,
        60_000,
    ]


def test_the_edge_clock_drift_is_read_within_one_boot() -> None:
    e0 = _epoch(0)
    assert clock_drift_ms(e0[0], e0[18]) == 18
    with pytest.raises(ValueError, match="one edge boot"):
        clock_drift_ms(e0[-1], _epoch(1)[0])


def test_settlement_periods_are_counted_from_local_midnight() -> None:
    assert settlement_period_of(datetime(2027, 1, 4, 16, tzinfo=UTC)) == (date(2027, 1, 4), 33)
    assert settlement_period_of(datetime(2027, 7, 1, 0, tzinfo=UTC)) == (date(2027, 7, 1), 3)
    assert settlement_period_of(datetime(2027, 6, 30, 23, tzinfo=UTC)) == (date(2027, 7, 1), 1)
    with pytest.raises(ValueError):
        settlement_period_of(datetime(2027, 1, 4, 16, 15, tzinfo=UTC))


# ------------------------------------------------------------------ the raw tier


def _story_slots() -> list[Slot]:
    return [("v", ValueQuality.GOOD, 1)] * 10 + [("g", GapReason.COMMS_LOST, None)] * 50


@pytest.mark.parametrize(
    "case",
    [
        "hole",
        "overlap",
        "short",
        "unmerged",
        "insufficient-coverage-in-raw",
        "genesis",
        "prior-epoch",
        "sealed-early",
        "out-of-quantity-range",
        "misaligned",
        "slot-count",
        "synchronised-without-bound",
        "receipt-lag",
    ],
)
def test_a_malformed_batch_is_refused(case: str) -> None:
    data = _batch_data(_story_slots(), WINDOW)
    seg = data["segments"]
    if case == "hole":
        seg[1]["first_slot"] = 11
    elif case == "overlap":
        seg[1]["first_slot"] = 9
    elif case == "short":
        seg[1]["last_slot"] = 58
    elif case == "unmerged":
        data["segments"] = [
            {**seg[0], "values": [1] * 5},
            {**seg[0], "first_slot": 5, "values": [1] * 5},
            seg[1],
        ]
    elif case == "insufficient-coverage-in-raw":
        seg[1]["reason"] = "insufficient_coverage"
    elif case == "genesis":
        data["seq"] = 1
    elif case == "prior-epoch":
        data["stream_epoch"] = 1
    elif case == "sealed-early":
        data["sealed_at"] = _iso(WINDOW + timedelta(seconds=59))
    elif case == "out-of-quantity-range":
        p = fx.SOC_POINT
        data.update({**fx._head(p)})
        seg[0]["values"] = [10_001] * 10
    elif case == "misaligned":
        data["origin"] = _iso(WINDOW + timedelta(seconds=1))
        data["sealed_at"] = _iso(WINDOW + timedelta(minutes=2))
    elif case == "slot-count":
        data["slot_count"] = 59
    elif case == "synchronised-without-bound":
        data["clock"]["clock_offset_bound_ms"] = fx._unknown("x")
    elif case == "receipt-lag":
        data["clock"]["stamped_by"] = "source_device"
    TelemetryBatch.model_validate_json(json.dumps(_batch_data(_story_slots(), WINDOW)))
    with pytest.raises(ValidationError):
        TelemetryBatch.model_validate_json(json.dumps(data))


def test_a_batch_binds_its_descriptor_and_a_good_value_stays_plausible() -> None:
    d, b = _descriptor(), _batches()[0]
    check_batch(d, b)
    with pytest.raises(BatchRefused, match="descriptor_mismatch"):
        check_batch(_descriptor(SCADA), b)
    data = json.loads(b.model_dump_json())
    data["segments"][0]["values"][3] = 20_000_001
    implausible = TelemetryBatch.model_validate_json(json.dumps(data))
    with pytest.raises(BatchRefused, match="implausible_good_value"):
        check_batch(d, implausible)
    data["segments"][0]["quality"] = "out_of_range"
    check_batch(d, TelemetryBatch.model_validate_json(json.dumps(data)))


def test_the_fixture_chain_verifies_across_the_reboot_and_a_tamper_breaks_it() -> None:
    head0 = verify_batch_chain(_epoch(0))
    assert _epoch(1)[0].prior_epoch_head == head0
    assert verify_batch_chain(_epoch(1), prior_epoch_head=head0) == GOLDEN_HASHES_IC14[BATCHES]
    with pytest.raises(ChainBroken, match="previous epoch's head"):
        verify_batch_chain(_epoch(1), prior_epoch_head=GENESIS_PREV_HASH)
    data = json.loads(_epoch(0)[4].model_dump_json())
    data["segments"][0]["values"][0] += 1
    tampered = [*_epoch(0)]
    tampered[4] = TelemetryBatch.model_validate_json(json.dumps(data))
    with pytest.raises(ChainBroken, match="seq 5"):
        verify_batch_chain(tampered)
    with pytest.raises(ChainBroken, match="one chain"):
        verify_batch_chain([_epoch(0)[-1], _epoch(1)[0]])


def test_the_tracker_refuses_a_fork_and_a_changed_descriptor_only() -> None:
    state = TrackState(_descriptor().series_id)
    for b in _epoch(0)[:6]:
        state, verdict = track(state, b)
        assert verdict.accepted
    data = json.loads(_epoch(0)[5].model_dump_json())
    data["segments"][0]["values"][0] += 1
    fork = TelemetryBatch.model_validate_json(json.dumps(data))
    after, verdict = track(state, fork)
    assert verdict.status is TrackStatus.FORK and not verdict.accepted and after is state
    assert track(state, _epoch(0)[5])[1].status is TrackStatus.DUPLICATE
    data = json.loads(_epoch(0)[6].model_dump_json())
    data["descriptor_hash"] = GOLDEN_HASHES_IC14[SCADA]
    changed = TelemetryBatch.model_validate_json(json.dumps(data))
    after, verdict = track(state, changed)
    assert verdict.status is TrackStatus.PROFILE_MISMATCH and after is state
    # Late fill (a batch whose successor arrived first) is normal, and so is a gap.
    gap_state, verdict = track(state, _epoch(0)[8])
    assert verdict.status is TrackStatus.GAP and verdict.accepted
    assert track(gap_state, _epoch(0)[7])[1].status is TrackStatus.LATE_FILL


# ------------------------------------------------------------------ annotations, the curated tier


def test_the_fixture_curates_as_the_story_says() -> None:
    rev1 = _curated(REV1)
    kinds = {i.slot: i for i in rev1.intervals}
    assert isinstance(kinds[5], Aggregate) and kinds[5].rejected_ms == 10_000  # held
    assert isinstance(kinds[7], CuratedGap) and kinds[7].gap_ms == 20_000  # comms_lost 20 s
    assert kinds[7].reason is GapReason.INSUFFICIENT_COVERAGE and kinds[7].usable_ms == 40_000
    assert isinstance(kinds[10], Aggregate) and kinds[10].quality is ValueQuality.INACCURATE
    assert isinstance(kinds[15], CuratedGap) and kinds[15].rejected_ms == 60_000  # flatline
    assert isinstance(kinds[19], CuratedGap) and kinds[19].reason is GapReason.NOT_RECORDED
    assert isinstance(kinds[20], CuratedGap) and kinds[20].reason is GapReason.CLOCK_UNTRUSTED
    rev0 = _curated(REV0)
    assert all(
        isinstance(i, CuratedGap) and i.reason is GapReason.NOT_RECORDED
        for i in rev0.intervals[19:]
    )
    # Recomputed from IC-14 alone, it is byte for byte the committed record.
    rebuilt = curate(
        _descriptor(),
        list(_batches()),
        [_annotation()],
        rev1.method,
        WINDOW,
        "standard_1min",
        rev0,
    )
    assert rebuilt == rev1


def test_curated_records_recompute_by_hash_and_supersede_in_order() -> None:
    rev0, rev1 = _curated(REV0), _curated(REV1)
    verify_curated(rev0, _descriptor(), _epoch(0), [_annotation()])
    verify_curated(rev1, _descriptor(), list(_batches()), [_annotation()])
    check_supersession(rev0, rev1)
    assert current_revision([rev1, rev0]) == rev1 and rev1.supersede_reason == "late_raw"
    with pytest.raises(ValueError, match="runs the record names"):
        verify_curated(rev1, _descriptor(), _epoch(0), [_annotation()])
    with pytest.raises(ValueError, match="annotations"):
        verify_curated(rev1, _descriptor(), list(_batches()), [])
    data = json.loads(rev1.model_dump_json())
    data["intervals"][0]["value"] += 1
    forged = CuratedSeries.model_validate_json(json.dumps(data))
    with pytest.raises(ValueError, match="does not recompute"):
        verify_curated(forged, _descriptor(), list(_batches()), [_annotation()])


def test_a_supersession_must_bear_out_its_reason_and_a_fork_is_refused() -> None:
    rev0, rev1 = _curated(REV0), _curated(REV1)
    backwards = json.loads(rev1.model_dump_json())
    backwards["inputs"] = backwards["inputs"][:1]
    backwards["intervals"] = json.loads(rev0.model_dump_json())["intervals"]
    no_new_raw = CuratedSeries.model_validate_json(json.dumps(backwards))
    with pytest.raises(ValueError, match="strictly more raw"):
        check_supersession(rev0, no_new_raw)
    with pytest.raises(ValueError, match="next one"):
        check_supersession(rev1, rev1)
    changed = curate(
        _descriptor(),
        list(_batches()),
        [_annotation()],
        _method(min_usable_ms=30_000),
        WINDOW,
        "standard_1min",
        rev1,
    )
    assert changed.supersede_reason == "method_change"
    check_supersession(rev1, changed)
    assert current_revision([rev0, rev1, changed]) == changed
    with pytest.raises(ValueError, match="fork"):
        current_revision([rev0, rev1, no_new_raw])


def test_a_withdrawn_annotation_stops_rejecting_and_the_change_is_a_revision() -> None:
    ann = _annotation()
    withdrawal = {
        **load_ic14(ANNOTATION),
        "annotation_id": "SYNTHETIC-DQ-0002",
        "disposition": "withdrawn",
        "supersedes": ann.content_hash(),
        "issued_at": _iso(WINDOW + timedelta(minutes=40)),
    }
    w = DqAnnotation.model_validate_json(json.dumps(withdrawal))
    assert effective_annotations([ann, w]) == ()
    revised = curate(
        _descriptor(),
        list(_batches()),
        [ann, w],
        _curated(REV1).method,
        WINDOW,
        "standard_1min",
        _curated(REV1),
    )
    assert revised.supersede_reason == "annotation_change"
    assert isinstance(revised.intervals[15], Aggregate)
    check_supersession(_curated(REV1), revised)
    withdrawal["supersedes"] = fx._na("x")
    with pytest.raises(ValidationError, match="names the annotation it withdraws"):
        DqAnnotation.model_validate_json(json.dumps(withdrawal))


@pytest.mark.parametrize(
    "case,changes,match",
    _cases(
        [
            ("held-usable", {"usable": ["good", "held"]}, "held value is never usable"),
            ("good-missing", {"usable": ["inaccurate"]}, "must contain good"),
            ("gap-applied", {"applied_findings": ["gap"]}, "never applied"),
            (
                "rounding-missing",
                {"rounding": {"kind": "not_applicable", "reason": "x"}},
                "rounding",
            ),
            ("rounding-extra", {"aggregation": "max"}, "rounding"),
            ("unsorted", {"usable": ["inaccurate", "good"]}, "ValueQuality order"),
        ]
    ),
)
def test_a_bad_method_is_refused(case: str, changes: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        _method(**changes)


@pytest.mark.parametrize(
    "case", ["closure", "thin-aggregate", "fat-gap", "not-coarser", "revision", "slots"]
)
def test_a_bad_curated_record_is_refused(case: str) -> None:
    data = json.loads(_curated(REV1).model_dump_json())
    iv = data["intervals"]
    if case == "closure":
        iv[0]["gap_ms"] = 1
    elif case == "thin-aggregate":
        iv[0].update(usable_ms=40_000, gap_ms=20_000)
    elif case == "fat-gap":
        iv[7].update(usable_ms=50_000, gap_ms=10_000)
    elif case == "not-coarser":
        data["source_resolution"] = "standard_1min"
    elif case == "revision":
        data["supersedes"] = fx._na("x")
    elif case == "slots":
        data["intervals"] = iv[:-1]
    with pytest.raises(ValidationError):
        CuratedSeries.model_validate_json(json.dumps(data))


def test_curated_derives_from_raw_only() -> None:
    with pytest.raises(TypeError, match="raw batches only"):
        resample(
            [_curated(REV0)],  # type: ignore[list-item]
            [],
            _curated(REV0).method,
            WINDOW,
            "standard_1min",
        )


def test_an_unsynchronised_block_curates_to_clock_untrusted_gaps() -> None:
    b = _batch([("v", ValueQuality.GOOD, 7)] * 60, WINDOW, synced=False)
    out = resample([b], [], _method(min_usable_ms=1_000), WINDOW, "standard_1min")
    assert isinstance(out[0], CuratedGap) and out[0].reason is GapReason.CLOCK_UNTRUSTED
    # The raw keeps its values, flagged untrusted on read.
    [*vals] = values_in(_allowed(), [b])
    assert all(isinstance(v, SlotValue) and not v.clock_trusted for v in vals)


# ------------------------------------------------------------------ data age


def test_data_age_shows_a_value_only_while_fresh() -> None:
    d, bs, dec = _descriptor(), list(_batches()), _allowed()
    at = lambda s: datetime.fromisoformat(f"2027-01-04T{s}+00:00")  # noqa: E731
    fresh = age_at(dec, d, bs, at("16:30:01"), 250)
    assert (fresh.state, fresh.age_ms) == ("fresh", 2_000) and isinstance(fresh.value, int)
    stale = age_at(dec, d, bs, at("16:31:59"), 250)  # exactly stale_after_ms: the worse state
    assert (stale.state, stale.age_ms) == ("stale", 120_000)
    assert isinstance(stale.value, NotApplicable)
    lost = age_at(dec, d, bs, at("16:39:59"), 250)
    assert (lost.state, lost.age_ms) == ("lost", 600_000)
    assert age_at(dec, d, bs, at("16:29:00"), 250).state == "future_dated"
    assert age_at(dec, d, bs, at("16:29:00"), 60_000).state == "fresh"
    assert age_at(dec, d, [*_epoch(0), _epoch(1)[0]], at("16:21:01"), 250).state == (
        "clock_untrusted"
    )
    assert age_at(dec, d, [], at("16:21:01"), 250).state == "no_data"
    with pytest.raises(ValidationError, match="exactly when fresh"):
        AgeReading.model_validate_json(
            json.dumps({**json.loads(stale.model_dump_json()), "value": 1})
        )


def test_a_held_value_never_reads_as_fresh() -> None:
    slots: list[Slot] = [("v", ValueQuality.GOOD, 5)] * 30 + [("v", ValueQuality.HELD, 5)] * 30
    b = _batch(slots, WINDOW)
    r = age_at(_allowed(), _descriptor(), [b], WINDOW + timedelta(seconds=60), 250)
    assert r.last_usable_at == WINDOW + timedelta(seconds=29) and r.age_ms == 31_000


# ------------------------------------------------------------------ tenancy


@pytest.mark.parametrize(
    "who,tenant,outcome",
    [
        ("neura_ops", (fx.ORG, fx.SITE), "allowed"),
        ("customer", (fx.ORG, fx.SITE), "denied"),
        ("customer", ("SYNTHETIC-ORG-CUSTOMER-A", "SYNTHETIC-SITE-A1"), "allowed"),
        ("verifier", ("SYNTHETIC-ORG-CUSTOMER-A", "SYNTHETIC-SITE-A1"), "allowed"),
        ("verifier", ("SYNTHETIC-ORG-CUSTOMER-A", "SYNTHETIC-SITE-A2"), "denied"),
        ("lender_readonly", ("SYNTHETIC-ORG-CUSTOMER-A", "SYNTHETIC-SITE-A1"), "denied"),
    ],
)
def test_read_guard_is_ic13_authorise_and_its_audit_event_agrees(
    who: str, tenant: tuple[str, str], outcome: str
) -> None:
    claims, key = _claims(who), _tenant(*tenant)
    decision = read_guard(claims, key)
    assert decision.outcome == outcome
    assert (
        decision.target
        == resource_ref(key)
        == ResourceRef(kind=ResourceKind.TELEMETRY, org_id=tenant[0], resource_id=tenant[1])
    )
    event = audit_access(
        None,
        claims,
        event_id="SYNTHETIC-AUDIT-IC14-0001",
        occurred_at=datetime(2027, 1, 4, 16, 31, tzinfo=UTC),
        action=AuditAction.CUSTOMER_DATA_READ,
        target=decision.target,
        control=not_applicable("a telemetry read"),
    )
    assert (event.outcome, event.scope, event.target) == (
        decision.outcome,
        decision.scope,
        decision.target,
    )


def test_a_denied_decision_reads_nothing() -> None:
    denied = read_guard(_claims("customer"), _tenant())
    with pytest.raises(TenantReadRefused, match="denied"):
        values_in(denied, list(_batches()))
    with pytest.raises(TenantReadRefused, match="denied"):
        age_at(denied, _descriptor(), list(_batches()), WINDOW, 250)


def test_a_decision_for_one_tenant_reads_no_other() -> None:
    other = _tenant("SYNTHETIC-ORG-CUSTOMER-A", "SYNTHETIC-SITE-A1")
    decision = read_guard(_claims("customer"), other)
    assert decision.outcome == "allowed"
    with pytest.raises(TenantReadRefused, match="under a decision for"):
        values_in(decision, list(_batches()))


def test_a_forged_read_decision_is_refused() -> None:
    real = json.loads(_allowed().model_dump_json())
    forged = {**real, "scope": fx._na("no scope"), "outcome": "allowed"}
    with pytest.raises(ValidationError, match="allowed exactly when"):
        ReadDecision.model_validate_json(json.dumps(forged))
    retargeted = {**real, "tenant": json.loads(_tenant(site="SYNTHETIC-SITE-X").model_dump_json())}
    with pytest.raises(ValidationError, match="target must be"):
        ReadDecision.model_validate_json(json.dumps(retargeted))


def test_a_transform_refuses_two_tenants() -> None:
    other = {
        "kind": "tenant_key",
        "org_id": "SYNTHETIC-ORG-B",
        "org_kind": "customer",
        "site_id": fx.SITE,
    }
    b = _batch(_story_slots(), WINDOW + timedelta(minutes=1), tenant=other)
    with pytest.raises(MixedTenants, match="tenant"):
        resample([_batches()[0], b], [], _method(), WINDOW, "standard_1min")
    with pytest.raises(MixedTenants):
        curate(
            _descriptor(),
            [b],
            [],
            _method(),
            WINDOW,
            "standard_1min",
            not_applicable("first revision"),
        )


@pytest.mark.parametrize(
    "case,edit,match",
    _cases(
        [
            ("site-mismatch", lambda d: d.update(site_id="SYNTHETIC-SITE-OTHER"), "tenant.site_id"),
            (
                "series-prefix",
                lambda d: d.update(series_id=f"SYNTHETIC-ORG-B/{fx.SITE}/{fx.METER_POINT}"),
                "start with the tenant key",
            ),
            (
                "synthetic-live-record",
                lambda d: d.update(record_class="live"),
                "exactly when the record is a synthetic fixture",
            ),
            (
                "synthetic-real-org",
                lambda d: (
                    d["tenant"].update(org_id="ORG-REAL"),
                    d.update(series_id=f"ORG-REAL/{fx.SITE}/{fx.METER_POINT}"),
                ),
                "exactly when the record is a synthetic fixture",
            ),
            (
                "neura-class",
                lambda d: d.update(data_class="neura_operational"),
                "Neura-owned series is neura_operational",
            ),
            (
                "neura-org-customer-class",
                lambda d: d["tenant"].update(org_kind="neura"),
                "Neura-owned series is neura_operational",
            ),
        ]
    ),
)
def test_the_tenant_key_rules_hold_on_every_record(case: str, edit: Any, match: str) -> None:
    data = load_ic14(METER)
    edit(data)
    with pytest.raises(ValidationError, match=match):
        SeriesDescriptor.model_validate_json(json.dumps(data))


def test_the_tenant_key_is_inside_the_content_hash() -> None:
    data = load_ic14(METER)
    data["data_class"] = "customer_personal"
    assert (
        SeriesDescriptor.model_validate_json(json.dumps(data)).content_hash()
        != (GOLDEN_HASHES_IC14[METER])
    )
    assert _descriptor().tenant.canonical() == f"{fx.ORG}/{fx.SITE}"


# ------------------------------------------------------------------ 2. properties: gap, never zero

SEEDS = range(12)


@pytest.mark.parametrize("seed", SEEDS)
def test_p1_a_producer_block_tiles_every_slot_once_and_a_gap_holds_no_value(seed: int) -> None:
    rng = random.Random(1_400 + seed)
    for res in ("event_20hz", "operational_1s", "standard_1min"):
        n = BLOCK_SPAN_MS[res] // PERIOD_MS[res]
        slots = _random_slots(rng, n)
        b = _batch(slots, datetime(2027, 1, 4, tzinfo=UTC), resolution=res)
        assert _expand(b) == slots
        read = values_in(_allowed(), [b])
        covered = 0
        for item in read:
            if isinstance(item, SlotGap):
                covered += (item.end - item.start) // (PERIOD_MS[res] * MS)
            else:
                covered += 1
        assert covered == n
        gap_slots = {i for i, s in enumerate(slots) if s[0] == "g"}
        value_starts = {_ms(v.start) for v in read if isinstance(v, SlotValue)}
        for i in gap_slots:
            assert _ms(slot_instant(b, i)) not in value_starts
        for s in b.segments:
            if isinstance(s, GapRun):
                assert "values" not in s.model_dump() and "value" not in s.model_dump()


@pytest.mark.parametrize("seed", SEEDS)
def test_p2_a_hole_an_overlap_or_an_unmerged_run_is_refused(seed: int) -> None:
    rng = random.Random(2_400 + seed)
    slots = _random_slots(rng, 60)
    data = _batch_data(slots, WINDOW)
    segs = data["segments"]
    TelemetryBatch.model_validate_json(json.dumps(data))
    i = rng.randrange(len(segs))
    for edit in ("drop", "shift", "split"):
        bad = json.loads(json.dumps(data))
        s = bad["segments"]
        if edit == "drop":
            if len(s) < 2:
                continue
            del s[i]
        elif edit == "shift":
            if i == 0:
                s[0]["first_slot"] = 1
            else:
                s[i]["first_slot"] -= 1
        else:
            seg = s[i]
            if seg["kind"] == "gap":
                if seg["first_slot"] == seg["last_slot"]:
                    continue
                s[i : i + 1] = [
                    {**seg, "last_slot": seg["first_slot"]},
                    {**seg, "first_slot": seg["first_slot"] + 1},
                ]
            else:
                if len(seg["values"]) < 2:
                    continue
                s[i : i + 1] = [
                    {**seg, "values": seg["values"][:1]},
                    {**seg, "first_slot": seg["first_slot"] + 1, "values": seg["values"][1:]},
                ]
        with pytest.raises(ValidationError):
            TelemetryBatch.model_validate_json(json.dumps(bad))


@pytest.mark.parametrize("raw,curated", CASCADES)
@pytest.mark.parametrize("seed", range(6))
def test_p3_resampling_and_aggregation_match_an_independent_oracle(
    raw: str, curated: str, seed: int
) -> None:
    rng = random.Random(3_400 + seed)
    origin, batches = _random_window(rng, raw, curated)
    method = _random_method(rng, PERIOD_MS[curated], PERIOD_MS[raw])
    got = resample(batches, [], method, origin, curated)
    want = _oracle(batches, method, origin, curated)
    assert len(got) == len(want)
    for g, w in zip(got, want, strict=True):
        assert (g.usable_ms, g.rejected_ms, g.gap_ms) == (
            w["usable_ms"],
            w["rejected_ms"],
            w["gap_ms"],
        )
        if "value" in w:
            assert isinstance(g, Aggregate) and (g.value, g.quality) == (w["value"], w["quality"])
        else:
            assert isinstance(g, CuratedGap) and g.reason is w["reason"]


@pytest.mark.parametrize("raw,curated", CASCADES)
@pytest.mark.parametrize("seed", range(6))
def test_p4_a_gap_and_a_zero_curate_differently(raw: str, curated: str, seed: int) -> None:
    """Replace every gap with good zeros: every interval's gap time goes to zero, and an interval
    that was wholly gap becomes a zero value. The contract never conflates the two."""
    rng = random.Random(4_400 + seed)
    origin, batches = _random_window(rng, raw, curated, keep=1.0)
    batches = [
        _batch(_expand(b), b.origin, resolution=raw, seq=b.seq, prev=_H) for b in batches
    ]  # all synchronised
    zeroed = [
        _batch(
            [("v", ValueQuality.GOOD, 0) if s[0] == "g" else s for s in _expand(b)],
            b.origin,
            resolution=raw,
            seq=b.seq,
            prev=_H,
        )
        for b in batches
    ]
    method = _method(
        aggregation="max",
        min_usable_ms=PERIOD_MS[raw],
        rounding=fx._na("max"),
        usable=["good", "local_override", "inaccurate", "out_of_range", "subnormal"],
        applied_findings=[],
    )
    a = resample(batches, [], method, origin, curated)
    z = resample(zeroed, [], method, origin, curated)
    for x, y in zip(a, z, strict=True):
        assert y.gap_ms == 0
        if x.gap_ms == PERIOD_MS[curated]:
            assert isinstance(x, CuratedGap) and x.reason in RAW_GAPS
            assert isinstance(y, Aggregate) and y.value == 0
        if isinstance(x, Aggregate) and isinstance(y, Aggregate):
            assert y.usable_ms == x.usable_ms + x.gap_ms


@pytest.mark.parametrize("raw,curated", CASCADES)
@pytest.mark.parametrize("seed", range(6))
def test_p5_withholding_raw_never_adds_a_value(raw: str, curated: str, seed: int) -> None:
    rng = random.Random(5_400 + seed)
    origin, batches = _random_window(rng, raw, curated, keep=1.0)
    method = _random_method(rng, PERIOD_MS[curated], PERIOD_MS[raw])
    full = resample(batches, [], method, origin, curated)
    kept = [b for b in batches if rng.random() < 0.6]
    part = resample(kept, [], method, origin, curated)
    for f, p in zip(full, part, strict=True):
        assert p.usable_ms <= f.usable_ms and p.gap_ms >= f.gap_ms
        if isinstance(p, Aggregate):
            assert isinstance(f, Aggregate)
    if not kept:
        assert all(isinstance(p, CuratedGap) and p.reason is GapReason.NOT_RECORDED for p in part)


@pytest.mark.parametrize("seed", SEEDS)
def test_p6_curation_is_independent_of_delivery_order(seed: int) -> None:
    rng = random.Random(6_400 + seed)
    batches = list(_batches())
    anns = [_annotation()]
    rng.shuffle(batches)
    m = _curated(REV1).method
    got = curate(_descriptor(), batches, anns, m, WINDOW, "standard_1min", _curated(REV0))
    assert got.content_hash() == GOLDEN_HASHES_IC14[REV1]


@pytest.mark.parametrize("seed", SEEDS)
def test_p7_curated_intervals_close_and_hold_a_value_only_with_enough_usable_time(
    seed: int,
) -> None:
    rng = random.Random(7_400 + seed)
    raw, curated = CASCADES[seed % 3]
    origin, batches = _random_window(rng, raw, curated)
    method = _random_method(rng, PERIOD_MS[curated], PERIOD_MS[raw])
    for i in resample(batches, [], method, origin, curated):
        assert i.usable_ms + i.rejected_ms + i.gap_ms == PERIOD_MS[curated]
        assert isinstance(i, Aggregate) == (i.usable_ms >= method.min_usable_ms)


@pytest.mark.parametrize("seed", SEEDS)
def test_p8_an_unsynchronised_block_never_curates_to_a_value(seed: int) -> None:
    rng = random.Random(8_400 + seed)
    raw, curated = CASCADES[seed % 3]
    origin, batches = _random_window(rng, raw, curated, keep=1.0)
    untrusted = [
        _batch(_expand(b), b.origin, resolution=raw, seq=b.seq, prev=_H, synced=False)
        for b in batches
    ]
    method = _method(min_usable_ms=PERIOD_MS[raw])
    for i in resample(untrusted, [], method, origin, curated):
        assert isinstance(i, CuratedGap) and i.usable_ms == 0
        assert i.reason in (GapReason.CLOCK_UNTRUSTED, GapReason.INSUFFICIENT_COVERAGE)


def test_p9_every_gb_day_has_46_48_or_50_settlement_periods_numbered_from_one() -> None:
    t, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2029, 1, 1, tzinfo=UTC)
    days: dict[date, list[int]] = {}
    while t < end:
        d, p = settlement_period_of(t)
        days.setdefault(d, []).append(p)
        t += timedelta(minutes=30)
    counts = Counter(len(v) for d, v in days.items() if date(2026, 1, 2) <= d < date(2028, 12, 31))
    assert set(counts) == {46, 48, 50} and counts[46] == counts[50] == 3
    for d, periods in days.items():
        assert periods == list(range(periods[0], periods[0] + len(periods)))
        if date(2026, 1, 2) <= d < date(2028, 12, 31):
            assert periods[0] == 1
    assert len(days[date(2027, 3, 28)]) == 46 and len(days[date(2027, 10, 31)]) == 50


@pytest.mark.parametrize("seed", SEEDS)
def test_p10_unit_conversions_bracket_the_exact_value(seed: int) -> None:
    rng = random.Random(10_400 + seed)
    for _ in range(200):
        v = rng.randint(-(10**12), 10**12)
        for down, by in ((w_to_kw, 1000), (wh_to_kwh, 1000), (millideg_to_tenths_c, 100)):
            lo, hi = down(v, "floor"), down(v, "ceil")
            assert lo * by <= v <= hi * by and hi - lo == (0 if v % by == 0 else 1)
        assert w_to_kw(kw_to_w(v), "floor") == v == w_to_kw(kw_to_w(v), "ceil")
        imp, exp = split_import_export_w(v)
        assert imp >= 0 and exp >= 0 and imp * exp == 0 and imp - exp == v


def test_p11_point_keys_round_trip_and_the_table_is_closed() -> None:
    accepted = 0
    for kind in AssetKind:
        for q in Quantity:
            for role in MeasurementRole:
                for aspect in Aspect:
                    raw = {
                        "asset_kind": kind.value,
                        "asset_id": "a-1",
                        "quantity": q.value,
                        "role": role.value,
                        "aspect": aspect.value,
                    }
                    ok = aspect in QUANTITY_TABLE[q].aspects and not (
                        role is MeasurementRole.SETPOINT_ECHO
                        and q
                        in (
                            Quantity.ENERGY_IMPORT,
                            Quantity.ENERGY_EXPORT,
                            Quantity.STATE_OF_ENERGY,
                            Quantity.STATE_OF_CHARGE,
                        )
                    )
                    if ok:
                        name = PointName.model_validate_json(json.dumps(raw))
                        assert parse_point_key(point_key(name)) == name
                        accepted += 1
                    else:
                        with pytest.raises(ValidationError):
                            PointName.model_validate_json(json.dumps(raw))
    assert accepted > 0


@pytest.mark.parametrize("seed", SEEDS)
def test_p12_the_opcua_mapping_is_injective_and_closed(seed: int) -> None:
    rng = random.Random(12_400 + seed)
    for member in QUALITY_MAP:
        assert from_opcua(to_opcua(member)) is member
    listed = {m.opcua_code for m in QUALITY_MAP.values()}
    for _ in range(200):
        code = rng.randrange(2**32)
        if code in listed:
            continue
        with pytest.raises(ValueError):
            from_opcua(code)


@pytest.mark.parametrize("seed", SEEDS)
def test_p13_the_tracker_accepts_any_delivery_order_and_refuses_only_a_fork(seed: int) -> None:
    rng = random.Random(13_400 + seed)
    in_order = TrackState(_descriptor().series_id)
    for b in _batches():
        in_order, _ = track(in_order, b)
    order = list(_batches())
    rng.shuffle(order)
    state = TrackState(_descriptor().series_id)
    for b in order:
        state, verdict = track(state, b)
        assert verdict.accepted and verdict.status is not TrackStatus.FORK
    assert state == in_order
    victim = rng.choice(list(_batches()))
    data = json.loads(victim.model_dump_json())
    seg = next(s for s in data["segments"] if s["kind"] == "values")
    seg["values"][rng.randrange(len(seg["values"]))] += 1
    after, verdict = track(state, TelemetryBatch.model_validate_json(json.dumps(data)))
    assert verdict.status is TrackStatus.FORK and not verdict.accepted and after is state


@pytest.mark.parametrize("seed", SEEDS)
def test_p14_data_age_never_shows_a_stale_value_and_ages_monotonically(seed: int) -> None:
    rng = random.Random(14_400 + seed)
    d, dec = _descriptor(), _allowed()
    bs = list(_batches())
    times = sorted(WINDOW + rng.randrange(0, 3_600_000) * MS for _ in range(30))
    last_age = None
    for t in times:
        r = age_at(dec, d, bs, t, 250)
        assert (r.state == "fresh") == isinstance(r.value, int)
        if r.state in ("stale", "lost"):
            assert isinstance(r.value, NotApplicable) and isinstance(r.quality, NotApplicable)
        if r.state in ("fresh", "stale", "lost"):
            assert isinstance(r.age_ms, int)
            if last_age is not None:
                assert r.age_ms >= last_age
            last_age = r.age_ms
        if isinstance(r.age_ms, int) and r.age_ms >= 0 and r.state != "future_dated":
            expect = (
                "lost"
                if r.age_ms >= d.freshness.lost_after_ms
                else "stale"
                if r.age_ms >= d.freshness.stale_after_ms
                else "fresh"
            )
            assert r.state == expect


# ------------------------------------------------------------------ mutation checks

_MALFORMED = "test_a_malformed_batch_is_refused"
_METHOD = "test_a_bad_method_is_refused"
_CURATED = "test_a_bad_curated_record_is_refused"
_DESCRIPTOR = "test_a_bad_descriptor_is_refused"
_TENANT = "test_the_tenant_key_rules_hold_on_every_record"

MUTATIONS = [
    (
        "segments start where the last ended",
        "        if s.first_slot != nxt:\n",
        "        if False:\n",
        f"{_MALFORMED}[hole]",
    ),
    (
        "adjacent runs of one kind are merged",
        "        if i and _same_run(segments[i - 1], s):\n",
        "        if False:\n",
        f"{_MALFORMED}[unmerged]",
    ),
    (
        "the segments reach the last slot",
        "    if nxt != slot_count:\n",
        "    if False:\n",
        f"{_MALFORMED}[short]",
    ),
    (
        "insufficient_coverage is curated only",
        "            if isinstance(s, GapRun) and s.reason is GapReason.INSUFFICIENT_COVERAGE:\n",
        "            if False:\n",
        f"{_MALFORMED}[insufficient-coverage-in-raw]",
    ),
    (
        "the genesis hash is exactly seq 0",
        "        if (self.seq == 0) != (self.prev_batch_hash == GENESIS_PREV_HASH):\n",
        "        if False:\n",
        f"{_MALFORMED}[genesis]",
    ),
    (
        "a batch is sealed after its block",
        "        if self.sealed_at < batch_end(self):\n",
        "        if False:\n",
        f"{_MALFORMED}[sealed-early]",
    ),
    (
        "a value is inside its quantity's range",
        "                not spec.minimum <= v <= spec.maximum for v in s.values\n",
        "                False for v in s.values\n",
        f"{_MALFORMED}[out-of-quantity-range]",
    ),
    (
        "an origin is on the block grid",
        "    if _ms_since_epoch(origin) % BLOCK_SPAN_MS[resolution]:\n",
        "    if False:\n",
        f"{_MALFORMED}[misaligned]",
    ),
    (
        "a synchronised clock states its bound",
        '        if self.clock_state == "synchronised" and not isinstance('
        "self.clock_offset_bound_ms, int):\n",
        "        if False:\n",
        f"{_MALFORMED}[synchronised-without-bound]",
    ),
    (
        "an unsynchronised block curates to clock_untrusted",
        "                if untrusted:\n",
        "                if False:\n",
        "test_an_unsynchronised_block_curates_to_clock_untrusted_gaps",
    ),
    (
        "an interval holds a value only with enough usable time",
        "        if tally.usable_ms >= method.min_usable_ms:\n",
        "        if tally.usable_ms > 0:\n",
        "test_the_fixture_curates_as_the_story_says",
    ),
    (
        "uncovered time is not_recorded",
        "        if missing:\n",
        "        if False:\n",
        "test_the_fixture_curates_as_the_story_says",
    ),
    (
        "an applied annotation rejects the time it covers",
        "                    hit = any(a.start < at + raw_period * _MS and at < a.end for a in "
        "applied)\n",
        "                    hit = False\n",
        "test_the_fixture_curates_as_the_story_says",
    ),
    (
        "curated derives from raw only",
        "    if any(not isinstance(b, TelemetryBatch) for b in batches):\n",
        "    if False:\n",
        "test_curated_derives_from_raw_only",
    ),
    (
        "a held value is never usable",
        "        if ValueQuality.HELD in self.usable:\n",
        "        if False:\n",
        f"{_METHOD}[held-usable]",
    ),
    (
        "a gap finding is never applied",
        "        if FindingKind.GAP in self.applied_findings:\n",
        "        if False:\n",
        f"{_METHOD}[gap-applied]",
    ),
    (
        "an interval closes on its period",
        "            if i.usable_ms + i.rejected_ms + i.gap_ms != period:\n",
        "            if False:\n",
        f"{_CURATED}[closure]",
    ),
    (
        "an aggregate has enough usable time",
        "            if isinstance(i, Aggregate) and i.usable_ms < self.method.min_usable_ms:\n",
        "            if False:\n",
        f"{_CURATED}[thin-aggregate]",
    ),
    (
        "a curated gap has too little usable time",
        "            if isinstance(i, CuratedGap) and i.usable_ms >= self.method.min_usable_ms:\n",
        "            if False:\n",
        f"{_CURATED}[fat-gap]",
    ),
    (
        "a curated period is strictly coarser",
        "        if not (period > source and period % source == 0):\n",
        "        if False:\n",
        f"{_CURATED}[not-coarser]",
    ),
    (
        "a curated record recomputes from its raw",
        '        raise ValueError("the record does not recompute from its raw")\n',
        "        pass\n",
        "test_curated_records_recompute_by_hash_and_supersede_in_order",
    ),
    (
        "late_raw reads strictly more raw",
        "        if not _covered_blocks(new) > _covered_blocks(old):\n",
        "        if False:\n",
        "test_a_supersession_must_bear_out_its_reason_and_a_fork_is_refused",
    ),
    (
        "current_revision refuses a fork",
        "    if any(len(v) > 1 for v in by_parent.values()):\n",
        "    if False:\n",
        "test_a_supersession_must_bear_out_its_reason_and_a_fork_is_refused",
    ),
    (
        "a superseded annotation is not effective",
        '        if h not in superseded and items[h].disposition == "asserted"\n',
        '        if items[h].disposition == "asserted"\n',
        "test_a_withdrawn_annotation_stops_rejecting_and_the_change_is_a_revision",
    ),
    (
        "the tracker tells a fork from a duplicate",
        "        status = TrackStatus.DUPLICATE if held[batch.seq][0] == h else TrackStatus.FORK\n",
        "        status = TrackStatus.DUPLICATE\n",
        "test_the_tracker_refuses_a_fork_and_a_changed_descriptor_only",
    ),
    (
        "the tracker refuses a changed descriptor inside an epoch",
        "    if known is not None and known != batch.descriptor_hash:\n",
        "    if False:\n",
        "test_the_tracker_refuses_a_fork_and_a_changed_descriptor_only",
    ),
    (
        "a chain links by prev hash",
        "            if b.prev_batch_hash != prev.content_hash():\n",
        "            if False:\n",
        "test_the_fixture_chain_verifies_across_the_reboot_and_a_tamper_breaks_it",
    ),
    (
        "a batch is bound to its descriptor",
        "    if batch.descriptor_hash != descriptor.content_hash():\n",
        "    if False:\n",
        "test_a_batch_binds_its_descriptor_and_a_good_value_stays_plausible",
    ),
    (
        "a good value stays inside the plausible bounds",
        "            if any(not lo <= v <= hi for v in s.values):\n",
        "            if False:\n",
        "test_a_batch_binds_its_descriptor_and_a_good_value_stays_plausible",
    ),
    (
        "an unlisted OPC UA code raises",
        "        return _BY_CODE[code]\n",
        "        return _BY_CODE.get(code, GapReason.CONFIGURATION_ERROR)\n",
        "test_an_unlisted_opcua_code_raises",
    ),
    (
        "a point is inside the quantity table",
        "        if self.aspect not in QUANTITY_TABLE[self.quantity].aspects:\n",
        "        if False:\n",
        "test_a_point_outside_the_table_is_refused[aspect]",
    ),
    (
        "a setpoint never echoes a register",
        "        if self.role is MeasurementRole.SETPOINT_ECHO and self.quantity in _NOT_ECHOED:\n",
        "        if False:\n",
        "test_a_point_outside_the_table_is_refused[echo]",
    ),
    (
        "a quantity has one unit",
        "        if self.unit is not spec.unit:\n",
        "        if False:\n",
        f"{_DESCRIPTOR}[unit]",
    ),
    (
        "a metered role names its meter",
        "        if metered == isinstance(self.meter, NotApplicable):\n",
        "        if False:\n",
        f"{_DESCRIPTOR}[meter-missing]",
    ),
    (
        "a simulated source is a synthetic fixture",
        '            != (self.record_class == "synthetic_fixture")\n',
        "            and False\n",
        f"{_DESCRIPTOR}[simulated-live]",
    ),
    (
        "staleness comes after a block span",
        "        if not span < self.freshness.stale_after_ms < self.freshness.lost_after_ms:\n",
        "        if False:\n",
        f"{_DESCRIPTOR}[freshness]",
    ),
    (
        "site_id is the tenant's site",
        "        if self.site_id != self.tenant.site_id:\n",
        "        if False:\n",
        f"{_TENANT}[site-mismatch]",
    ),
    (
        "series_id starts with the tenant key",
        "        if not self.series_id.startswith(prefix):\n",
        "        if False:\n",
        f"{_TENANT}[series-prefix]",
    ),
    (
        "synthetic ids and synthetic records go together",
        "            if label.startswith(_SYNTHETIC) != synthetic:\n",
        "            if False:\n",
        f"{_TENANT}[synthetic-live-record]",
    ),
    (
        "a Neura-owned series is neura_operational",
        '        if (self.tenant.org_kind == "neura") != '
        '(self.data_class == "neura_operational"):\n',
        "        if False:\n",
        f"{_TENANT}[neura-class]",
    ),
    (
        "a read decision is allowed only under a covering scope",
        '        if (self.outcome == "allowed") != granted:\n',
        "        if False:\n",
        "test_a_forged_read_decision_is_refused",
    ),
    (
        "a denied decision reads nothing",
        '    if decision.outcome != "allowed":\n',
        "    if False:\n",
        "test_a_denied_decision_reads_nothing",
    ),
    (
        "a decision reads only its own tenant",
        "        if r.tenant != decision.tenant:\n",
        "        if False:\n",
        "test_a_decision_for_one_tenant_reads_no_other",
    ),
    (
        "a transform refuses two tenants",
        "    if len(tenants) > 1:\n",
        "    if False:\n",
        "test_a_transform_refuses_two_tenants",
    ),
    (
        "a value shows only while fresh",
        "        if fresh != isinstance(self.value, int) or "
        "fresh != isinstance(self.quality, str):\n",
        "        if False:\n",
        "test_data_age_shows_a_value_only_while_fresh",
    ),
    (
        "at a threshold the worse state holds",
        "    if age >= policy.lost_after_ms:\n",
        "    if age > policy.lost_after_ms:\n",
        "test_data_age_shows_a_value_only_while_fresh",
    ),
    (
        "a newest block on an untrusted clock reads clock_untrusted",
        '    if newest.clock.clock_state == "unsynchronised":\n',
        "    if False:\n",
        "test_data_age_shows_a_value_only_while_fresh",
    ),
    (
        "a held value never makes a series fresh",
        "            if isinstance(s, ValueRun) and s.quality is not ValueQuality.HELD:\n",
        "            if isinstance(s, ValueRun):\n",
        "test_a_held_value_never_reads_as_fresh",
    ),
    (
        "a kW figure rounds to its stated side",
        '    return value // by if side == "floor" else -(-value // by)\n',
        "    return value // by\n",
        "test_a_kw_figure_states_its_rounding_side",
    ),
    (
        "settlement periods count from one",
        "    return local.date(), (start - midnight) // timedelta(minutes=30) + 1\n",
        "    return local.date(), (start - midnight) // timedelta(minutes=30)\n",
        "test_settlement_periods_are_counted_from_local_midnight",
    ),
    (
        "a withdrawal names what it withdraws",
        '        if self.disposition == "withdrawn" and '
        "isinstance(self.supersedes, NotApplicable):\n",
        "        if False:\n",
        "test_a_withdrawn_annotation_stops_rejecting_and_the_change_is_a_revision",
    ),
]


def _run_mutant(old: str, new: str, test: str) -> int:
    return run_mutant(
        "gridq.contracts.telemetry_series", old, new, f"{Path(__file__).resolve()}::{test}"
    )


@pytest.mark.parametrize("test", sorted({m[3] for m in MUTATIONS}))
def test_mutation_runner_passes_on_the_unmutated_source(test: str) -> None:
    """Control: the harness itself is sound, so a red mutant below means the guard bit."""
    assert _run_mutant("", "", test) == 0, test


@pytest.mark.parametrize("guard,old,new,test", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_reverting_a_guard_turns_its_test_red(guard: str, old: str, new: str, test: str) -> None:
    source = Path(telemetry_series.__file__).read_text(encoding="utf-8")
    assert source.count(old) == 1, guard
    # Exit 1 is pytest's "tests ran and failed": the guard is load-bearing. A mutant that cannot
    # load exits 3, and a collection error exits 2, so neither passes as red.
    assert _run_mutant(old, new, test) == 1, guard
