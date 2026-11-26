"""Write or check the committed JSON Schema of each frozen contract version.

``python -m gridq.contracts --check`` exits 1 if a model no longer matches its committed schema
(the model changed without a version bump), or if an archived schema file's bytes no longer match
their pinned SHA-256. ``--write`` regenerates the live files; use it only together with a
deliberate ``CONTRACT_VERSION`` bump and a CHANGELOG entry. It refuses (exit 2, nothing written)
if any file it would write is an archived file whose text would change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType

from pydantic import BaseModel

# IC-9 (WP-SITE-07) also publishes a generated human-readable specification (`_specdoc`).
from gridq.contracts import (
    _specdoc,
    audit,
    compute_turndown,
    connection,
    contract_templates,
    curtailment,
    dispatch_envelope,
    edge_cloud,
    evidence,
    identity,
    telemetry_series,
    thermal_flex,
)

# IC-12 is the GridQ chassis. Its models live in `gridq.schemas`, which itself imports the
# reference types of this package, so this checker is the one file here that reads outside the
# package. The contract modules stay a leaf (`tests/contracts/test_ic12_chassis.py`).
from gridq.schemas import chassis

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

#: (file stem, model, version) for every frozen contract.
FROZEN: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "ic9_compute_turndown_declaration",
        compute_turndown.ComputeTurndownDeclaration,
        compute_turndown.CONTRACT_VERSION,
    ),
    ("ic9_resolved_timeline", compute_turndown.ResolvedTimeline, compute_turndown.CONTRACT_VERSION),
    ("ic4_evidence_object", evidence.EvidenceObject, evidence.CONTRACT_VERSION),
    ("ic1_connection_agreement", connection.ConnectionAgreement, connection.CONTRACT_VERSION),
    (
        "ic2_curtailment_scenario_set",
        curtailment.CurtailmentScenarioSet,
        curtailment.CONTRACT_VERSION,
    ),
    ("ic13_identity_claims", identity.IdentityClaims, identity.CONTRACT_VERSION),
    ("ic13_audit_event", audit.AuditEvent, audit.CONTRACT_VERSION),
    (
        "ic11_thermal_flex_availability",
        thermal_flex.ThermalFlexAvailability,
        thermal_flex.CONTRACT_VERSION,
    ),
    ("ic11_flex_instruction", thermal_flex.FlexInstruction, thermal_flex.CONTRACT_VERSION),
    ("ic11_flex_acknowledgement", thermal_flex.FlexAcknowledgement, thermal_flex.CONTRACT_VERSION),
    (
        "ic3_dispatch_envelope",
        dispatch_envelope.DispatchEnvelope,
        dispatch_envelope.CONTRACT_VERSION,
    ),
    (
        "ic3_resolved_timeline",
        dispatch_envelope.ResolvedTimeline,
        dispatch_envelope.CONTRACT_VERSION,
    ),
    ("ic5_cloud_to_edge_frame", edge_cloud.CloudToEdgeFrame, edge_cloud.CONTRACT_VERSION),
    ("ic5_edge_to_cloud_frame", edge_cloud.EdgeToCloudFrame, edge_cloud.CONTRACT_VERSION),
    ("ic5_link_profile", edge_cloud.LinkProfile, edge_cloud.CONTRACT_VERSION),
    *((stem, model, chassis.CONTRACT_VERSION) for stem, model in chassis.FROZEN_MODELS),
    (
        "ic15_contract_template_set",
        contract_templates.ContractTemplateSet,
        contract_templates.CONTRACT_VERSION,
    ),
    (
        "ic15_executed_contract_terms",
        contract_templates.ExecutedContractTerms,
        contract_templates.CONTRACT_VERSION,
    ),
    (
        "ic14_series_descriptor",
        telemetry_series.SeriesDescriptor,
        telemetry_series.CONTRACT_VERSION,
    ),
    ("ic14_telemetry_batch", telemetry_series.TelemetryBatch, telemetry_series.CONTRACT_VERSION),
    ("ic14_curated_series", telemetry_series.CuratedSeries, telemetry_series.CONTRACT_VERSION),
    ("ic14_dq_annotation", telemetry_series.DqAnnotation, telemetry_series.CONTRACT_VERSION),
)

#: Archived schema files: tagged contract versions that a later version supersedes (or will),
#: kept byte for byte. File name -> SHA-256 of its bytes. ``--check`` verifies each one, and
#: ``--write`` never changes one. Added with IC-4 1.1.0 (WP-BO-06); the hashes are those of the
#: files at the ``v1.0.0`` tags. IC-4 1.2.0 (WP-GRC-09) archives the IC-4 ``v1.1.0`` file too, at
#: its hash under the tag ``contracts/ic4-evidence-object/v1.1.0``.
ARCHIVED: dict[str, str] = {
    "ic4_evidence_object.v1.0.0.schema.json": (
        "d457702203c285245704a29c3f8472cf8daa4413462db01d3de910ec73ebdf80"
    ),
    "ic1_connection_agreement.v1.0.0.schema.json": (
        "1c62e7139ab3a27171cbc5302fd0230a58ee99336877bf3d5e046407215b73b3"
    ),
    "ic2_curtailment_scenario_set.v1.0.0.schema.json": (
        "fd9e588cb7b061daa5370eac4b08ccfba39c91276e7bcd289d37d259c92c0318"
    ),
    "ic11_thermal_flex_availability.v1.0.0.schema.json": (
        "8ee3297cdea7d365e8a71be5ac0e037363227e6668603abde00cb35745c8dbfd"
    ),
    "ic11_flex_instruction.v1.0.0.schema.json": (
        "363f44f25a33cc0fc3098ea5252611977b4987c97305b4628b963b7a6355ad42"
    ),
    "ic11_flex_acknowledgement.v1.0.0.schema.json": (
        "2a44df0094548fe7a14104d82f49930454f0e5f1e0335bdff1d3d0e2af7b05d4"
    ),
    "ic4_evidence_object.v1.1.0.schema.json": (
        "f81888ab7d964c800717f94b18b7411f56ab3c142cbaba683c4112313e838ee4"
    ),
    # IC-12 1.0.0: the chassis at the ``gridq_clean_baseline`` tag, rendered by WP-CORE-03 and
    # superseded by 2.0.0. The hashes are those of the files at ``contracts/ic12-chassis/v1.0.0``.
    "ic12_site_model.v1.0.0.schema.json": (
        "490f4927ad35362dcb272a5bb7c8c9b3b771852f90591041baec8e28880e8c1a"
    ),
    "ic12_optimisation_result.v1.0.0.schema.json": (
        "a9fb5541837599aec543a65d8973a0cc45c3ac8665591970a06026d98acbe18e"
    ),
    "ic12_quote_record.v1.0.0.schema.json": (
        "455f95bc3dc7fa7b77ab3581e7a68e3786af08004e224c144ef0d885d6cdd4f6"
    ),
    # IC-12 2.0.0 (release ``gridq_chassis_v2``), superseded by the additive minor 2.1.0
    # (WP-UW-03, D79). The hashes are those of the files at ``contracts/ic12-chassis/v2.0.0``.
    "ic12_site_model.v2.0.0.schema.json": (
        "245a0be2bbd6ebf5db7aac7c580818890f9ed9d655e427d55a0f002c48f38d1f"
    ),
    "ic12_optimisation_result.v2.0.0.schema.json": (
        "302e69c183d3a930cd5b2fd86484814eaf98cdcceab7090520780a53b4f9d3e1"
    ),
    "ic12_quote_record.v2.0.0.schema.json": (
        "236449bb9128d8478deaec23a65aabb6a4a5b479af10c312e7ad81151141a0a2"
    ),
    # IC-12 2.1.0 (release ``gridq_chassis_v2``), superseded by the major 3.0.0 (WP-UW-07, D103
    # and D104: a required tariff basis and provenance), which was rendered on it. The hashes are
    # those of the files at ``contracts/ic12-chassis/v2.1.0``. The site and result files are the
    # 2.0.0 files byte for byte (2.1.0 moved only `QuoteRecord`), so their pins repeat.
    "ic12_site_model.v2.1.0.schema.json": (
        "245a0be2bbd6ebf5db7aac7c580818890f9ed9d655e427d55a0f002c48f38d1f"
    ),
    "ic12_optimisation_result.v2.1.0.schema.json": (
        "302e69c183d3a930cd5b2fd86484814eaf98cdcceab7090520780a53b4f9d3e1"
    ),
    "ic12_quote_record.v2.1.0.schema.json": (
        "2559daea119adfb8a2230af2555867fc056b01a7a7619cc89401d85a1455010b"
    ),
    # IC-12 3.0.0 (release ``gridq_chassis_v3``, WP-UW-07), superseded by the additive minor 3.1.0
    # (WP-UW-17, D128 to D132). The hashes are those of the files at
    # ``contracts/ic12-chassis/v3.0.0``.
    # The result file is the 2.0.0 and 2.1.0 file byte for byte, so its pin repeats.
    "ic12_site_model.v3.0.0.schema.json": (
        "0f2868cd9aaa996756f235b8dc9cf45b10f14a20c153a775a486513b266e844d"
    ),
    "ic12_optimisation_result.v3.0.0.schema.json": (
        "302e69c183d3a930cd5b2fd86484814eaf98cdcceab7090520780a53b4f9d3e1"
    ),
    "ic12_quote_record.v3.0.0.schema.json": (
        "3c52e3724290081c84f3e9a7e4be88508036797f9494f22e169f912a6e710d21"
    ),
}


#: Generated specifications: (spec stem, contract module, the (schema stem, version) pairs it
#: documents). ``--check`` fails when a committed spec differs from a fresh render (`_specdoc`).
SPECS: tuple[tuple[str, ModuleType, tuple[tuple[str, str], ...]], ...] = (
    (
        "ic9_compute_turndown_declaration",
        compute_turndown,
        (
            ("ic9_compute_turndown_declaration", compute_turndown.CONTRACT_VERSION),
            ("ic9_resolved_timeline", compute_turndown.CONTRACT_VERSION),
        ),
    ),
    (
        "ic14_telemetry_series",
        telemetry_series,
        tuple(
            (stem, telemetry_series.CONTRACT_VERSION)
            for stem in (
                "ic14_series_descriptor",
                "ic14_telemetry_batch",
                "ic14_curated_series",
                "ic14_dq_annotation",
            )
        ),
    ),
)


def render_specs(schema_dir: Path, spec_dir: Path) -> list[tuple[Path, str]]:
    """(path in ``spec_dir``, text) of every generated specification, rendered from the schemas
    in ``schema_dir``."""
    return [
        (
            _specdoc.spec_path(stem, module.CONTRACT_VERSION, spec_dir),
            _specdoc.render_spec(module, schemas, schema_dir),
        )
        for stem, module, schemas in SPECS
    ]


def schema_path(stem: str, version: str, schema_dir: Path | None = None) -> Path:
    return (SCHEMA_DIR if schema_dir is None else schema_dir) / f"{stem}.v{version}.schema.json"


def render(model: type[BaseModel]) -> str:
    schema = model.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(schema_dir: Path) -> int:
    # Render everything first: if any archived file would change, write nothing at all.
    plan = [(schema_path(s, v, schema_dir), render(m)) for s, m, v in FROZEN]
    refused = [
        path
        for path, text in plan
        if path.name in ARCHIVED
        and (
            _sha256(text.encode("utf-8")) != ARCHIVED[path.name]
            or not path.exists()
            or path.read_bytes() != text.encode("utf-8")
        )
    ]
    if refused:
        for path in refused:
            print(f"REFUSED: {path.name} is archived and its text would change; nothing written")
        return 2
    # Past the refusal, an archived path's text equals its pinned bytes: rewriting is a no-op.
    for path, text in plan:
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.name}")
    # The committed specs beside the committed schemas; a scratch schema dir gets a scratch one.
    spec_dir = _specdoc.SPEC_DIR if schema_dir == SCHEMA_DIR else schema_dir.parent / "specs"
    for path, text in render_specs(schema_dir, spec_dir):
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.name}")
    return 0


def _check(schema_dir: Path) -> int:
    bad = 0
    for stem, model, version in FROZEN:
        path, text = schema_path(stem, version, schema_dir), render(model)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            print(f"DRIFT: {model.__name__} no longer matches {path.name}")
            bad += 1
        else:
            print(f"ok    {path.name}")
    for name, pinned in ARCHIVED.items():
        path = schema_dir / name
        if not path.exists():
            print(f"DRIFT: archived {name} is missing")
            bad += 1
        elif _sha256(path.read_bytes()) != pinned:
            print(f"DRIFT: archived {name} no longer matches its pinned sha256")
            bad += 1
        else:
            print(f"ok    {name} (archived, sha256 pinned)")
    # Always against the committed specs: a byte-identical scratch copy renders them unchanged.
    for path, text in render_specs(schema_dir, _specdoc.SPEC_DIR):
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            print(f"DRIFT: {path.name} is not the specification its contract renders")
            bad += 1
        else:
            print(f"ok    {path.name} (generated specification)")
    return 1 if bad else 0


def main(argv: list[str] | None = None, schema_dir: Path | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    directory = SCHEMA_DIR if schema_dir is None else schema_dir
    return _write(directory) if args.write else _check(directory)


if __name__ == "__main__":
    sys.exit(main())
