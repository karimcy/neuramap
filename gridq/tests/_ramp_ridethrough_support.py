"""Shared, labelled **synthetic** inputs for the ``gridq.ramp_ridethrough`` tests (WP-SITE-06).

Every number here is an illustrative test input chosen to exercise a code path. None is a
default, a sourced figure, or a claim about any real site, network, battery or customer: the fault
level, X/R ratio and BESS ratings are SYNTHETIC. Every site id starts ``SYNTHETIC-`` and every
record is ``synthetic_fixture``.

The site is the committed IC-1 hybrid archetype (``SYNTHETIC-SITE-HYBRID``: 33 kV PCC, ramp terms
5.0 MW/min up and 10.0 MW/min down), so the ramp terms these tests read are the fixture's own.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from gridq.contracts._base import Unknown
from gridq.contracts.connection import ConnectionAgreement
from gridq.contracts.evidence import EvidenceObject, evidence_ref
from gridq.ramp_ridethrough import (
    BessDeclaration,
    Declarant,
    EvidencedQuantity,
    ReactiveRange,
    StudyInputs,
)
from gridq.ramp_ridethrough._common import rational

SITE = "SYNTHETIC-SITE-HYBRID"
FIX = Path(__file__).resolve().parent / "contracts" / "fixtures"
IC1_HYBRID = FIX / "ic1_archetype_hybrid.synthetic.json"
AS_OF = date(2026, 11, 26)

OEM = Declarant(name="SYNTHETIC-OEM (fixture)", role="oem", reference="SYNTH-DECL-0001")


def unknown(reason: str = "SYNTHETIC: not stated in this fixture") -> Unknown:
    return Unknown(kind="unknown", reason=reason)


def agreement(**changes: Any) -> ConnectionAgreement:
    """The IC-1 hybrid archetype fixture, with top-level fields replaced (then re-validated)."""
    data = json.loads(IC1_HYBRID.read_text())
    data.update(changes)
    return ConnectionAgreement.model_validate_json(json.dumps(data))


def doc_hash(label: str) -> str:
    return "sha256:" + hashlib.sha256(f"SYNTHETIC {label}".encode()).hexdigest()


def evidence(
    evidence_type: str,
    role: str,
    label: str,
    *,
    site: str = SITE,
    document_hash: str | None = None,
    verified: bool = True,
    expires: str = "2027-12-31",
) -> EvidenceObject:
    """A SYNTHETIC IC-4 object issued by a fixture party of ``role``."""
    return EvidenceObject.model_validate_json(
        json.dumps(
            {
                "record_class": "synthetic_fixture",
                "evidence_type": evidence_type,
                "title": f"SYNTHETIC {label} (schema-test fixture)",
                "issuer": {
                    "name": f"SYNTHETIC-{role.upper()} (fixture; not a real party)",
                    "role": role,
                    "reference": f"SYNTH-{label.upper().replace(' ', '-')}",
                },
                "document_hash": document_hash or doc_hash(label),
                "issued_on": "2026-10-01",
                "effective_from": "2026-10-01",
                "expiry": {"kind": "expires_on", "expires_on": expires},
                "scope": {"kind": "site", "site_id": site},
                "verification": (
                    {
                        "kind": "verified",
                        "verifier": "SYNTHETIC-VERIFIER (fixture)",
                        "verifier_role": "neura_internal",
                        "method": "confirmed_with_issuer",
                        "verified_on": "2026-10-15",
                    }
                    if verified
                    else {"kind": "unverified", "reason": "SYNTHETIC: not yet checked"}
                ),
            }
        )
    )


def declaration(**changes: Any) -> BessDeclaration:
    """A SYNTHETIC 5 MW BESS (G99 Type B at the fixture's 33 kV PCC)."""
    base: dict[str, Any] = {
        "site_id": SITE,
        "record_class": "synthetic_fixture",
        "declared_by": OEM,
        "registered_capacity_w": 5_000_000,
        "rated_import_power_w": 5_000_000,
        "reactive_range": ReactiveRange(
            kind="reactive_range", absorb_var=1_650_000, inject_var=1_650_000
        ),
        "ppc_ramp_towards_charge_w_per_min": 4_000_000,
        "ppc_ramp_towards_discharge_w_per_min": 8_000_000,
        "facility_registered_capacity_w": 5_000_000,
        "gb_region": "england_wales",
        "rvc_category": "category_2",
    }
    base.update(changes)
    return BessDeclaration(**base)


#: The study-input documents: (field, evidence type, issuer role).
DOCUMENTS = (
    ("pre_post_fault_short_circuit", "technical_report", "dno"),
    ("network_equivalent", "technical_report", "dno"),
    ("statutory_voltage_limits", "technical_report", "dno"),
    ("transformer_data", "technical_report", "customer"),
    ("transformer_saturation", "technical_report", "customer"),
    ("site_network_data", "technical_report", "customer"),
    ("customer_installation_equivalent", "technical_report", "customer"),
    ("oem_rms_model", "technical_report", "oem"),
    ("oem_emt_model", "technical_report", "oem"),
    ("ppc_settings", "technical_report", "oem"),
    ("protection_settings", "technical_report", "oem"),
    ("frt_manufacturer_information", "conformity_certificate", "oem"),
)

FAULT_LEVEL_EVIDENCE = evidence("operational_data_record", "dno", "minimum fault level letter")
XR_EVIDENCE = evidence("operational_data_record", "dno", "x over r letter")
DOCUMENT_EVIDENCE = {f: evidence(t, r, f.replace("_", " ")) for f, t, r in DOCUMENTS}


def study(
    *,
    sk_va: int | None = 200_000_000,
    x_over_r: int | None = 5,
    documents: bool = True,
    frt: bool = False,
    **changes: Any,
) -> StudyInputs:
    """SYNTHETIC study inputs: S"k 200 MVA and X/R 5 unless overridden (``None`` = unknown)."""
    base: dict[str, Any] = {
        "site_id": SITE,
        "record_class": "synthetic_fixture",
        "min_fault_level_va": (
            EvidencedQuantity(
                kind="evidenced_quantity",
                value=rational(sk_va),
                unit="VA",
                evidence=evidence_ref(FAULT_LEVEL_EVIDENCE),
            )
            if sk_va is not None
            else unknown()
        ),
        "x_over_r": (
            EvidencedQuantity(
                kind="evidenced_quantity",
                value=rational(x_over_r),
                unit="ratio",
                evidence=evidence_ref(XR_EVIDENCE),
            )
            if x_over_r is not None
            else unknown()
        ),
    }
    for field, _, _ in DOCUMENTS:
        base[field] = evidence_ref(DOCUMENT_EVIDENCE[field]) if documents else unknown()
    base["frt_study_report"] = evidence_ref(FRT_STUDY) if frt else unknown()
    base["dno_frt_acceptance"] = evidence_ref(G99_APPROVAL) if frt else unknown()
    base.update(changes)
    return StudyInputs(**base)


#: Both legs of a required_evidenced flag: a PSAL study report and a DNO G99 approval.
FRT_STUDY = evidence("technical_report", "research_partner", "psal frt study")
G99_APPROVAL = evidence("g99_approval", "dno", "g99 approval")


def all_study_evidence() -> tuple[EvidenceObject, ...]:
    return (FAULT_LEVEL_EVIDENCE, XR_EVIDENCE, *DOCUMENT_EVIDENCE.values(), FRT_STUDY, G99_APPROVAL)
