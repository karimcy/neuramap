"""IC-15 synthetic fixtures (WP-GRC-09): two signed copies and the executed terms made from them.

- ``v1_2_0/ic4_master_services_agreement``: an IC-4 1.2.0 evidence object, the customer's signed
  copy of an MSA made from the ``software_dispatch_msa`` template.
- ``v1_2_0/ic4_lender_reliance_letter``: an IC-4 1.2.0 evidence object, the lender's countersigned
  copy of a reliance letter.
- ``v1_2_0/ic15_executed_msa_terms``: the MSA's executed terms. The parties, the site and the dates
  are synthetic. **Every commercial term is `Unknown` with its ask**, because no price, cap, service
  level or period is on record and a fixture must not invent one.
- ``v1_2_0/ic15_executed_reliance_letter_terms``: the reliance letter's executed terms. The IC-1
  and IC-2 materials are bound by content hash to the committed 1.1.0 fixtures, and the dispatch
  envelope to the committed IC-3 1.0.0 hybrid BESS fixture (``fixtures/ic3/``), which binds those
  same IC-1 and IC-2 fixtures: one synthetic site, one consistent set of relied materials. IC-3 was
  frozen by WP-SIZE-07; before then the envelope was `Unknown`.

All values are synthetic. Party names say ``SYNTHETIC`` and are not real companies. Each document
hash is the SHA-256 of ``"SYNTHETIC DOCUMENT: " + title``; no real document exists. The template
text hashes come from the committed manifest. ``test_ic15_contract_templates`` asserts that the
committed files equal a fresh build. Regenerate with ``python tests/contracts/_ic15_fixtures.py``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

FIXTURES_V120 = Path(__file__).resolve().parent / "fixtures" / "v1_2_0"
MSA_COPY = FIXTURES_V120 / "ic4_master_services_agreement.synthetic.json"
RELIANCE_COPY = FIXTURES_V120 / "ic4_lender_reliance_letter.synthetic.json"
MSA_TERMS = FIXTURES_V120 / "ic15_executed_msa_terms.synthetic.json"
RELIANCE_TERMS = FIXTURES_V120 / "ic15_executed_reliance_letter_terms.synthetic.json"

#: The committed IC-1 and IC-2 1.1.0 fixtures and the IC-3 1.0.0 fixture the reliance letter
#: binds, by file. The IC-3 envelope itself references the IC-1 and IC-2 fixtures by hash.
IC1_FIXTURE = FIXTURES_V120.parent / "v1_1_0" / "ic1_hybrid_evidence_backed.synthetic.json"
IC2_FIXTURE = FIXTURES_V120.parent / "v1_1_0" / "ic2_modelled_v110.synthetic.json"
IC3_FIXTURE = FIXTURES_V120.parent / "ic3" / "ic3_envelope_hybrid_bess.synthetic.json"
SITE_ID = "SYNTHETIC-SITE-HYBRID"


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def _document_hash(title: str) -> str:
    return "sha256:" + hashlib.sha256(f"SYNTHETIC DOCUMENT: {title}".encode()).hexdigest()


def _signed_copy(evidence_type: str, title: str, name: str, role: str, ref: str) -> dict[str, Any]:
    return {
        "contract": "IC-4",
        "contract_version": "1.2.0",
        "record_class": "synthetic_fixture",
        "evidence_type": evidence_type,
        "title": title,
        "issuer": {"name": name, "role": role, "reference": ref},
        "document_hash": _document_hash(title),
        "issued_on": "2026-11-24",
        "effective_from": "2026-11-24",
        "expiry": {"kind": "no_expiry", "reason": "synthetic fixture: no term is agreed"},
        "scope": {"kind": "site", "site_id": SITE_ID},
        "verification": {
            "kind": "unverified",
            "reason": "synthetic fixture: not checked against any signed copy",
        },
    }


def build_msa_copy() -> dict[str, Any]:
    return _signed_copy(
        "master_services_agreement",
        "Synthetic signed Software and Dispatch MSA (schema-test fixture)",
        "SYNTHETIC-CUSTOMER (fixture; not a real company)",
        "customer",
        "SYNTH-MSA-0001",
    )


def build_reliance_copy() -> dict[str, Any]:
    return _signed_copy(
        "lender_reliance_letter",
        "Synthetic countersigned lender reliance letter (schema-test fixture)",
        "SYNTHETIC-LENDER (fixture; not a real bank)",
        "lender",
        "SYNTH-RELIANCE-0001",
    )


def _contracts() -> Any:
    from gridq.contracts import contract_templates, evidence

    return contract_templates, evidence


def _ref_to(copy: dict[str, Any]) -> dict[str, Any]:
    _, ev = _contracts()
    obj = ev.EvidenceObject.model_validate_json(json.dumps(copy))
    return ev.evidence_ref(obj).model_dump(mode="json")


def _unknown(ask: str) -> dict[str, str]:
    return {"kind": "unknown", "reason": ask}


def _text(value: str) -> dict[str, str]:
    return {"kind": "text", "value": value}


def _date(value: str) -> dict[str, str]:
    return {"kind": "date", "value": value}


def _terms(template_id: str, copy: dict[str, Any], known: dict[str, Any]) -> dict[str, Any]:
    """Executed terms for ``template_id``: ``known`` fields as given, every other field `Unknown`
    with the manifest's ask. Fields in manifest order."""
    ct, _ = _contracts()
    entry = ct.load_template_set().template(ct.TemplateId(template_id))
    values = [
        {"field_id": f.field_id, "value": known.get(f.field_id, _unknown(f.ask))}
        for f in entry.required_fields
    ]
    return {
        "contract": "IC-15",
        "contract_version": "1.0.0",
        "template_id": template_id,
        "template_version": entry.template_version,
        "text_sha256": entry.text_sha256,
        "executed_copy": _ref_to(copy),
        "variant": "direct_host_funded",
        "values": values,
    }


def build_msa_terms() -> dict[str, Any]:
    return _terms(
        "software_dispatch_msa",
        build_msa_copy(),
        {
            "provider_legal_name": _text("SYNTHETIC-PROVIDER (fixture; not a real company)"),
            "customer_legal_name": _text("SYNTHETIC-CUSTOMER (fixture; not a real company)"),
            "effective_date": _date("2026-11-24"),
            "site_reference": _text(SITE_ID),
            "initial_control_stage": _text("l0_read_only"),
            "asset_owner": _text("SYNTHETIC-CUSTOMER, who is also the host (fixture)"),
            "it_outage_sublimit": {
                "kind": "not_applicable",
                "reason": "synthetic fixture: no sublimit chosen",
            },
        },
    )


def _instance(path: Path, model: str) -> dict[str, Any]:
    from gridq.contracts import connection, curtailment, dispatch_envelope

    cls = {
        "ic1": connection.ConnectionAgreement,
        "ic2": curtailment.CurtailmentScenarioSet,
        "ic3": dispatch_envelope.DispatchEnvelope,
    }[model]
    obj = cls.model_validate_json(path.read_text(encoding="utf-8"))
    return {
        "kind": "contract_instance",
        "contract": obj.contract,
        "contract_version": obj.contract_version,
        "content_hash": obj.content_hash(),
    }


def build_reliance_terms() -> dict[str, Any]:
    return _terms(
        "lender_reliance_letter",
        build_reliance_copy(),
        {
            "provider_legal_name": _text("SYNTHETIC-PROVIDER (fixture; not a real company)"),
            "lender_legal_name": _text("SYNTHETIC-LENDER (fixture; not a real bank)"),
            "effective_date": _date("2026-11-24"),
            "relied_connection_agreement": _instance(IC1_FIXTURE, "ic1"),
            "relied_curtailment_scenarios": _instance(IC2_FIXTURE, "ic2"),
            "relied_dispatch_envelope": _instance(IC3_FIXTURE, "ic3"),
        },
    )


BUILDERS = {
    MSA_COPY: build_msa_copy,
    RELIANCE_COPY: build_reliance_copy,
    MSA_TERMS: build_msa_terms,
    RELIANCE_TERMS: build_reliance_terms,
}


def main() -> None:
    FIXTURES_V120.mkdir(exist_ok=True)
    for path, build in BUILDERS.items():
        path.write_text(render(build()), encoding="utf-8")


if __name__ == "__main__":
    main()
