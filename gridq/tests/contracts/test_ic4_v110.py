"""IC-4 EvidenceObject v1.1.0 (roadmap WP-BO-06; plan docs/shared_work/wp_bo06_plan.md §4).

What 1.1.0 adds, and where each piece is tested here:
1. Seventeen evidence types and six third-party issuer roles; the two 1.0.0 member sets written
   out literally (the down-level rule).
2. Neura as a restricted issuer (ADR-D10 option b, widened by the owner 2026-11-23 to decision
   records, plan §11): `SelfIssuerRole`, `SELF_ISSUABLE_TYPES`, R3 to R6.
3. The consumer refusals: `require_third_party`, `resolve_ref(expected_type=...)`,
   `assess(admit_self_issued=...)`.
4. `evidence_ref` states the object's own version; `EvidenceRef` accepts the 1.x range.

The 1.0.0 conformance tests (``test_ic4_evidence_object.py``) still run against the 1.1.0 code.
"""

from __future__ import annotations

import json
import warnings
from datetime import date
from typing import Any

import pytest
from _contracts_support import (
    GOLDEN_HASHES_V110,
    IC4_FIXTURES,
    IC4_V110_FIXTURES,
    dumps,
    load,
    load_v110,
)
from pydantic import ValidationError

from gridq import contracts
from gridq.assumptions.models import Assumption, AssumptionStatus
from gridq.contracts.__main__ import ARCHIVED, SCHEMA_DIR
from gridq.contracts._base import EMPTY_SHA256, canonical_json
from gridq.contracts.evidence import (
    ALL_SITES,
    CONTRACT_VERSION,
    GIT_TAG,
    SELF_ISSUABLE_TYPES,
    SUPPORTED_VERSIONS,
    V1_0_0_EVIDENCE_TYPES,
    V1_0_0_ISSUER_ROLES,
    EvidenceNotFound,
    EvidenceObject,
    EvidenceRef,
    EvidenceRefused,
    EvidenceStatus,
    EvidenceType,
    Issuer,
    IssuerRole,
    NoExpiry,
    SelfIssuedEvidenceRefused,
    SelfIssuerRole,
    SiteScope,
    SyntheticEvidenceRefused,
    Unknown,
    Unverified,
    Verified,
    VerifierRole,
    assess,
    evidence_ref,
    require_third_party,
    resolve_ref,
    serialise_evidence,
)
from gridq.schemas.derived import DerivedAssumption
from gridq.schemas.enums import Confidence, ConfidenceBand

DPA = "ic4_data_processing_agreement.synthetic.json"
BASELINE = "ic4_self_issued_baseline_method.synthetic.json"
TRIAL = "ic4_self_issued_trial_record.synthetic.json"
DECISION = "ic4_self_issued_decision_record.synthetic.json"
OPINION = "ic4_legal_opinion.synthetic.json"
SELF_ISSUED = (BASELINE, TRIAL, DECISION)
#: 1.0.0 fixtures (``fixtures/``), parsed by the 1.1.0 code.
VALID_V100 = "ic4_valid_connection_offer.synthetic.json"

#: The seventeen types 1.1.0 appends, in order (plan §4.2 and §11.2).
NEW_TYPES = (
    "data_processing_agreement",
    "data_sharing_agreement",
    "non_disclosure_agreement",
    "legal_opinion",
    "conformity_certificate",
    "technical_report",
    "data_licence",
    "method_agreement",
    "service_level_agreement",
    "commissioning_record",
    "control_authority_signoff",
    "fmar_registration",
    "metered_data_record",
    "operational_data_record",
    "baseline_method",
    "trial_record",
    "decision_record",
)
#: The six third-party roles 1.1.0 appends, in order (plan §4.3).
NEW_ROLES = (
    "legal_adviser",
    "certification_body",
    "data_provider",
    "funder",
    "research_partner",
    "independent_verifier",
)
NOT_SELF_ISSUABLE = sorted(t.value for t in EvidenceType if t not in SELF_ISSUABLE_TYPES)
AS_OF = date(2026, 11, 23)
CHILLED_WATER = SiteScope(kind="site", site_id="SYNTHETIC-DC-CHILLED-WATER")
HYBRID_SITE = SiteScope(kind="site", site_id="SYNTHETIC-SITE-HYBRID")


def _obj(name: str) -> EvidenceObject:
    return EvidenceObject.model_validate_json(dumps(load_v110(name)))


def _obj_v100(name: str) -> EvidenceObject:
    return EvidenceObject.model_validate_json(dumps(load(name)))


def _parse(data: dict[str, Any]) -> EvidenceObject:
    return EvidenceObject.model_validate_json(dumps(data))


def _third_party_baseline() -> dict[str, Any]:
    """The baseline-method fixture issued by the host instead: a third party may issue it too."""
    data = load_v110(BASELINE)
    data["issuer"] = {
        "name": "SYNTHETIC-HOST (fixture; not a real company)",
        "role": "host",
        "reference": "SYNTH-HOST-BASELINE-0001",
    }
    return data


def _ref(version: str, evidence_hash: str) -> EvidenceRef:
    return EvidenceRef(
        kind="evidence_ref", contract="IC-4", contract_version=version, evidence_hash=evidence_hash
    )


def _unknown(reason: str) -> dict[str, str]:
    return {"kind": "unknown", "reason": reason}


# ------------------------------------------------------------------ 1. version and fixtures


def test_contract_version_is_current_and_reads_1_1_0_and_1_0_0() -> None:
    # IC-4 1.2.0 (WP-GRC-09) writes 1.2.0 and still reads every 1.1.0 and 1.0.0 object.
    assert CONTRACT_VERSION == "1.2.0"
    assert SUPPORTED_VERSIONS == ("1.0.0", "1.1.0", "1.2.0")
    assert SUPPORTED_VERSIONS[-1] == CONTRACT_VERSION
    assert GIT_TAG == "contracts/ic4-evidence-object/v1.2.0"
    assert EvidenceObject.model_fields["contract_version"].default == "1.2.0"


def test_the_v110_fixtures_validate_and_are_1_1_0() -> None:
    assert {DPA, OPINION, BASELINE, TRIAL, DECISION} <= {p.name for p in IC4_V110_FIXTURES}
    for path in IC4_V110_FIXTURES:
        obj = _obj(path.name)
        assert (obj.contract, obj.contract_version) == ("IC-4", "1.1.0")
        assert json.loads(canonical_json(obj))["contract_version"] == "1.1.0"
        assert obj.record_class == "synthetic_fixture"
        assert "SYNTHETIC" in obj.issuer.name
        assert obj.scope.site_id.startswith("SYNTHETIC") or obj.scope.site_id == ALL_SITES


def test_the_dpa_fixture_is_what_the_dpa_guard_needs() -> None:
    # Plan §4.7: valid, verified, NoExpiry, customer, SiteScope.
    obj = _obj(DPA)
    assert obj.evidence_type is EvidenceType.DATA_PROCESSING_AGREEMENT
    assert obj.issuer.role is IssuerRole.CUSTOMER
    assert isinstance(obj.expiry, NoExpiry)
    assert isinstance(obj.scope, SiteScope)
    assert obj.is_verified and not obj.is_self_issued
    assert obj.status_at(AS_OF) is EvidenceStatus.VALID


def test_the_legal_opinion_fixture_is_third_party_evidence_from_a_legal_adviser() -> None:
    obj = _obj(OPINION)
    assert obj.content_hash() == GOLDEN_HASHES_V110[OPINION]
    assert obj.evidence_type is EvidenceType.LEGAL_OPINION
    assert obj.issuer.role is IssuerRole.LEGAL_ADVISER
    assert not obj.is_self_issued and not obj.is_verified
    assert obj.scope == HYBRID_SITE
    assert "SYNTHETIC" in obj.issuer.name
    assert require_third_party(obj) is obj
    assert obj.status_at(AS_OF) is EvidenceStatus.VALID
    check = assess(
        [obj],
        evidence_type=EvidenceType.LEGAL_OPINION,
        scope=HYBRID_SITE,
        as_of=AS_OF,
        include_synthetic=True,
    )
    assert check.status is EvidenceStatus.VALID
    assert (check.best_is_verified, check.best_is_self_issued) == (False, False)
    found = resolve_ref(
        evidence_ref(obj), [obj], expected_type=EvidenceType.LEGAL_OPINION, include_synthetic=True
    )
    assert found is obj


def test_the_decision_record_fixture_names_and_dates_its_decision() -> None:
    obj = _obj(DECISION)
    assert obj.content_hash() == GOLDEN_HASHES_V110[DECISION]
    assert obj.evidence_type is EvidenceType.DECISION_RECORD
    assert obj.is_self_issued
    assert obj.issuer.reference == "SYNTH-ADR-0001"
    assert obj.effective_from == date(2026, 11, 9)
    assert isinstance(obj.verification, Unverified)
    assert obj.scope == HYBRID_SITE
    assert "SYNTHETIC" in obj.issuer.name
    assert obj.status_at(AS_OF) is EvidenceStatus.VALID


def test_the_self_issued_fixtures_are_marked_on_the_wire() -> None:
    baseline, trial = _obj(BASELINE), _obj(TRIAL)
    for obj in (baseline, trial, _obj(DECISION)):
        assert obj.is_self_issued
        assert obj.issuer.role is SelfIssuerRole.NEURA_SELF_ISSUED
        assert json.loads(canonical_json(obj))["issuer"]["role"] == "neura_self_issued"
    assert baseline.evidence_type is EvidenceType.BASELINE_METHOD
    assert isinstance(baseline.verification, Unverified)
    assert trial.evidence_type is EvidenceType.TRIAL_RECORD
    assert isinstance(trial.verification, Verified)
    assert trial.verification.verifier_role is VerifierRole.INDEPENDENT_THIRD_PARTY


@pytest.mark.parametrize("path", IC4_FIXTURES, ids=lambda p: p.name)
def test_a_1_0_0_object_is_read_by_1_1_0_and_is_not_self_issued(path: Any) -> None:
    obj = _obj_v100(path.name)
    assert obj.contract_version == "1.0.0"
    assert not obj.is_self_issued
    assert require_third_party(obj) is obj


# ------------------------------------------------------------------ 2. the member lists


def test_seventeen_types_are_appended_after_the_fifteen() -> None:
    values = [t.value for t in EvidenceType]
    assert len(values) == 37  # 32 in 1.1.0; IC-4 1.2.0 (WP-GRC-09) appends five after them
    assert set(values[:15]) == V1_0_0_EVIDENCE_TYPES
    assert tuple(values[15:32]) == NEW_TYPES


def test_six_issuer_roles_are_appended_after_the_sixteen() -> None:
    values = [r.value for r in IssuerRole]
    assert len(values) == 22
    assert set(values[:16]) == V1_0_0_ISSUER_ROLES
    assert tuple(values[16:]) == NEW_ROLES
    assert values[15] == "independent_engineer"


def test_the_literal_1_0_0_sets_equal_the_archived_schema() -> None:
    name = "ic4_evidence_object.v1.0.0.schema.json"
    assert name in ARCHIVED
    schema = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    assert schema["properties"]["contract_version"]["const"] == "1.0.0"
    assert V1_0_0_EVIDENCE_TYPES == frozenset(schema["$defs"]["EvidenceType"]["enum"])
    assert V1_0_0_ISSUER_ROLES == frozenset(schema["$defs"]["IssuerRole"]["enum"])
    assert len(V1_0_0_EVIDENCE_TYPES) == 15
    assert len(V1_0_0_ISSUER_ROLES) == 16


def test_self_issuable_types_are_exactly_the_three_the_owner_allowed() -> None:
    # ADR-D10 option b, widened 2026-11-23: pre-registration records and decision records only.
    assert SELF_ISSUABLE_TYPES == frozenset(
        {EvidenceType.BASELINE_METHOD, EvidenceType.TRIAL_RECORD, EvidenceType.DECISION_RECORD}
    )


def test_the_self_issuer_role_is_one_value_and_not_an_issuer_role() -> None:
    assert [r.value for r in SelfIssuerRole] == ["neura_self_issued"]
    assert "neura_self_issued" not in {r.value for r in IssuerRole}
    assert not any("neura" in r.value or "self" in r.value for r in IssuerRole)


def test_an_issuer_role_parses_to_the_right_enum() -> None:
    def role(value: str) -> IssuerRole | SelfIssuerRole:
        payload = {"name": "SYNTHETIC-X (fixture)", "role": value, "reference": "SYNTH-1"}
        return Issuer.model_validate_json(json.dumps(payload)).role

    assert type(role("dno")) is IssuerRole
    assert type(role("neura_self_issued")) is SelfIssuerRole
    for bad in ("neura", "self_issued", "NEURA_SELF_ISSUED", "neura_internal"):
        with pytest.raises(ValidationError):
            role(bad)


# ------------------------------------------------------------------ 3. the four rules (§4.5)


@pytest.mark.parametrize("value", NEW_TYPES)
def test_r1_a_1_0_0_object_with_a_1_1_0_type_is_refused(value: str) -> None:
    data = load_v110(DPA)
    data["evidence_type"] = value
    data["contract_version"] = "1.0.0"
    with pytest.raises(ValidationError, match="not in IC-4 1.0.0"):
        _parse(data)
    data["contract_version"] = "1.1.0"
    assert _parse(data).evidence_type.value == value


@pytest.mark.parametrize("value", sorted(V1_0_0_EVIDENCE_TYPES))
def test_r1_admits_every_1_0_0_type_at_1_0_0(value: str) -> None:
    data = load(VALID_V100)
    data["evidence_type"] = value
    assert _parse(data).contract_version == "1.0.0"


def test_r2_a_1_0_0_object_with_a_1_1_0_role_is_refused() -> None:
    data = load(VALID_V100)
    data["issuer"]["role"] = "neura_self_issued"
    with pytest.raises(ValidationError, match="not in IC-4 1.0.0"):
        _parse(data)


@pytest.mark.parametrize("value", NEW_ROLES)
def test_r2_a_1_0_0_object_with_a_new_third_party_role_is_refused(value: str) -> None:
    data = load(VALID_V100)
    data["issuer"]["role"] = value
    with pytest.raises(ValidationError, match=f"issuer role {value} is not in IC-4 1.0.0"):
        _parse(data)
    data["contract_version"] = "1.1.0"
    obj = _parse(data)
    assert obj.issuer.role is IssuerRole(value) and not obj.is_self_issued


def test_the_legal_opinion_cannot_be_labelled_1_0_0() -> None:
    # Both its type and its issuer's role are 1.1.0 members; R1 is checked first.
    data = load_v110(OPINION)
    data["contract_version"] = "1.0.0"
    with pytest.raises(ValidationError, match="evidence_type legal_opinion is not in IC-4 1.0.0"):
        _parse(data)
    data["evidence_type"] = "connection_offer"
    with pytest.raises(ValidationError, match="issuer role legal_adviser is not in IC-4 1.0.0"):
        _parse(data)


@pytest.mark.parametrize("name", SELF_ISSUED)
def test_r2_a_self_issued_object_cannot_be_labelled_1_0_0(name: str) -> None:
    data = load_v110(name)
    data["contract_version"] = "1.0.0"
    with pytest.raises(ValidationError, match="not in IC-4 1.0.0"):
        _parse(data)


@pytest.mark.parametrize("value", sorted(V1_0_0_ISSUER_ROLES))
def test_r2_admits_every_1_0_0_role_at_1_0_0(value: str) -> None:
    data = load(VALID_V100)
    data["issuer"]["role"] = value
    assert _parse(data).issuer.role.value == value


@pytest.mark.parametrize("value", NOT_SELF_ISSUABLE)
def test_r3_neura_may_not_self_issue_any_other_type(value: str) -> None:
    data = load_v110(BASELINE)
    data["evidence_type"] = value
    with pytest.raises(
        ValidationError, match="Neura may self-issue only its pre-registration and decision records"
    ):
        _parse(data)


def test_r3_a_self_issued_connection_offer_is_refused_and_a_baseline_method_is_accepted() -> None:
    data = load_v110(BASELINE)
    data["evidence_type"] = "connection_offer"
    with pytest.raises(
        ValidationError, match="Neura may self-issue only its pre-registration and decision records"
    ):
        _parse(data)
    data["evidence_type"] = "baseline_method"
    obj = _parse(data)
    assert obj.is_self_issued


@pytest.mark.parametrize("value", ["connection_offer", "legal_opinion", "conformity_certificate"])
def test_r3_still_refuses_a_self_issued_offer_opinion_or_certificate(value: str) -> None:
    data = load_v110(DECISION)
    data["evidence_type"] = value
    with pytest.raises(ValidationError, match="Neura may self-issue only"):
        _parse(data)


@pytest.mark.parametrize("value", ["baseline_method", "trial_record", "decision_record"])
def test_r3_admits_the_three_self_issuable_types(value: str) -> None:
    data = load_v110(BASELINE)
    data["evidence_type"] = value
    obj = _parse(data)
    assert obj.is_self_issued and obj.evidence_type.value == value


def test_r3_a_third_party_may_issue_a_self_issuable_type() -> None:
    obj = _parse(_third_party_baseline())
    assert obj.evidence_type is EvidenceType.BASELINE_METHOD
    assert not obj.is_self_issued
    assert require_third_party(obj) is obj


def test_a_baseline_method_issued_by_an_independent_verifier_needs_no_admission() -> None:
    data = load_v110(BASELINE)
    data["issuer"] = {
        "name": "SYNTHETIC-INDEPENDENT-VERIFIER (fixture; not a real body)",
        "role": "independent_verifier",
        "reference": "SYNTH-IV-BASELINE-0001",
    }
    obj = _parse(data)
    assert obj.issuer.role is IssuerRole.INDEPENDENT_VERIFIER and not obj.is_self_issued
    assert require_third_party(obj) is obj
    found = resolve_ref(
        evidence_ref(obj), [obj], expected_type=EvidenceType.BASELINE_METHOD, include_synthetic=True
    )
    assert found is obj


@pytest.mark.parametrize("name", SELF_ISSUED)
def test_r4_neura_cannot_verify_its_own_record(name: str) -> None:
    data = load_v110(name)
    data["verification"] = {
        "kind": "verified",
        "verifier": "SYNTHETIC-VERIFIER (fixture)",
        "verifier_role": "neura_internal",
        "method": "document_inspection",
        "verified_on": "2026-11-20",
    }
    with pytest.raises(ValidationError, match="cannot verify its own document"):
        _parse(data)
    data["verification"]["verifier_role"] = "independent_third_party"
    assert _parse(data).is_verified


def test_r4_admits_unverified_and_a_countersignature_and_neura_verifying_a_third_party() -> None:
    assert not _obj(BASELINE).is_verified
    assert _obj(TRIAL).is_verified
    dpa = _obj(DPA)
    assert isinstance(dpa.verification, Verified)
    assert dpa.verification.verifier_role is VerifierRole.NEURA_INTERNAL


def test_r5_a_self_issued_decision_record_names_its_decision() -> None:
    data = load_v110(DECISION)
    data["issuer"]["reference"] = _unknown("synthetic fixture: the decision has no id")
    with pytest.raises(ValidationError, match="a decision record names its decision"):
        _parse(data)


def test_r6_a_self_issued_decision_record_states_when_it_takes_effect() -> None:
    data = load_v110(DECISION)
    data["effective_from"] = _unknown("synthetic fixture: no date of effect")
    with pytest.raises(ValidationError, match="a decision record states when it takes effect"):
        _parse(data)


def test_r5_r6_bind_only_neuras_own_decision_records() -> None:
    # A third party's decision record may leave its reference and its effective date unknown.
    data = load_v110(DECISION)
    data["issuer"] = {
        "name": "SYNTHETIC-HOST (fixture; not a real company)",
        "role": "host",
        "reference": _unknown("synthetic fixture: the host's minute has no id"),
    }
    data["effective_from"] = _unknown("synthetic fixture: no date of effect")
    obj = _parse(data)
    assert obj.evidence_type is EvidenceType.DECISION_RECORD and not obj.is_self_issued
    assert isinstance(obj.issuer.reference, Unknown)
    # Neura's own baseline method is not a decision record: R5 and R6 do not apply to it.
    data = load_v110(BASELINE)
    data["issuer"]["reference"] = _unknown("synthetic fixture: no reference")
    data["effective_from"] = _unknown("synthetic fixture: no date of effect")
    assert _parse(data).is_self_issued


# ------------------------------------------------------------------ 4. require_third_party


@pytest.mark.parametrize("name", [DPA, OPINION])
def test_require_third_party_returns_a_third_party_object(name: str) -> None:
    obj = _obj(name)
    assert require_third_party(obj) is obj


@pytest.mark.parametrize("name", SELF_ISSUED)
def test_require_third_party_raises_on_a_self_issued_object(name: str) -> None:
    obj = _obj(name)
    with pytest.raises(SelfIssuedEvidenceRefused) as caught:
        require_third_party(obj)
    message = str(caught.value)
    assert obj.evidence_type.value in message
    assert obj.content_hash() in message
    assert isinstance(caught.value, EvidenceRefused)
    assert isinstance(caught.value, ValueError)


@pytest.mark.parametrize(
    "value",
    [load_v110(DPA), canonical_json(EvidenceObject.model_validate_json(dumps(load_v110(DPA)))), 7],
    ids=["dict", "json", "int"],
)
def test_require_third_party_accepts_only_an_evidence_object(value: Any) -> None:
    with pytest.raises(TypeError, match="only an EvidenceObject"):
        require_third_party(value)


def test_require_third_party_refuses_another_contract_model() -> None:
    ref = evidence_ref(_obj_v100(VALID_V100))
    with pytest.raises(TypeError, match="only an EvidenceObject"):
        require_third_party(ref)  # type: ignore[arg-type]


def _role_swapped(obj: EvidenceObject, role: str) -> EvidenceObject:
    """``obj`` with its issuer role replaced by the raw string, past validation."""
    issuer = Issuer.model_construct(name=obj.issuer.name, role=role, reference=obj.issuer.reference)
    return obj.model_copy(update={"issuer": issuer})


def test_require_third_party_catches_a_model_construct_role_swap() -> None:
    # A third-party baseline method whose role is swapped to the raw string "neura_self_issued"
    # after construction. Unvalidated, it does not look self-issued; on the wire it is.
    smuggled = _role_swapped(_parse(_third_party_baseline()), "neura_self_issued")
    assert not smuggled.is_self_issued
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # pydantic warns that the role is not an enum member
        with pytest.raises(SelfIssuedEvidenceRefused):
            require_third_party(smuggled)


def test_require_third_party_refuses_a_smuggled_invalid_object() -> None:
    # A DPA swapped to self-issued is not a valid object at all (R3): re-validation refuses it.
    smuggled = _role_swapped(_obj(DPA), "neura_self_issued")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(ValidationError, match="Neura may self-issue only"):
            require_third_party(smuggled)


# ------------------------------------------------------------------ 5. resolve_ref (§4.6 order)


def _all_objects() -> list[EvidenceObject]:
    return [_obj_v100(p.name) for p in IC4_FIXTURES] + [_obj(p.name) for p in IC4_V110_FIXTURES]


def test_resolve_ref_1_no_object_has_the_hash() -> None:
    ref = _ref("1.0.0", "sha256:" + "0" * 64)
    with pytest.raises(EvidenceNotFound, match="no evidence object"):
        resolve_ref(
            ref,
            _all_objects(),
            expected_type=EvidenceType.CONNECTION_OFFER,
            admit_self_issued=True,
            include_synthetic=True,
        )
    with pytest.raises(LookupError):
        resolve_ref(ref, [])


def test_resolve_ref_2_a_reference_whose_version_is_not_the_objects_is_refused() -> None:
    dpa = _obj(DPA)  # a 1.1.0 object
    ref = _ref("1.0.0", dpa.content_hash())  # a reference that claims 1.0.0
    with pytest.raises(EvidenceRefused, match="contract_version") as caught:
        resolve_ref(
            ref,
            _all_objects(),
            expected_type=EvidenceType.DATA_PROCESSING_AGREEMENT,
            admit_self_issued=True,
            include_synthetic=True,
        )
    assert type(caught.value) is EvidenceRefused


def test_resolve_ref_2_comes_before_3_the_synthetic_refusal() -> None:
    dpa = _obj(DPA)
    with pytest.raises(EvidenceRefused) as caught:
        resolve_ref(_ref("1.0.0", dpa.content_hash()), [dpa])
    assert type(caught.value) is EvidenceRefused


def test_resolve_ref_3_a_synthetic_fixture_is_refused_by_default() -> None:
    obj = _obj_v100(VALID_V100)
    with pytest.raises(SyntheticEvidenceRefused, match="synthetic"):
        resolve_ref(evidence_ref(obj), _all_objects())
    with pytest.raises(SyntheticEvidenceRefused):
        resolve_ref(
            evidence_ref(obj),
            _all_objects(),
            expected_type=EvidenceType.CONNECTION_OFFER,
            admit_self_issued=True,
        )


def test_resolve_ref_0_admitting_a_self_issued_record_needs_the_expected_type() -> None:
    # Raised before any lookup: the reference here matches nothing.
    ref = _ref("1.0.0", "sha256:" + "0" * 64)
    for objects in ([], _all_objects()):
        with pytest.raises(ValueError, match="name the expected_type") as caught:
            resolve_ref(ref, objects, admit_self_issued=True, include_synthetic=True)
        assert type(caught.value) is ValueError


def test_resolve_ref_3_an_object_of_another_type_is_refused() -> None:
    offer = _obj_v100(VALID_V100)
    with pytest.raises(EvidenceRefused, match="expected") as caught:
        resolve_ref(
            evidence_ref(offer),
            [offer],
            expected_type=EvidenceType.G99_APPROVAL,
            include_synthetic=True,
        )
    assert type(caught.value) is EvidenceRefused
    assert "g99_approval" in str(caught.value) and "connection_offer" in str(caught.value)
    found = resolve_ref(
        evidence_ref(offer),
        [offer],
        expected_type=EvidenceType.CONNECTION_OFFER,
        include_synthetic=True,
    )
    assert found is offer


def test_resolve_ref_4_a_self_issued_record_is_refused_unless_admitted_by_type() -> None:
    objects = _all_objects()
    for name, kind in (
        (BASELINE, EvidenceType.BASELINE_METHOD),
        (TRIAL, EvidenceType.TRIAL_RECORD),
    ):
        obj = _obj(name)
        ref = evidence_ref(obj)
        with pytest.raises(SelfIssuedEvidenceRefused, match="self-issued by Neura"):
            resolve_ref(ref, objects, expected_type=kind, include_synthetic=True)
        with pytest.raises(SelfIssuedEvidenceRefused):
            resolve_ref(ref, objects, include_synthetic=True)
        found = resolve_ref(
            ref, objects, expected_type=kind, admit_self_issued=True, include_synthetic=True
        )
        assert found is next(o for o in objects if o.content_hash() == obj.content_hash())


def test_a_decision_record_resolves_only_by_its_own_type_and_only_when_admitted() -> None:
    objects = _all_objects()
    decision = next(o for o in objects if o.evidence_type is EvidenceType.DECISION_RECORD)
    assert decision == _obj(DECISION)
    ref = evidence_ref(decision)
    with pytest.raises(SyntheticEvidenceRefused):
        resolve_ref(ref, objects)
    with pytest.raises(SelfIssuedEvidenceRefused):
        resolve_ref(ref, objects, include_synthetic=True)
    with pytest.raises(SelfIssuedEvidenceRefused):
        resolve_ref(
            ref, objects, expected_type=EvidenceType.DECISION_RECORD, include_synthetic=True
        )
    for slot in (EvidenceType.BASELINE_METHOD, EvidenceType.TRIAL_RECORD):
        # A decision record cannot fill a baseline-method or trial slot, even when admitted.
        with pytest.raises(EvidenceRefused, match="expected") as caught:
            resolve_ref(
                ref, objects, expected_type=slot, admit_self_issued=True, include_synthetic=True
            )
        assert type(caught.value) is EvidenceRefused
    found = resolve_ref(
        ref,
        objects,
        expected_type=EvidenceType.DECISION_RECORD,
        admit_self_issued=True,
        include_synthetic=True,
    )
    assert found is decision


def test_resolve_ref_checks_run_in_the_stated_order() -> None:
    # Each object below fails several checks; the first in §11.4's order is the one reported.
    baseline = _obj(BASELINE)  # 1.1.0, synthetic, self-issued, a baseline method
    objects = [baseline]
    missing = _ref("1.0.0", "sha256:" + "1" * 64)
    with pytest.raises(ValueError, match="name the expected_type"):  # 0 before 1
        resolve_ref(missing, objects, admit_self_issued=True)
    with pytest.raises(EvidenceNotFound):  # 1
        resolve_ref(missing, objects, expected_type=EvidenceType.TRIAL_RECORD)
    wrong_version = _ref("1.0.0", baseline.content_hash())
    with pytest.raises(EvidenceRefused, match="contract_version"):  # 2 before 3, 4, 5
        resolve_ref(wrong_version, objects, expected_type=EvidenceType.TRIAL_RECORD)
    ref = evidence_ref(baseline)
    with pytest.raises(EvidenceRefused, match="expected") as caught:  # 3 before 4 and 5
        resolve_ref(ref, objects, expected_type=EvidenceType.TRIAL_RECORD)
    assert type(caught.value) is EvidenceRefused
    with pytest.raises(SyntheticEvidenceRefused):  # 4 before 5
        resolve_ref(ref, objects, expected_type=EvidenceType.BASELINE_METHOD)
    with pytest.raises(SelfIssuedEvidenceRefused):  # 5
        resolve_ref(
            ref, objects, expected_type=EvidenceType.BASELINE_METHOD, include_synthetic=True
        )
    found = resolve_ref(  # 6
        ref,
        objects,
        expected_type=EvidenceType.BASELINE_METHOD,
        admit_self_issued=True,
        include_synthetic=True,
    )
    assert found is baseline


def test_resolve_ref_5_returns_the_object() -> None:
    objects = _all_objects()
    for obj in objects:
        if obj.contract_version == "1.0.0":
            assert resolve_ref(evidence_ref(obj), objects, include_synthetic=True) is obj


# ------------------------------------------------------------------ 6. assess


def test_assess_skips_a_self_issued_match_by_default() -> None:
    baseline = _obj(BASELINE)
    kw: dict[str, Any] = {
        "evidence_type": EvidenceType.BASELINE_METHOD,
        "scope": CHILLED_WATER,
        "as_of": AS_OF,
        "include_synthetic": True,
    }
    default = assess([baseline], **kw)
    assert default.status is EvidenceStatus.ABSENT
    assert default.matches == ()
    assert default.best_is_self_issued is False
    admitted = assess([baseline], admit_self_issued=True, **kw)
    assert admitted.status is EvidenceStatus.VALID
    assert admitted.matches == ((baseline.content_hash(), EvidenceStatus.VALID),)
    assert admitted.best_is_self_issued is True
    assert admitted.best_is_verified is False


def test_assess_reports_a_self_issued_decision_record_absent_by_default() -> None:
    decision = _obj(DECISION)
    kw: dict[str, Any] = {
        "evidence_type": EvidenceType.DECISION_RECORD,
        "scope": HYBRID_SITE,
        "as_of": AS_OF,
        "include_synthetic": True,
    }
    assert assess([decision], **kw).status is EvidenceStatus.ABSENT
    admitted = assess([decision], admit_self_issued=True, **kw)
    assert admitted.status is EvidenceStatus.VALID
    assert (admitted.best_is_self_issued, admitted.best_is_verified) == (True, False)


def test_assess_finds_a_countersigned_self_issued_trial_only_when_admitted() -> None:
    trial = _obj(TRIAL)
    kw: dict[str, Any] = {
        "evidence_type": EvidenceType.TRIAL_RECORD,
        "scope": CHILLED_WATER,
        "as_of": AS_OF,
        "include_synthetic": True,
    }
    assert assess([trial], **kw).status is EvidenceStatus.ABSENT
    admitted = assess([trial], admit_self_issued=True, **kw)
    assert admitted.status is EvidenceStatus.VALID
    assert admitted.best_is_self_issued and admitted.best_is_verified


def test_assess_ranking_is_unchanged_by_who_issued() -> None:
    # Verified outranks unverified whoever issued it: the self-issued, countersigned record wins
    # once admitted, and the third-party one is all that remains by default.
    data = load_v110(TRIAL)
    data["issuer"] = {
        "name": "SYNTHETIC-HOST (fixture; not a real company)",
        "role": "host",
        "reference": "SYNTH-HOST-TRIAL-0001",
    }
    data["verification"] = {"kind": "unverified", "reason": "synthetic fixture: not checked"}
    third_party = _parse(data)
    self_issued = _obj(TRIAL)
    kw: dict[str, Any] = {
        "evidence_type": EvidenceType.TRIAL_RECORD,
        "scope": CHILLED_WATER,
        "as_of": AS_OF,
        "include_synthetic": True,
    }
    default = assess([self_issued, third_party], **kw)
    assert default.matches == ((third_party.content_hash(), EvidenceStatus.VALID),)
    assert (default.best_is_verified, default.best_is_self_issued) == (False, False)
    admitted = assess([self_issued, third_party], admit_self_issued=True, **kw)
    assert {h for h, _ in admitted.matches} == {
        third_party.content_hash(),
        self_issued.content_hash(),
    }
    assert (admitted.best_is_verified, admitted.best_is_self_issued) == (True, True)


def test_assess_on_third_party_evidence_never_reports_self_issued() -> None:
    dpa = _obj(DPA)
    check = assess(
        [dpa, _obj(BASELINE), _obj(TRIAL)],
        evidence_type=EvidenceType.DATA_PROCESSING_AGREEMENT,
        scope=SiteScope(kind="site", site_id="SYNTHETIC-SITE-HYBRID"),
        as_of=AS_OF,
        include_synthetic=True,
        admit_self_issued=True,
    )
    assert check.status is EvidenceStatus.VALID
    assert (check.best_is_verified, check.best_is_self_issued) == (True, False)


# ------------------------------------------------------------------ 7. evidence_ref


@pytest.mark.parametrize("path", IC4_FIXTURES, ids=lambda p: p.name)
def test_evidence_ref_of_a_1_0_0_object_says_1_0_0(path: Any) -> None:
    obj = _obj_v100(path.name)
    ref = evidence_ref(obj)
    assert (ref.contract_version, ref.evidence_hash) == ("1.0.0", obj.content_hash())


@pytest.mark.parametrize("path", IC4_V110_FIXTURES, ids=lambda p: p.name)
def test_evidence_ref_of_a_1_1_0_object_says_1_1_0(path: Any) -> None:
    obj = _obj(path.name)
    ref = evidence_ref(obj)
    assert (ref.contract, ref.contract_version) == ("IC-4", "1.1.0")
    assert ref.evidence_hash == obj.content_hash() == GOLDEN_HASHES_V110[path.name]
    # A valid wire object now: it survives its own round trip.
    assert EvidenceRef.model_validate_json(ref.model_dump_json()) == ref


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.10.0"])
def test_an_evidence_ref_accepts_the_1x_range(version: str) -> None:
    assert _ref(version, "sha256:" + "a" * 64).contract_version == version


@pytest.mark.parametrize("version", ["2.0.0", "0.9.0", "1.1", "1.01.0", "1.1.0\n", ""])
def test_an_evidence_ref_refuses_anything_outside_1x(version: str) -> None:
    with pytest.raises(ValidationError):
        _ref(version, "sha256:" + "a" * 64)


def test_resolve_ref_returns_a_third_party_1_1_0_object() -> None:
    objects = _all_objects()
    for name, kind in (
        (DPA, EvidenceType.DATA_PROCESSING_AGREEMENT),
        (OPINION, EvidenceType.LEGAL_OPINION),
    ):
        obj = _obj(name)
        found = resolve_ref(evidence_ref(obj), objects, expected_type=kind, include_synthetic=True)
        assert found == obj and found.contract_version == "1.1.0"


# ------------------------------------------------------------------ 8. assumptions, self-issued


def _derived() -> DerivedAssumption:
    return DerivedAssumption(
        kind="derived_input",
        key="baseline.method",
        missing_input="a registered baseline method",
        value=1.0,
        unit="ratio",
        basis="derived from the modelled surface",
        derivation_method="fable_advisor",
        derived_by="claude-fable-5",
        confidence_ceiling=ConfidenceBand.LOW,
        confirmation_ask="register the method",
    )


def _library_assumption() -> Assumption:
    return Assumption(
        value=0.5,
        unit="ratio",
        source="published range",
        status=AssumptionStatus.NEEDS_REVIEW,
        owner_or_reviewer="Olly",
        confidence=Confidence.LOW,
        last_reviewed=date(2026, 8, 18),
        notes="assumed",
    )


@pytest.mark.parametrize("name", SELF_ISSUED)
@pytest.mark.parametrize("make", [_derived, _library_assumption])
def test_an_assumption_merged_onto_a_self_issued_record_is_refused(name: str, make: Any) -> None:
    payload = {**load_v110(name), **make().model_dump(mode="json")}
    with pytest.raises(ValidationError, match="an assumption is not evidence"):
        _parse(payload)


@pytest.mark.parametrize("make", [_derived, _library_assumption])
def test_an_assumption_instance_is_not_a_self_issued_record(make: Any) -> None:
    with pytest.raises(ValidationError, match="an assumption is not evidence"):
        EvidenceObject.model_validate(make())


@pytest.mark.parametrize("name", SELF_ISSUED)
@pytest.mark.parametrize(
    "key,value",
    [
        ("status", "assumed_approved"),
        ("assumed", True),
        ("derivation_method", "fable_advisor"),
        ("study_basis", "all_streams_enabled_study_v1"),
        ("confirmation_ask", "please confirm"),
    ],
)
def test_assumption_marker_fields_are_refused_on_a_self_issued_record(
    name: str, key: str, value: Any
) -> None:
    data = load_v110(name)
    data[key] = value
    with pytest.raises(ValidationError, match="an assumption is not evidence"):
        _parse(data)


@pytest.mark.parametrize("name", SELF_ISSUED)
@pytest.mark.parametrize(
    "path,token",
    [
        (("issuer", "reference"), "pending_permission"),
        (("title",), "placeholder"),
        (("scope", "site_id"), "derived_input"),
        (("expiry", "reason"), "assumed"),
    ],
)
def test_assumption_status_tokens_are_refused_on_a_self_issued_record(
    name: str, path: tuple[str, ...], token: str
) -> None:
    data = load_v110(name)
    node = data
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = token
    with pytest.raises(ValidationError, match="an assumption is not evidence"):
        _parse(data)


@pytest.mark.parametrize("name", SELF_ISSUED)
def test_a_self_issued_record_needs_a_document(name: str) -> None:
    data = load_v110(name)
    data["document_hash"] = EMPTY_SHA256
    with pytest.raises(ValidationError, match="no document"):
        _parse(data)


@pytest.mark.parametrize("name", SELF_ISSUED)
@pytest.mark.parametrize("role", ["neura", "self", "assumption", "internal"])
def test_neura_or_self_is_still_not_an_issuer_role(name: str, role: str) -> None:
    data = load_v110(name)
    data["issuer"]["role"] = role
    with pytest.raises(ValidationError):
        _parse(data)


@pytest.mark.parametrize("name", [DPA, *SELF_ISSUED])
def test_serialise_evidence_round_trips_a_1_1_0_object(name: str) -> None:
    obj = _obj(name)
    text = serialise_evidence(obj)
    assert text == canonical_json(obj)
    assert EvidenceObject.model_validate_json(text) == obj


def test_serialise_evidence_refuses_a_smuggled_self_issued_type() -> None:
    smuggled = _obj(BASELINE).model_copy(update={"evidence_type": EvidenceType.CONNECTION_OFFER})
    with pytest.raises(ValidationError, match="Neura may self-issue only"):
        serialise_evidence(smuggled)


# ------------------------------------------------------------------ 9. exports


def test_the_new_names_are_exported() -> None:
    from gridq.contracts import evidence

    new = {
        "SELF_ISSUABLE_TYPES",
        "SUPPORTED_VERSIONS",
        "V1_0_0_EVIDENCE_TYPES",
        "V1_0_0_ISSUER_ROLES",
        "EvidenceNotFound",
        "EvidenceRefused",
        "SelfIssuedEvidenceRefused",
        "SelfIssuerRole",
        "SyntheticEvidenceRefused",
        "require_third_party",
        "resolve_ref",
    }
    assert new <= set(evidence.__all__)
    assert {"require_third_party", "resolve_ref"} <= set(contracts.__all__)
    assert contracts.require_third_party is require_third_party
    assert contracts.resolve_ref is resolve_ref
    assert issubclass(EvidenceNotFound, LookupError)
    assert not issubclass(EvidenceNotFound, EvidenceRefused)
    assert issubclass(SyntheticEvidenceRefused, EvidenceRefused)
    assert issubclass(EvidenceRefused, ValueError)
