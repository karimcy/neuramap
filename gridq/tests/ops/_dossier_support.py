"""Labelled synthetic inputs for the candidate site dossier tests (WP-OPS-01).

Every site here is a SYNTHETIC- site or a fictitious ``fixture_`` site; every document, IC-1
instance, transcription, location and boundary dataset is a TEST FIXTURE. Nothing here describes
a real customer, site, network operator or document.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from gridq.contracts._base import NotApplicable
from gridq.contracts.connection import ConnectionAgreement
from gridq.contracts.evidence import EvidenceObject, EvidenceRef, evidence_ref
from gridq.ops.dossier_inputs import (
    IC1_DIR,
    TRANSCRIPTION_DIR,
    CandidateEntry,
    CandidateRegister,
)
from gridq.ops.dossier_model import PublicSource, SiteLocation
from gridq.ops.public_sources import BoundaryDataset, load_boundary_dataset
from gridq.schemas.references import connection_agreement_ref

REPO = Path(__file__).resolve().parents[2]
IC1_FIXTURES = REPO / "tests" / "contracts" / "fixtures"
SITE_TEMPLATE = REPO / "examples" / "site_t0.json"

AS_OF = date(2026, 11, 26)
SYN = "SYNTHETIC-OPS01-ALPHA"
SYN_B = "SYNTHETIC-OPS01-BRAVO"
#: A fictitious non-synthetic site, released by a fixture DPA (exercises the released route).
FIX = "fixture_ops01_site"
FIX_CUSTOMER = "fixture_ops01_customer"


def evidence(
    site_id: str,
    evidence_type: str,
    role: str,
    *,
    tag: str = "",
    record_class: str = "synthetic_fixture",
    verified: bool = True,
    validity_unknown: bool = False,
    expires_on: str | None = None,
    scope_site_id: str | None = None,
) -> EvidenceObject:
    """A TEST FIXTURE IC-4 object. Each keyword breaks one property of the admissible default."""
    return EvidenceObject.model_validate_json(
        json.dumps(
            {
                "contract": "IC-4",
                "contract_version": "1.2.0",
                "record_class": record_class,
                "evidence_type": evidence_type,
                "title": f"TEST FIXTURE {evidence_type} for {site_id} {tag}".strip(),
                "issuer": {
                    "name": f"TEST FIXTURE {role} (not a real party)",
                    "role": role,
                    "reference": f"TEST-{evidence_type}-{tag or 'x'}",
                },
                "document_hash": "sha256:"
                + hashlib.sha256(
                    f"TEST FIXTURE {site_id} {evidence_type} {tag}".encode()
                ).hexdigest(),
                "issued_on": "2026-01-01",
                "effective_from": "2026-01-01",
                "expiry": (
                    {"kind": "unknown", "reason": "TEST FIXTURE: term not stated"}
                    if validity_unknown
                    else {"kind": "expires_on", "expires_on": expires_on}
                    if expires_on
                    else {"kind": "no_expiry", "reason": "TEST FIXTURE: enduring"}
                ),
                "scope": {"kind": "site", "site_id": scope_site_id or site_id},
                "verification": (
                    {
                        "kind": "verified",
                        "verifier": "TEST FIXTURE verifier",
                        "verifier_role": "neura_internal",
                        "method": "confirmed_with_issuer",
                        "verified_on": "2026-02-01",
                    }
                    if verified
                    else {"kind": "unverified", "reason": "TEST FIXTURE: not yet checked"}
                ),
            }
        )
    )


def fixture_dpa(site_id: str) -> EvidenceObject:
    return evidence(site_id, "data_processing_agreement", "customer", record_class="registered")


def write_registry(root: Path, *objects: EvidenceObject) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for obj in objects:
        name = obj.content_hash().removeprefix("sha256:")[:16]
        (root / f"{name}.json").write_text(obj.model_dump_json(), encoding="utf-8")


def ic1(
    site_id: str,
    *,
    source: dict[str, Any] | EvidenceRef | None = None,
    operator: str | None = "nged",
    archetype: str = "firm",
    ref: str = "TEST-CA-01",
) -> ConnectionAgreement:
    """A TEST FIXTURE IC-1 instance from the contracts' archetype fixture of that name."""
    template = IC1_FIXTURES / f"ic1_archetype_{archetype}.synthetic.json"
    data = json.loads(template.read_text(encoding="utf-8"))
    data["contract_version"] = "1.1.0"
    data["site_id"] = site_id
    data["agreement_ref"] = ref
    data["network_operator"] = (
        operator
        if operator is not None
        else {"kind": "unknown", "reason": "TEST FIXTURE: operator not stated"}
    )
    if isinstance(source, EvidenceRef):
        data["source"] = {"kind": "evidence_backed", "evidence": source.model_dump(mode="json")}
    elif source is not None:
        data["source"] = source
    return ConnectionAgreement.model_validate_json(json.dumps(data))


def write_ic1(root: Path, agreement: ConnectionAgreement) -> None:
    d = root / IC1_DIR / agreement.site_id
    d.mkdir(parents=True, exist_ok=True)
    name = agreement.content_hash().removeprefix("sha256:")[:16]
    (d / f"{name}.json").write_text(agreement.model_dump_json(), encoding="utf-8")


def write_transcription(
    root: Path,
    site_id: str,
    field: str,
    value: dict[str, Any],
    *refs: EvidenceRef,
    record_class: str = "synthetic_fixture",
    locator: str = "TEST FIXTURE page 1",
) -> None:
    d = root / TRANSCRIPTION_DIR / site_id
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "record": "gridq.dossier_transcription",
        "record_version": "1.0.0",
        "record_class": record_class,
        "site_id": site_id,
        "field": field,
        "value": value,
        "evidence": [r.model_dump(mode="json") for r in refs],
        "locator": locator,
        "transcribed_on": "2026-11-20",
    }
    (d / f"{field}-{len(list(d.iterdir()))}.json").write_text(json.dumps(payload), "utf-8")


def site_record(
    path: Path,
    site_id: str,
    *,
    dno_region: str | None = "NGED_SW",
    mec_kw: float | None = 120.0,
    links: list[tuple[str, EvidenceRef]] | None = None,
    agreement: ConnectionAgreement | None = None,
) -> Path:
    """A TEST FIXTURE SiteModel JSON (from the public example site), written outside any root."""
    data = json.loads(SITE_TEMPLATE.read_text(encoding="utf-8"))
    data["site_id"] = site_id
    data["connection"]["dno_region"] = dno_region
    data["connection"]["mec_kw"] = mec_kw
    if agreement is not None:
        data["connection"]["agreement"] = connection_agreement_ref(agreement).model_dump(
            mode="json"
        )
    if links:
        data["evidence_refs"] = [
            {"supports": s, "evidence": r.model_dump(mode="json")} for s, r in links
        ]
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


LOCATION_INSIDE = {"latitude": 51.5, "longitude": -1.5}
LOCATION_NEAR_EDGE = {"latitude": 51.5, "longitude": -1.005}


def location(where: dict[str, float], basis: str = "synthetic_fixture") -> SiteLocation:
    return SiteLocation.model_validate_json(
        json.dumps(
            {
                **where,
                "basis": {
                    "basis": basis,
                    "citation": "TEST FIXTURE location",
                    "recorded_on": "2026-11-20",
                },
            }
        )
    )


#: A TEST FIXTURE boundary dataset: two invented licence areas split at longitude -1.0, with a
#: hole in the west area around (-1.8, 51.8).
BOUNDARY_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": "west",
            "properties": {"area": "TEST AREA WEST"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[-3.0, 50.0], [-1.0, 50.0], [-1.0, 53.0], [-3.0, 53.0], [-3.0, 50.0]],
                    [[-1.9, 51.7], [-1.7, 51.7], [-1.7, 51.9], [-1.9, 51.9], [-1.9, 51.7]],
                ],
            },
        },
        {
            "type": "Feature",
            "id": "east",
            "properties": {"area": "TEST AREA EAST"},
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [[[-1.0, 50.0], [1.0, 50.0], [1.0, 53.0], [-1.0, 53.0], [-1.0, 50.0]]]
                ],
            },
        },
    ],
}


def boundary(
    tmp: Path, *, record_class: str = "synthetic_fixture", doc: dict[str, Any] | None = None
) -> tuple[Path, PublicSource, BoundaryDataset]:
    data = json.dumps(doc or BOUNDARY_GEOJSON, sort_keys=True).encode()
    path = tmp / "boundaries.geojson"
    path.write_bytes(data)
    source = PublicSource(
        record_class=record_class,  # type: ignore[arg-type]
        title="TEST FIXTURE licence area boundaries",
        publisher="TEST FIXTURE publisher",
        url="https://example.invalid/test-fixture-boundaries.geojson",
        retrieved_on=date(2026, 11, 20),
        content_sha256="sha256:" + hashlib.sha256(data).hexdigest(),
        method="TEST FIXTURE: written by the test",
    )
    return path, source, load_boundary_dataset(path, source=source, area_property="area")


def entry(
    site_id: str,
    *,
    customer: str = "synthetic_customer",
    records: tuple[str, ...] = (),
    loc: SiteLocation | None = None,
) -> CandidateEntry:
    synthetic = site_id.startswith("SYNTHETIC-")
    return CandidateEntry(
        site_id=site_id,
        customer=customer,
        origin="synthetic" if synthetic else "owner_register",
        partner=NotApplicable(kind="not_applicable", reason="TEST FIXTURE"),
        record_paths=records,
        location=loc
        if loc is not None
        else NotApplicable(kind="not_applicable", reason="TEST FIXTURE: none"),
    )


def register(*entries: CandidateEntry) -> CandidateRegister:
    return CandidateRegister(entries=entries, exclusions=(), pipeline_asks=())


def full_synthetic_inputs(root: Path, site_id: str = SYN) -> None:
    """Every field of ``site_id`` evidenced through fixture IC-4, IC-1 and transcriptions."""
    ca = evidence(site_id, "connection_agreement", "dno", tag="ca")
    mpan = evidence(site_id, "mpan_registration", "elexon", tag="mpan")
    g99 = evidence(site_id, "g99_approval", "dno", tag="g99")
    tech = evidence(site_id, "technical_report", "independent_engineer", tag="tech")
    saia = evidence(site_id, "site_access_installation_agreement", "host", tag="saia")
    write_registry(root, ca, mpan, g99, tech, saia)
    write_ic1(root, ic1(site_id, source=evidence_ref(ca)))
    write_transcription(
        root,
        site_id,
        "metering_class",
        {"kind": "metering_class", "settlement": "sva", "measurement_class": "C"},
        evidence_ref(mpan),
    )
    write_transcription(
        root,
        site_id,
        "net_export",
        {
            "kind": "net_export",
            "measures": [{"basis": "agreed_export_capacity", "kw": 250.0}],
        },
        evidence_ref(g99),
    )
    write_transcription(
        root,
        site_id,
        "onsite_generation",
        {
            "kind": "onsite_generation",
            "statement": "assets_listed",
            "assets": [
                {
                    "technology": "diesel_genset",
                    "rated_kw": 500.0,
                    "operation": "standby_only",
                    "description": "TEST FIXTURE standby genset",
                }
            ],
        },
        evidence_ref(tech),
    )
    write_transcription(
        root,
        site_id,
        "cooling_topology",
        {
            "kind": "cooling_topology",
            "plant": ["air_cooled_chillers", "free_cooling"],
            "description": "TEST FIXTURE chilled water with free cooling",
        },
        evidence_ref(tech),
    )
    write_transcription(
        root,
        site_id,
        "bess_owner",
        {"kind": "bess_owner", "owner_type": "third_party", "owner_name": "TEST FIXTURE owner"},
        evidence_ref(saia),
        locator="TEST FIXTURE clause 4.1",
    )
