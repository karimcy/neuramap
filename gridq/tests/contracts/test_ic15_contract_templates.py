"""IC-15 ContractTemplateSet v1.0.0 (roadmap NEW-IC-15, WP-GRC-09; plan
docs/shared_work/wp_grc09_plan.md).

Each section maps to the work package's acceptance criteria and to the plan:

1. The version, the committed schemas and the committed manifest.
2. Acceptance 1: each template is registered as an IC-4 evidence type, with its required fields.
3. Acceptance 2: the MSA carries the safety hierarchy, the IT-outage carve-out, step-in and the
   liability cap as named clauses. It also carries the ADR-D2 staging and the responsibility
   boundary.
4. Acceptance 3: the templates reference IC-3 and IC-8 by contract version, not by prose. IC-3 is
   bound: WP-SIZE-07 froze it at 1.0.0, and the reliance letter fixture binds an IC-3 envelope by
   content hash. IC-8 completes when WP-LEDGER-07 freezes it.
5. Legal review: every template is a draft, and a review binds the reviewed text.
6. Prices and terms: only ADR-D1's four list prices; everything else is to be agreed.
7. One executed contract: `ExecutedContractTerms` and `check_executed_terms`.
8. No None, the guard words, and the canonical hash.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import types
import typing
from typing import Any

import pytest
from _contracts_support import FIXTURES_V110
from _ic15_fixtures import (
    BUILDERS,
    IC1_FIXTURE,
    IC2_FIXTURE,
    IC3_FIXTURE,
    MSA_COPY,
    MSA_TERMS,
    RELIANCE_COPY,
    RELIANCE_TERMS,
    render,
)
from pydantic import BaseModel, ValidationError

from gridq.contracts import (
    connection,
    curtailment,
    dispatch_envelope,
    evidence,
    identity,
    thermal_flex,
)
from gridq.contracts import contract_templates as ct
from gridq.contracts.__main__ import FROZEN, schema_path
from gridq.contracts._base import ContractModel, canonical_json
from gridq.contracts.contract_templates import (
    CLOSED_LOOP_GATES,
    CONTRACT_ID,
    CONTRACT_VERSION,
    DRAFT_HEADER,
    GIT_TAG,
    LIST_PRICE_RENDERING,
    LIST_PRICES_ON_RECORD,
    MANIFEST_PATH,
    MSA_NAMED_CLAUSES,
    SAFETY_HIERARCHY,
    SUPPORTED_VERSIONS,
    TEMPLATE_DIR,
    ContractTemplateSet,
    CounselReviewed,
    DraftNeedsLegalReview,
    DraftTemplateRefused,
    ExecutedContractTerms,
    NotYetFrozen,
    PersonOnlyKind,
    TemplateEntry,
    TemplateId,
    TemplateRefused,
    TemplateTextMismatch,
    check_counsel_review,
    check_executed_terms,
    effective_review,
    live_contract_versions,
    load_template_set,
    manifest_text,
    reference_findings,
    template_set_findings,
    template_text,
    text_findings,
    text_sha256,
)
from gridq.contracts.evidence import (
    EvidenceObject,
    EvidenceRefused,
    SelfIssuedEvidenceRefused,
    SyntheticEvidenceRefused,
    evidence_ref,
)

MSA = TemplateId.SOFTWARE_DISPATCH_MSA
WRAPPER = TemplateId.PERFORMANCE_WRAPPER
RELIANCE = TemplateId.LENDER_RELIANCE_LETTER

#: Golden content hashes of template set 0.1.0, as released with IC-15 1.0.0 at its tag. A change
#: means the manifest, a fixture or the canonical form changed. Never re-pinned: the files at the
#: tag must still hash to these (`test_the_set_0_1_0_goldens_hold_at_the_ic15_tag`).
GOLDEN = {
    "manifest": "sha256:0b468e8682eed64265882831d0aec8e88240e387bedf8aeaf82c006794c42c51",
    MSA_COPY.name: "sha256:5a9d80f6a77d7a54958bb84a55169d8f7f02ea6b1378e449b969e6e0282b86d6",
    RELIANCE_COPY.name: "sha256:7e841f4c1c80cbbcf48d56abe56b9f81f88ef03948a4354d57d9a090da68fdba",
    MSA_TERMS.name: "sha256:cb061d3509f0faae34b96f447ffe3166068a56f60148820bc6f47948a4caafa0",
    RELIANCE_TERMS.name: "sha256:31b079e294f8a41c7687fb26f70a7ac4b69539c2259de836247b9cdea3277078",
}

#: Template set 0.2.0: IC-3 bound at 1.0.0 once WP-SIZE-07 froze it, and the five texts citing it
#: at template version 0.2.0. A new table, as for IC-3's fixtures; `GOLDEN` is not edited. The two
#: signed copies did not change, so they keep their `GOLDEN` hashes.
GOLDEN_SET_0_2_0 = {
    "manifest": "sha256:8ea7274740dfcec0c0bc60f7dc8c1a773750aa382fc29197a6678eee912b8d00",
    MSA_TERMS.name: "sha256:a3dce58a7af7e7f1dc0e5f75be512c96f21fbbcfcf6ec8643bc1d3336e1f4fee",
    RELIANCE_TERMS.name: "sha256:21aeea1f6ea0258ef6d9f3983d55edf5779c253534bb596bdd0375c6fe5b4fe4",
}

#: Plan §2 item 1: template -> (IC-4 type, admitted issuer roles) for each way it is executed.
REGISTERED_AS = {
    TemplateId.ASSESSMENT_SOW: [("assessment_statement_of_work", ("customer", "host"))],
    MSA: [("master_services_agreement", ("customer", "host"))],
    TemplateId.DATA_SHARING_AGREEMENT: [
        ("data_sharing_agreement", ("dno", "idno", "neso", "elexon"))
    ],
    TemplateId.FLEXIBILITY_SERVICES_AGREEMENT: [
        ("aggregator_agreement", ("aggregator",)),
        ("dno_flexibility_contract", ("dno", "idno")),
    ],
    TemplateId.SITE_ACCESS_INSTALLATION: [("site_access_installation_agreement", ("host",))],
    RELIANCE: [("lender_reliance_letter", ("lender",))],
    WRAPPER: [("performance_wrapper_agreement", ("insurer",))],
}


def _set() -> ContractTemplateSet:
    return load_template_set()


def _manifest() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return data


def _entry_dict(data: dict[str, Any], tid: TemplateId) -> dict[str, Any]:
    entry: dict[str, Any] = next(t for t in data["templates"] if t["template_id"] == tid.value)
    return entry


def _parse_set(data: dict[str, Any]) -> ContractTemplateSet:
    return ContractTemplateSet.model_validate_json(json.dumps(data))


def _text(tid: TemplateId) -> str:
    return (TEMPLATE_DIR / f"{tid.value}.md").read_text(encoding="utf-8")


def _findings(tid: TemplateId, text: str) -> list[str]:
    s = _set()
    return text_findings(s.template(tid), text, s)


def _copies() -> list[EvidenceObject]:
    return [
        EvidenceObject.model_validate_json(p.read_text(encoding="utf-8"))
        for p in (MSA_COPY, RELIANCE_COPY)
    ]


def _terms(path: Any) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _parse_terms(data: dict[str, Any]) -> ExecutedContractTerms:
    return ExecutedContractTerms.model_validate_json(json.dumps(data))


def _check(data: dict[str, Any], objects: list[EvidenceObject] | None = None) -> EvidenceObject:
    return check_executed_terms(
        _parse_terms(data),
        _set(),
        _copies() if objects is None else objects,
        admit_draft=True,
        include_synthetic=True,
    )


def _set_value(data: dict[str, Any], field_id: str, value: dict[str, Any]) -> None:
    next(v for v in data["values"] if v["field_id"] == field_id)["value"] = value


# ------------------------------------------------------------------ 1. version, schemas, manifest


def test_ic15_is_1_0_0_and_frozen_in_two_schemas() -> None:
    assert (CONTRACT_ID, CONTRACT_VERSION, SUPPORTED_VERSIONS) == ("IC-15", "1.0.0", ("1.0.0",))
    assert GIT_TAG == "contracts/ic15-contract-template-set/v1.0.0"
    stems = {s for s, _, v in FROZEN if s.startswith("ic15_") and v == "1.0.0"}
    assert stems == {"ic15_contract_template_set", "ic15_executed_contract_terms"}
    for stem in stems:
        assert schema_path(stem, "1.0.0").exists()


def test_the_ic15_schemas_embed_no_ic4_enum() -> None:
    # A future IC-4 types-only minor must not re-version IC-15: types and roles are held as
    # strings checked against the live IC-4 enums, and IC-4 is reached only through EvidenceRef.
    for stem in ("ic15_contract_template_set", "ic15_executed_contract_terms"):
        schema = json.loads(schema_path(stem, "1.0.0").read_text(encoding="utf-8"))
        assert "EvidenceRef" in schema["$defs"]
        assert not {"EvidenceType", "IssuerRole", "SelfIssuerRole"} & set(schema["$defs"])
        assert "master_services_agreement" not in json.dumps(schema)


def test_the_committed_manifest_validates_in_its_canonical_text_form() -> None:
    s = _set()
    assert MANIFEST_PATH.read_text(encoding="utf-8") == manifest_text(s)
    assert (s.contract, s.contract_version, s.work_package) == ("IC-15", "1.0.0", "WP-GRC-09")
    assert s.set_id == "contract_template_set"


def test_the_manifest_golden_hash() -> None:
    assert _set().set_version == "0.2.0"
    assert _set().content_hash() == GOLDEN_SET_0_2_0["manifest"]


def _at_ic15_tag(path: Any) -> str:
    """The committed text of ``path`` at the IC-15 1.0.0 tag. A tagless checkout skips."""
    root = MANIFEST_PATH.resolve().parents[4]
    rel = path.resolve().relative_to(root).as_posix()
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "show", f"{GIT_TAG}:{rel}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        pytest.skip("git is not installed")
    if res.returncode != 0:
        pytest.skip(f"{GIT_TAG} is not in this checkout")
    return res.stdout


def test_the_set_0_1_0_goldens_hold_at_the_ic15_tag() -> None:
    tagged = ContractTemplateSet.model_validate_json(_at_ic15_tag(MANIFEST_PATH))
    assert tagged.set_version == "0.1.0"
    assert tagged.content_hash() == GOLDEN["manifest"]
    assert isinstance(tagged.reference("ic3_dispatch_envelope").status, NotYetFrozen)
    for path in (MSA_TERMS, RELIANCE_TERMS):
        terms = ExecutedContractTerms.model_validate_json(_at_ic15_tag(path))
        assert terms.content_hash() == GOLDEN[path.name], path.name


def test_the_seven_templates_in_work_package_order() -> None:
    assert [t.template_id for t in _set().templates] == list(TemplateId)
    assert len(TemplateId) == 7
    assert {p.name for p in TEMPLATE_DIR.glob("*.md")} == {f"{t.value}.md" for t in TemplateId}


def test_the_set_refuses_a_missing_or_reordered_template() -> None:
    data = _manifest()
    data["templates"] = data["templates"][1:]
    with pytest.raises(ValidationError, match="seven templates"):
        _parse_set(data)
    data = _manifest()
    data["templates"][0], data["templates"][1] = data["templates"][1], data["templates"][0]
    with pytest.raises(ValidationError, match="seven templates"):
        _parse_set(data)


def test_the_whole_set_has_no_findings() -> None:
    assert template_set_findings(_set()) == []


# ------------------------------------------------------------------ 2. acceptance 1


def test_each_template_is_registered_as_an_ic4_evidence_type() -> None:
    for t in _set().templates:
        got = [(e.evidence_type, e.admissible_issuers) for e in t.executed_as]
        assert got == REGISTERED_AS[t.template_id], t.template_id


def test_every_registered_type_is_a_live_third_party_ic4_type() -> None:
    types_ = {t.value for t in evidence.EvidenceType}
    roles = {r.value for r in evidence.IssuerRole}
    for t in _set().templates:
        for e in t.executed_as:
            assert e.evidence_type in types_
            assert evidence.EvidenceType(e.evidence_type) not in evidence.SELF_ISSUABLE_TYPES
            assert set(e.admissible_issuers) <= roles


def test_the_five_ic4_1_2_0_types_each_register_exactly_one_template() -> None:
    new = [t.value for t in evidence.EvidenceType][32:]
    registered = [e.evidence_type for t in _set().templates for e in t.executed_as]
    assert all(registered.count(t) == 1 for t in new)


def test_every_template_has_required_fields_each_with_an_ask() -> None:
    for t in _set().templates:
        assert len(t.required_fields) >= 9, t.template_id
        for f in t.required_fields:
            assert f.ask.strip() and f.asked_of, (t.template_id, f.field_id)
        ids = {f.field_id for f in t.required_fields}
        assert {"provider_legal_name", "effective_date", "governing_law"} <= ids


def test_a_template_cannot_be_executed_as_a_self_issuable_type() -> None:
    data = _manifest()
    _entry_dict(data, MSA)["executed_as"][0]["evidence_type"] = "decision_record"
    with pytest.raises(ValidationError, match="a type Neura may self-issue"):
        _parse_set(data)


@pytest.mark.parametrize(
    "key,value,message",
    [
        ("evidence_type", "signed_contract", "not an IC-4 evidence type"),
        ("admissible_issuers", ["neura_self_issued"], "not IC-4 third-party issuer roles"),
        ("admissible_issuers", ["neura"], "not IC-4 third-party issuer roles"),
    ],
)
def test_executed_as_names_only_live_ic4_members(key: str, value: Any, message: str) -> None:
    data = _manifest()
    _entry_dict(data, MSA)["executed_as"][0][key] = value
    with pytest.raises(ValidationError, match=message):
        _parse_set(data)


def test_the_wrapper_admits_only_an_available_risk_carrier() -> None:
    wrapper = _set().template(WRAPPER)
    assert not isinstance(wrapper.risk_carriers, ct.NotApplicable)
    admitted = {o.carrier.value: o.admission.kind for o in wrapper.risk_carriers}
    assert admitted == {
        "insurer": "available",
        "mga": "unavailable",
        "floor_provider": "unavailable",
    }
    for option in wrapper.risk_carriers[1:]:
        assert isinstance(option.admission, ct.CarrierUnavailable)
        assert (option.admission.gap, option.admission.owner) == ("G8", "WP-GRC-13")
    data = _manifest()
    _entry_dict(data, WRAPPER)["executed_as"][0]["admissible_issuers"] = ["insurer", "funder"]
    with pytest.raises(ValidationError, match="only an available risk carrier"):
        _parse_set(data)


# ------------------------------------------------------------------ 3. acceptance 2


def test_the_msa_carries_the_eight_named_clauses() -> None:
    assert MSA_NAMED_CLAUSES == (
        "safety_hierarchy",
        "it_outage_carve_out",
        "step_in",
        "liability_cap",
        "service_levels",
        "termination",
        "control_authority_staging",
        "responsibility_boundary",
    )
    msa = _set().template(MSA)
    assert set(MSA_NAMED_CLAUSES) <= {c.clause_id for c in msa.clauses}
    assert msa.form == "full_text"
    for clause in MSA_NAMED_CLAUSES:
        assert f"[clause:{clause}]" in _text(MSA)
    assert {t.form for t in _set().templates if t.template_id is not MSA} == {"heads_of_terms"}


def test_an_msa_without_a_named_clause_is_refused() -> None:
    data = _manifest()
    clauses = _entry_dict(data, MSA)["clauses"]
    _entry_dict(data, MSA)["clauses"] = [c for c in clauses if c["clause_id"] != "step_in"]
    with pytest.raises(ValidationError, match="the MSA lacks its named clauses"):
        _parse_set(data)


def test_the_msa_safety_hierarchy_is_spec_s8_in_order() -> None:
    # Spec §8 principle 1: "life safety > IT SLA / thermal limits > connection compliance (import
    # limit, ramp) > resilience reserve > contracted market delivery > merchant revenue".
    expected = (
        "life_safety",
        "it_sla_and_thermal_limits",
        "connection_compliance",
        "resilience_reserve",
        "contracted_market_delivery",
        "merchant_revenue",
    )
    assert tuple(t.value for t in SAFETY_HIERARCHY) == expected
    msa = _set().template(MSA)
    assert isinstance(msa.safety_hierarchy, ct.SafetyHierarchy)
    assert tuple(t.value for t in msa.safety_hierarchy.order) == expected


def test_a_reordered_safety_hierarchy_is_refused() -> None:
    data = _manifest()
    order = _entry_dict(data, MSA)["safety_hierarchy"]["order"]
    order[3], order[4] = order[4], order[3]  # market delivery above the resilience reserve
    with pytest.raises(ValidationError, match="must be spec §8's"):
        _parse_set(data)
    data = _manifest()
    _entry_dict(data, MSA)["safety_hierarchy"]["order"] = order[:5]  # merchant revenue dropped
    with pytest.raises(ValidationError, match="must be spec §8's"):
        _parse_set(data)


def test_the_msa_text_writes_the_tiers_in_order() -> None:
    text = _text(MSA)
    swapped = text.replace("4. Resilience reserve.", "4. Contracted market delivery.", 1).replace(
        "5. Contracted market delivery.", "5. Resilience reserve.", 1
    )
    assert swapped != text
    assert any("not written in order" in f for f in _findings(MSA, swapped))


def test_the_msa_without_a_safety_hierarchy_or_staging_is_refused() -> None:
    for key, message in (
        ("safety_hierarchy", "spec §8 safety hierarchy"),
        ("control_authority", "control-authority staging"),
    ):
        data = _manifest()
        _entry_dict(data, MSA)[key] = {"kind": "not_applicable", "reason": "test"}
        with pytest.raises(ValidationError, match=message):
            _parse_set(data)


def test_control_authority_is_staged_l0_to_l4_with_the_adr_d2_gates() -> None:
    msa = _set().template(MSA)
    assert isinstance(msa.control_authority, ct.ControlAuthorityStaging)
    stages = {s.stage.value: s for s in msa.control_authority.stages}
    assert list(stages) == [
        "l0_read_only",
        "l1_shadow",
        "l2_bess_closed_loop",
        "l3_chiller_narrow_band",
        "l4_workload_advisory",
    ]
    assert CLOSED_LOOP_GATES == ("XG-11", "WP-EDGE-08", "XG-17")
    assert [s for s, v in stages.items() if v.write_path] == [
        "l2_bess_closed_loop",
        "l3_chiller_narrow_band",
    ]
    for stage in ("l2_bess_closed_loop", "l3_chiller_narrow_band"):
        assert set(CLOSED_LOOP_GATES) <= set(stages[stage].gates)


def test_a_write_stage_without_its_three_gates_is_refused() -> None:
    data = _manifest()
    _entry_dict(data, MSA)["control_authority"]["stages"][2]["gates"] = ["XG-11", "WP-EDGE-08"]
    with pytest.raises(ValidationError, match="waits on XG-11, WP-EDGE-08, XG-17"):
        _parse_set(data)


def test_l4_never_writes() -> None:
    data = _manifest()
    _entry_dict(data, MSA)["control_authority"]["stages"][4]["write_path"] = True
    with pytest.raises(ValidationError, match="only L2 and L3 have a write path"):
        _parse_set(data)


# ------------------------------------------------------------------ 4. acceptance 3


def test_ic3_is_bound_at_its_tag_and_ic8_is_still_not_yet_frozen() -> None:
    s = _set()
    ic3, ic8 = s.reference("ic3_dispatch_envelope"), s.reference("ic8_verifier_interface")
    assert (ic3.contract, ic3.version_range, ic8.contract, ic8.version_range) == (
        "IC-3",
        "1.x",
        "IC-8",
        "1.x",
    )
    # WP-SIZE-07 froze IC-3 at 1.0.0; the reference is bound at that version and its tag.
    assert ic3.status == ct.Frozen(
        kind="frozen", minimum_version="1.0.0", git_tag="contracts/ic3-dispatch-envelope/v1.0.0"
    )
    assert ic3.status.git_tag == dispatch_envelope.GIT_TAG
    assert isinstance(ic8.status, NotYetFrozen)
    assert (ic8.status.freezing_work_package, ic8.status.gates) == (
        "WP-LEDGER-07",
        ("D-2", "XG-22"),
    )
    cited = {r for t in s.templates for r in t.references}
    assert {"ic3_dispatch_envelope", "ic8_verifier_interface"} <= cited
    assert "ic3_dispatch_envelope" in s.template(MSA).references
    assert "ic8_verifier_interface" in s.template(WRAPPER).references


def test_every_frozen_reference_matches_a_live_contract_and_its_tag() -> None:
    assert reference_findings(_set(), live_contract_versions()) == []
    modules = {
        "IC-1": connection,
        "IC-2": curtailment,
        "IC-3": dispatch_envelope,
        "IC-4": evidence,
        "IC-11": thermal_flex,
        "IC-13": identity,
    }
    for ref in _set().references:
        if isinstance(ref.status, ct.Frozen):
            prefix = modules[ref.contract].GIT_TAG.rsplit("/v", 1)[0]
            assert ref.status.git_tag == f"{prefix}/v{ref.status.minimum_version}", ref.ref_id
    assert _set().reference("ic4_evidence_object").status.kind == "frozen"
    assert _set().reference("ic3_dispatch_envelope").status.kind == "frozen"
    assert live_contract_versions()["IC-3"] == dispatch_envelope.CONTRACT_VERSION


def test_a_not_yet_frozen_reference_to_a_contract_that_now_exists_is_reported() -> None:
    live = {**live_contract_versions(), "IC-8": "1.0.0"}
    assert reference_findings(_set(), live) == [
        "ic8_verifier_interface: IC-8 now exists at 1.0.0; bind the reference"
    ]


@pytest.mark.parametrize(
    "contract,version,expected",
    [
        ("IC-4", "1.1.0", "older than the minimum 1.2.0"),
        ("IC-4", "2.0.0", "outside the 1.x range"),
        ("IC-4", None, "is not a contract in this package"),
    ],
)
def test_a_frozen_reference_to_an_absent_old_or_new_major_contract_is_reported(
    contract: str, version: str | None, expected: str
) -> None:
    live = dict(live_contract_versions())
    if version is None:
        del live[contract]
    else:
        live[contract] = version
    findings = reference_findings(_set(), live)
    assert len(findings) == 1 and expected in findings[0]


def test_no_text_names_a_contract_in_prose() -> None:
    for tid in TemplateId:
        assert not re.search(r"\bIC-[0-9]+\b", _text(tid)), tid


def test_a_contract_id_written_in_prose_is_refused() -> None:
    text = _text(MSA).replace("[[ref:ic1_connection_agreement]]", "IC-1 version 1.1.0", 1)
    found = _findings(MSA, text)
    assert any("a contract named in prose" in f for f in found)


def test_the_prose_guard_refuses_an_artefact_named_without_its_reference() -> None:
    for term, contract in ct.PROSE_GUARD_TERMS.items():
        text = _text(MSA) + f"\nThe Provider shares the {term} as agreed.\n"
        assert f"'{term}' without a reference token to {contract}" in " ".join(
            _findings(MSA, text)
        ), term
    assert set(ct.PROSE_GUARD_TERMS.values()) == {"IC-3", "IC-8"}


def test_a_pending_reference_must_say_unavailable_until() -> None:
    text = _text(MSA).replace(
        "[[ref:ic8_verifier_interface]] (binding unavailable until WP-LEDGER-07)",
        "[[ref:ic8_verifier_interface]]",
        1,
    )
    assert any("without 'unavailable until WP-LEDGER-07'" in f for f in _findings(MSA, text))


def test_every_line_citing_ic8_says_unavailable_until_and_no_line_waits_on_ic3() -> None:
    cited_ic3 = 0
    for tid in TemplateId:
        text = _text(tid)
        assert "WP-SIZE-07" not in text, tid
        for line in text.splitlines():
            if "[[ref:ic3_dispatch_envelope]]" in line:
                cited_ic3 += 1
                assert "unavailable until" not in line or "WP-LEDGER-07" in line, (tid, line)
            if "[[ref:ic8_verifier_interface]]" in line:
                assert "unavailable until WP-LEDGER-07" in line, (tid, line)
    assert cited_ic3 == 6


def test_each_text_states_its_manifest_template_version() -> None:
    for t in _set().templates:
        stated = f"Template `{t.template_id.value}`, version {t.template_version},"
        assert stated in _text(t.template_id), t.template_id


def _with_ic3_pending() -> ContractTemplateSet:
    """The committed set as it stood before WP-SIZE-07 froze IC-3: its reference pending."""
    data = _manifest()
    ref = next(r for r in data["references"] if r["ref_id"] == "ic3_dispatch_envelope")
    ref["status"] = {
        "kind": "not_yet_frozen",
        "freezing_work_package": "WP-SIZE-07",
        "gates": [],
        "reason": "IC-3 is frozen by WP-SIZE-07; until then no envelope instance can be bound",
    }
    return _parse_set(data)


def test_a_known_instance_of_a_contract_not_yet_frozen_is_refused() -> None:
    # No field binds IC-8, the one contract still pending, so the guard is shown on the set with
    # IC-3 pending again: the committed reliance terms, which bind an IC-3 envelope, are refused.
    data = _terms(RELIANCE_TERMS)
    with pytest.raises(TemplateRefused, match="unavailable until WP-SIZE-07"):
        check_executed_terms(
            _parse_terms(data),
            _with_ic3_pending(),
            _copies(),
            admit_draft=True,
            include_synthetic=True,
        )


def test_a_known_ic3_instance_is_accepted_now_that_ic3_is_frozen() -> None:
    assert _check(_terms(RELIANCE_TERMS)).evidence_type.value == "lender_reliance_letter"
    data = _terms(RELIANCE_TERMS)
    value = next(v for v in data["values"] if v["field_id"] == "relied_dispatch_envelope")
    value["value"]["contract"] = "IC-1"
    with pytest.raises(TemplateRefused, match="binds IC-3, not IC-1"):
        _check(data)


def test_a_contract_instance_of_the_wrong_contract_is_refused() -> None:
    data = _terms(RELIANCE_TERMS)
    value = next(v for v in data["values"] if v["field_id"] == "relied_connection_agreement")
    value["value"]["contract"] = "IC-2"
    with pytest.raises(TemplateRefused, match="binds IC-1, not IC-2"):
        _check(data)


def test_the_reliance_letter_binds_ic1_ic2_and_ic3_by_hash_for_one_site() -> None:
    terms = _parse_terms(_terms(RELIANCE_TERMS))
    values = {v.field_id: v.value for v in terms.values}
    ic1 = connection.ConnectionAgreement.model_validate_json(IC1_FIXTURE.read_text("utf-8"))
    ic2 = curtailment.CurtailmentScenarioSet.model_validate_json(IC2_FIXTURE.read_text("utf-8"))
    assert values["relied_connection_agreement"] == ct.ContractInstanceValue(
        kind="contract_instance",
        contract="IC-1",
        contract_version="1.1.0",
        content_hash=ic1.content_hash(),
    )
    assert values["relied_curtailment_scenarios"] == ct.ContractInstanceValue(
        kind="contract_instance",
        contract="IC-2",
        contract_version="1.1.0",
        content_hash=ic2.content_hash(),
    )
    ic3 = dispatch_envelope.DispatchEnvelope.model_validate_json(IC3_FIXTURE.read_text("utf-8"))
    assert values["relied_dispatch_envelope"] == ct.ContractInstanceValue(
        kind="contract_instance",
        contract="IC-3",
        contract_version="1.0.0",
        content_hash=ic3.content_hash(),
    )
    # One consistent set of relied materials: the envelope binds the same agreement and scenarios.
    agreement, scenarios = ic3.source.connection_agreement, ic3.source.scenario_set
    assert getattr(agreement, "agreement_hash", None) == ic1.content_hash()
    assert getattr(scenarios, "scenario_set_hash", None) == ic2.content_hash()
    assert ic3.site_id == ic1.site_id


# ------------------------------------------------------------------ 5. legal review


def test_all_seven_templates_are_drafts_that_need_legal_review() -> None:
    for t in _set().templates:
        assert isinstance(t.legal_review, DraftNeedsLegalReview), t.template_id
        assert t.template_version.startswith("0."), t.template_id


def test_every_draft_text_opens_with_the_draft_header_and_says_it_is_unreviewed() -> None:
    assert DRAFT_HEADER == "DRAFT — NEEDS LEGAL REVIEW. NOT LEGAL ADVICE."
    for tid in TemplateId:
        text = _text(tid)
        assert text.splitlines()[0] == f"> **{DRAFT_HEADER}**", tid
        assert "Counsel has not reviewed it" in text, tid


def test_a_draft_without_its_header_is_reported() -> None:
    text = _text(MSA).split("\n", 1)[1]
    assert any("DRAFT header" in f for f in _findings(MSA, text))


def _reviewed(entry: TemplateEntry, reviewed_hash: str) -> dict[str, Any]:
    opinion = EvidenceObject.model_validate_json(
        (FIXTURES_V110 / "ic4_legal_opinion.synthetic.json").read_text(encoding="utf-8")
    )
    data = json.loads(canonical_json(entry))
    data["legal_review"] = {
        "kind": "counsel_reviewed",
        "reviewed_text_sha256": reviewed_hash,
        "opinion": evidence_ref(opinion).model_dump(mode="json"),
    }
    return data


def test_counsel_review_binds_the_reviewed_text() -> None:
    entry = _set().template(MSA)
    accepted = TemplateEntry.model_validate_json(json.dumps(_reviewed(entry, entry.text_sha256)))
    assert isinstance(accepted.legal_review, CounselReviewed)
    other = text_sha256(_text(MSA) + "\nAn edit after review.\n")
    with pytest.raises(ValidationError, match="counsel reviewed another text"):
        TemplateEntry.model_validate_json(json.dumps(_reviewed(entry, other)))


def test_an_edited_text_is_a_draft_again() -> None:
    entry = _set().template(MSA)
    reviewed = TemplateEntry.model_validate_json(json.dumps(_reviewed(entry, entry.text_sha256)))
    assert isinstance(effective_review(reviewed, _text(MSA)), CounselReviewed)
    edited = _text(MSA).replace("Step-in lasts no longer", "Step-in lasts no more", 1)
    assert isinstance(effective_review(reviewed, edited), DraftNeedsLegalReview)


def test_check_counsel_review_needs_a_third_party_legal_opinion() -> None:
    entry = _set().template(MSA)
    with pytest.raises(DraftTemplateRefused):
        check_counsel_review(entry, [])
    reviewed = TemplateEntry.model_validate_json(json.dumps(_reviewed(entry, entry.text_sha256)))
    opinion = EvidenceObject.model_validate_json(
        (FIXTURES_V110 / "ic4_legal_opinion.synthetic.json").read_text(encoding="utf-8")
    )
    assert check_counsel_review(reviewed, [opinion], include_synthetic=True) is opinion
    with pytest.raises(SyntheticEvidenceRefused):
        check_counsel_review(reviewed, [opinion])
    decision = EvidenceObject.model_validate_json(
        (FIXTURES_V110 / "ic4_self_issued_decision_record.synthetic.json").read_text("utf-8")
    )
    data = json.loads(canonical_json(reviewed))
    data["legal_review"]["opinion"] = evidence_ref(decision).model_dump(mode="json")
    self_opinion = TemplateEntry.model_validate_json(json.dumps(data))
    with pytest.raises(EvidenceRefused, match="expected evidence_type legal_opinion"):
        check_counsel_review(self_opinion, [decision], include_synthetic=True)


def test_each_draft_has_a_person_only_legal_review_step() -> None:
    s = _set()
    reviews = [st for st in s.person_only_steps if st.kind is PersonOnlyKind.LEGAL_REVIEW]
    assert [st.templates[0] for st in reviews][:2] == [MSA, WRAPPER]  # MSA and wrapper first
    assert {st.templates[0] for st in reviews} == set(TemplateId)
    assert {st.kind for st in s.person_only_steps} == set(PersonOnlyKind)


def test_a_draft_without_a_person_only_legal_review_step_is_refused() -> None:
    data = _manifest()
    data["person_only_steps"] = [
        st
        for st in data["person_only_steps"]
        if st["step_id"] != "legal_review_performance_wrapper"
    ]
    with pytest.raises(ValidationError, match="performance_wrapper is a draft with no person-only"):
        _parse_set(data)


def test_the_person_only_steps_cover_the_brief() -> None:
    steps = {st.step_id: st for st in _set().person_only_steps}
    assert steps["risk_carrier_and_perimeter"].gates == ("XG-20",)
    assert steps["critical_facilities_signoff"].gates == ("XG-11",)
    assert steps["load_control_licence"].gates == ("XG-17",)
    assert "PERG 6" in steps["risk_carrier_and_perimeter"].action
    assert "not agreed with anyone" in steps["agree_commercial_terms"].action
    assert "NEW-IC-15" in steps["register_contract"].action


# ------------------------------------------------------------------ 6. texts


@pytest.mark.parametrize("tid", list(TemplateId), ids=lambda t: t.value)
def test_each_text_is_the_pinned_text_and_conforms(tid: TemplateId) -> None:
    s = _set()
    entry = s.template(tid)
    text = template_text(entry)
    assert text_sha256(text) == entry.text_sha256
    assert text_findings(entry, text, s) == []


def test_an_edited_text_no_longer_loads(tmp_path: Any) -> None:
    entry = _set().template(MSA)
    (tmp_path / entry.text_file).write_text(_text(MSA) + "\n", encoding="utf-8")
    with pytest.raises(TemplateTextMismatch, match="the manifest pins"):
        template_text(entry, tmp_path)


def test_clause_anchors_must_match_the_manifest() -> None:
    text = _text(MSA).replace(" [clause:step_in]", "", 1)
    assert any("clause anchors" in f for f in _findings(MSA, text))
    moved = _text(MSA).replace("[clause:step_in]", "[clause:liability_cap_tmp]", 1)
    assert any("clause anchors" in f for f in _findings(MSA, moved))


def test_field_tokens_must_be_in_bijection_with_required_fields() -> None:
    extra = _text(MSA) + "\nThe guarantor is {{field:guarantor_name}}.\n"
    assert any(
        "field tokens without a field ['guarantor_name']" in f for f in _findings(MSA, extra)
    )
    dropped = _text(MSA).replace("{{field:liability_cap_basis}}", "a fixed sum", 1)
    assert any(
        "fields without a token ['liability_cap_basis']" in f for f in _findings(MSA, dropped)
    )


def test_reference_tokens_must_be_the_entrys_references() -> None:
    text = _text(TemplateId.SITE_ACCESS_INSTALLATION) + "\nSee [[ref:ic13_identity_claims]].\n"
    found = _findings(TemplateId.SITE_ACCESS_INSTALLATION, text)
    assert any("reference tokens" in f for f in found)


def test_unavailable_terms_render_as_unavailable_until() -> None:
    wrapper = _set().template(WRAPPER)
    assert [(u.term_id, u.until) for u in wrapper.unavailable_terms] == [
        ("insurer_terms", ("WP-GRC-13",)),
        ("payout_terms", ("WP-LEDGER-07", "WP-GRC-13")),
    ]
    text = _text(WRAPPER)
    assert "unavailable until WP-GRC-13" in text
    assert "unavailable until WP-LEDGER-07 / WP-GRC-13" in text
    gone = text.replace("unavailable until WP-LEDGER-07 / WP-GRC-13", "to follow", 1)
    assert any("payout_terms is not shown" in f for f in _findings(WRAPPER, gone))


def test_every_variant_schedule_covers_the_three_adr_d1_variants() -> None:
    for t in _set().templates:
        assert [v.variant.value for v in t.variants] == [
            "direct_host_funded",
            "ipp_third_party_owner",
            "power_provision_lessor",
        ]
        assert "variant_schedule" in {c.clause_id for c in t.clauses}
    text = _text(MSA).replace("Power-provision lessor", "Lessor", 1)
    assert any("power_provision_lessor" in f for f in _findings(MSA, text))


def test_the_dsa_cites_the_wp_grc_05_heads_of_terms_rather_than_copying_them() -> None:
    text = _text(TemplateId.DATA_SHARING_AGREEMENT)
    assert "atlas/roadmap/governance/data_protection/data_sharing_agreements.md" in text
    assert "ENA" not in text and "Schedule A" not in text


#: Organisations named in the research and ADRs. No template text names a party.
_NAMES = (
    "Neura",
    "UKPN",
    "UK Power Networks",
    "NGED",
    "SSEN",
    "SPEN",
    "ENWL",
    "Northern Powergrid",
    "Flexitricity",
    "Piclo",
    "Emerald",
    "National Grid",
    "PSAL",
)


def test_no_text_names_a_party() -> None:
    for tid in TemplateId:
        text = _text(tid)
        assert not [n for n in _NAMES if n in text], tid


# ------------------------------------------------------------------ 7. prices and terms


def test_the_list_prices_on_record_are_adr_d1s_four() -> None:
    # ADR-D1: assessments (£20k), integration (£5k), Operate at £10k/MW/yr, and 2.5% of
    # underwritten value through an insurer, MGA or floor partner (XG-20).
    assert {k.value: (a, u.value, t.value) for k, (a, u, t) in LIST_PRICES_ON_RECORD.items()} == {
        "assessment_fee": (2_000_000, "gbp_pence", "assessment_sow"),
        "integration_fee": (500_000, "gbp_pence", "software_dispatch_msa"),
        "operate_fee": (1_000_000, "gbp_pence_per_megawatt_year", "software_dispatch_msa"),
        "wrapper_fee": (250, "basis_points_of_underwritten_value", "performance_wrapper"),
    }
    assert LIST_PRICE_RENDERING == "list price on record, not agreed with any customer"


def test_a_list_price_that_is_not_on_record_is_refused() -> None:
    data = _manifest()
    term = next(
        t for t in _entry_dict(data, MSA)["commercial_terms"] if t["term_id"] == "operate_fee"
    )
    term["price"]["amount"] = 1_200_000
    with pytest.raises(ValidationError, match="operate_fee on record is 1000000"):
        _parse_set(data)


def test_a_list_price_cited_by_another_template_is_refused() -> None:
    data = _manifest()
    wrapper_fee = copy.deepcopy(
        next(
            t
            for t in _entry_dict(data, WRAPPER)["commercial_terms"]
            if t["term_id"] == "wrapper_fee"
        )
    )
    wrapper_fee["executed_field"] = "liability_cap_tmp"
    msa = _entry_dict(data, MSA)
    msa["required_fields"].append(
        {
            "field_id": "liability_cap_tmp",
            "kind": "quantity",
            "unit": "basis_points_of_underwritten_value",
            "binds": {"kind": "not_applicable", "reason": "test"},
            "ask": "test",
            "asked_of": ["customer"],
        }
    )
    msa["commercial_terms"].append(wrapper_fee)
    with pytest.raises(ValidationError, match="wrapper_fee belongs to performance_wrapper only"):
        _parse_set(data)


def test_the_2_5_percent_is_only_in_the_wrapper_and_only_through_an_insurer() -> None:
    for t in _set().templates:
        listed = {
            term.price.price_id.value
            for term in t.commercial_terms
            if isinstance(term.price, ct.ListPriceOnRecord)
        }
        assert ("wrapper_fee" in listed) == (t.template_id is WRAPPER)
        assert ("2.5%" in _text(t.template_id)) == (t.template_id is WRAPPER)
    assert "only through the insurer partner (XG-20)" in _text(WRAPPER)


def test_no_text_states_a_figure_that_is_not_on_record() -> None:
    money = re.compile(r"£[0-9][0-9,.]*")
    percent = re.compile(r"[0-9][0-9.]*%")
    found = {m for tid in TemplateId for m in money.findall(_text(tid))}
    assert found == {"£20,000", "£5,000", "£10,000"}
    assert {m for tid in TemplateId for m in percent.findall(_text(tid))} == {"2.5%"}
    for tid in TemplateId:
        for line in _text(tid).splitlines():
            if money.search(line) or percent.search(line):
                assert LIST_PRICE_RENDERING in line, (tid, line)


def test_every_commercial_term_is_executed_as_a_quantity_field_with_an_ask() -> None:
    for t in _set().templates:
        fields = {f.field_id: f for f in t.required_fields}
        for term in t.commercial_terms:
            assert fields[term.executed_field].kind is ct.FieldKind.QUANTITY
            assert fields[term.executed_field].ask
    msa = {term.term_id for term in _set().template(MSA).commercial_terms}
    assert {
        "liability_cap",
        "it_outage_sublimit",
        "platform_availability",
        "incident_response",
        "step_in_notice",
        "step_in_period",
        "minimum_term",
        "termination_notice",
    } <= msa
    to_agree = [
        term.term_id
        for term in _set().template(MSA).commercial_terms
        if isinstance(term.price, ct.ToBeAgreed)
    ]
    assert "integration_fee" not in to_agree and "liability_cap" in to_agree


def test_a_commercial_term_not_executed_as_a_quantity_is_refused() -> None:
    data = _manifest()
    term = next(
        t for t in _entry_dict(data, MSA)["commercial_terms"] if t["term_id"] == "liability_cap"
    )
    term["executed_field"] = "liability_cap_basis"  # a text field
    with pytest.raises(ValidationError, match="executed as a required quantity field"):
        _parse_set(data)


# ------------------------------------------------------------------ 8. executed terms


def test_the_committed_fixtures_equal_a_fresh_build() -> None:
    for path, build in BUILDERS.items():
        assert path.read_text(encoding="utf-8") == render(build()), path.name


def test_the_fixture_golden_hashes() -> None:
    copies = dict(zip((MSA_COPY.name, RELIANCE_COPY.name), _copies(), strict=True))
    for name, obj in copies.items():
        assert obj.content_hash() == GOLDEN[name]
        assert (obj.contract_version, obj.record_class) == ("1.2.0", "synthetic_fixture")
    for path in (MSA_TERMS, RELIANCE_TERMS):
        assert _parse_terms(_terms(path)).content_hash() == GOLDEN_SET_0_2_0[path.name]


def test_the_executed_msa_states_every_field_and_invents_no_term() -> None:
    terms = _parse_terms(_terms(MSA_TERMS))
    entry = _set().template(MSA)
    assert [v.field_id for v in terms.values] == [f.field_id for f in entry.required_fields]
    executed = {t.executed_field for t in entry.commercial_terms}
    for v in terms.values:
        if v.field_id in executed:
            assert isinstance(v.value, ct.Unknown | ct.NotApplicable), v.field_id
    assert (
        _check(_terms(MSA_TERMS)).evidence_type is evidence.EvidenceType.MASTER_SERVICES_AGREEMENT
    )


def test_executed_terms_from_a_draft_are_refused_by_default() -> None:
    terms = _parse_terms(_terms(MSA_TERMS))
    with pytest.raises(DraftTemplateRefused, match="draft that needs legal review"):
        check_executed_terms(terms, _set(), _copies(), include_synthetic=True)


def test_executed_terms_need_every_required_field() -> None:
    data = _terms(MSA_TERMS)
    data["values"] = [v for v in data["values"] if v["field_id"] != "liability_cap"]
    with pytest.raises(TemplateRefused, match=r"required fields missing \['liability_cap'\]"):
        _check(data)


def test_executed_terms_refuse_a_field_the_template_does_not_have() -> None:
    data = _terms(MSA_TERMS)
    data["values"].append({"field_id": "guarantor", "value": {"kind": "text", "value": "x"}})
    with pytest.raises(TemplateRefused, match="fields the template does not have"):
        _check(data)


@pytest.mark.parametrize(
    "field_id,value,message",
    [
        (
            "liability_cap",
            {"kind": "text", "value": "one million"},
            "is a quantity field, not text",
        ),
        ("liability_cap", {"kind": "quantity", "unit": "days", "value": 5}, "is in gbp_pence"),
        ("effective_date", {"kind": "text", "value": "2026-11-24"}, "is a date field, not text"),
    ],
)
def test_a_value_of_the_wrong_kind_is_refused(
    field_id: str, value: dict[str, Any], message: str
) -> None:
    data = _terms(MSA_TERMS)
    _set_value(data, field_id, value)
    with pytest.raises(TemplateRefused, match=message):
        _check(data)


def test_terms_citing_another_text_are_refused() -> None:
    data = _terms(MSA_TERMS)
    data["text_sha256"] = text_sha256("another text")
    with pytest.raises(TemplateRefused, match="a text the manifest does not pin"):
        _check(data)


def test_a_signed_copy_of_another_type_is_refused() -> None:
    data = _terms(MSA_TERMS)
    data["executed_copy"] = _terms(RELIANCE_TERMS)["executed_copy"]
    with pytest.raises(EvidenceRefused, match="is executed as"):
        _check(data)


def test_a_signed_copy_from_an_issuer_the_template_does_not_admit_is_refused() -> None:
    copy_ = json.loads(MSA_COPY.read_text(encoding="utf-8"))
    copy_["issuer"]["role"] = "lender"
    lender_copy = EvidenceObject.model_validate_json(json.dumps(copy_))
    data = _terms(MSA_TERMS)
    data["executed_copy"] = evidence_ref(lender_copy).model_dump(mode="json")
    with pytest.raises(EvidenceRefused, match="issued by lender is not admitted"):
        _check(data, [lender_copy])


def test_a_synthetic_signed_copy_is_refused_unless_included() -> None:
    terms = _parse_terms(_terms(MSA_TERMS))
    with pytest.raises(SyntheticEvidenceRefused):
        check_executed_terms(terms, _set(), _copies(), admit_draft=True)


def test_a_self_issued_signed_copy_cannot_exist() -> None:
    # R3 refuses it at the IC-4 boundary, before IC-15 sees it.
    copy_ = json.loads(MSA_COPY.read_text(encoding="utf-8"))
    copy_["issuer"]["role"] = "neura_self_issued"
    with pytest.raises(ValidationError, match="Neura may self-issue only"):
        EvidenceObject.model_validate_json(json.dumps(copy_))
    assert SelfIssuedEvidenceRefused  # the consumer-side refusal resolve_ref applies


def test_the_flexibility_template_resolves_either_registration() -> None:
    entry = _set().template(TemplateId.FLEXIBILITY_SERVICES_AGREEMENT)
    data = json.loads(MSA_COPY.read_text(encoding="utf-8"))
    for evidence_type, role in (
        ("aggregator_agreement", "aggregator"),
        ("dno_flexibility_contract", "dno"),
    ):
        data["evidence_type"], data["issuer"]["role"] = evidence_type, role
        obj = EvidenceObject.model_validate_json(json.dumps(data))
        terms = {
            "contract": "IC-15",
            "contract_version": "1.0.0",
            "template_id": entry.template_id.value,
            "template_version": entry.template_version,
            "text_sha256": entry.text_sha256,
            "executed_copy": evidence_ref(obj).model_dump(mode="json"),
            "variant": "direct_host_funded",
            "values": [
                {"field_id": f.field_id, "value": {"kind": "unknown", "reason": f.ask}}
                for f in entry.required_fields
            ],
        }
        assert _check(terms, [obj]) is obj
    data["evidence_type"], data["issuer"]["role"] = "dno_flexibility_contract", "aggregator"
    obj = EvidenceObject.model_validate_json(json.dumps(data))
    terms["executed_copy"] = evidence_ref(obj).model_dump(mode="json")
    with pytest.raises(EvidenceRefused, match="issued by aggregator is not admitted"):
        _check(terms, [obj])


def test_a_field_stated_twice_is_refused() -> None:
    data = _terms(MSA_TERMS)
    data["values"].append(copy.deepcopy(data["values"][0]))
    with pytest.raises(ValidationError, match="a field is stated twice"):
        _parse_terms(data)


# ------------------------------------------------------------------ 9. no None, guard words


def _allows_none(annotation: Any) -> bool:
    if annotation is None or annotation is type(None):
        return True
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _allows_none(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        return any(_allows_none(a) for a in typing.get_args(annotation))
    if origin is typing.Literal:
        return None in typing.get_args(annotation)
    if origin is tuple:
        return any(_allows_none(a) for a in typing.get_args(annotation) if a is not Ellipsis)
    return False


def _ic15_models() -> list[type[BaseModel]]:
    return [
        obj
        for obj in vars(ct).values()
        if isinstance(obj, type)
        and issubclass(obj, ContractModel)
        and obj.__module__ == ct.__name__
    ]


@pytest.mark.parametrize("model", _ic15_models(), ids=lambda m: m.__name__)
def test_no_field_of_any_ic15_model_admits_none(model: type[BaseModel]) -> None:
    for name, field in model.model_fields.items():
        assert not _allows_none(field.annotation), f"{model.__name__}.{name} admits None"


def test_the_ic15_schemas_contain_no_null_type() -> None:
    for stem in ("ic15_contract_template_set", "ic15_executed_contract_terms"):
        assert '"null"' not in schema_path(stem, "1.0.0").read_text(encoding="utf-8")


def test_no_ic15_enum_value_names_an_assumption_a_placeholder_or_neura() -> None:
    words = ("assum", "derived", "placeholder", "pending", "study", "self", "neura", "estimate")
    enums = [
        obj
        for obj in vars(ct).values()
        if isinstance(obj, type) and issubclass(obj, ct.StrEnum) and obj.__module__ == ct.__name__
    ]
    assert len(enums) == 10
    values = [m.value for e in enums for m in e]
    assert not [(v, w) for v in values for w in words if w in v]


def test_a_manifest_value_never_holds_null() -> None:
    def walk(node: Any) -> None:
        assert node is not None
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(_manifest())
    for path in (MSA_TERMS, RELIANCE_TERMS):
        walk(_terms(path))
