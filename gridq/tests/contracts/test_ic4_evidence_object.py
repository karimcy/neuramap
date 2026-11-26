"""IC-4 EvidenceObject v1.0.0 conformance (roadmap WP-BO-01).

Acceptance:
1. Contract version is tagged and conformance tests pass.
2. An expired evidence object validates as expired, not as absent.
3. An assumption can never be serialised as an evidence object.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from _contracts_support import IC4_FIXTURES, dumps, load
from pydantic import ValidationError

from gridq.assumptions.models import Assumption, AssumptionStatus
from gridq.contracts._base import EMPTY_SHA256, canonical_json
from gridq.contracts.evidence import (
    ALL_SITES,
    CONTRACT_VERSION,
    GIT_TAG,
    EvidenceObject,
    EvidenceStatus,
    EvidenceType,
    IssuerRole,
    SiteScope,
    StreamScope,
    assess,
    evidence_ref,
    scope_covers,
    serialise_evidence,
)
from gridq.schemas.derived import DerivedAssumption
from gridq.schemas.enums import Confidence, ConfidenceBand

EXPIRED = "ic4_expired_aggregator_agreement.synthetic.json"
VALID = "ic4_valid_connection_offer.synthetic.json"
UNVERIFIED = "ic4_unverified_regulatory_publication.synthetic.json"
NO_EXPIRY = "ic4_no_expiry_customer_consent.synthetic.json"


def _obj(name: str) -> EvidenceObject:
    return EvidenceObject.model_validate_json(dumps(load(name)))


def _derived() -> DerivedAssumption:
    return DerivedAssumption(
        kind="derived_input",
        key="connection.mic_kw",
        missing_input="agreed import capacity",
        value=500.0,
        unit="kW",
        basis="derived from the observed peak plus a margin",
        derivation_method="fable_advisor",
        derived_by="claude-fable-5",
        confidence_ceiling=ConfidenceBand.LOW,
        confirmation_ask="send the connection agreement",
    )


def _library_assumption() -> Assumption:
    return Assumption(
        value=0.30581,
        unit="GBP/kWh",
        source="published range, favourable end",
        status=AssumptionStatus.NEEDS_REVIEW,
        owner_or_reviewer="Olly",
        confidence=Confidence.LOW,
        last_reviewed=date(2026, 8, 18),
        notes="assumed approved under D57",
    )


# ---------------------------------------------------------------- 1. version tagged, fixtures pass


def test_contract_version_is_tagged_in_module_and_on_every_instance() -> None:
    assert CONTRACT_VERSION == "1.2.0"
    assert GIT_TAG == f"contracts/ic4-evidence-object/v{CONTRACT_VERSION}"
    for path in IC4_FIXTURES:
        obj = _obj(path.name)
        assert (obj.contract, obj.contract_version) == ("IC-4", "1.0.0")
        assert json.loads(canonical_json(obj))["contract_version"] == "1.0.0"


@pytest.mark.parametrize(
    "field,value",
    [("contract_version", "1.0.1"), ("contract_version", "2.0.0"), ("contract", "IC-1")],
)
def test_a_payload_of_another_contract_or_version_is_refused(field: str, value: str) -> None:
    data = load(VALID)
    data[field] = value
    with pytest.raises(ValidationError):
        EvidenceObject.model_validate_json(dumps(data))


def test_at_least_four_fixtures_and_all_validate() -> None:
    assert len(IC4_FIXTURES) >= 4
    for path in IC4_FIXTURES:
        _obj(path.name)


def test_every_fixture_is_marked_synthetic_and_names_no_real_party() -> None:
    for path in IC4_FIXTURES:
        obj = _obj(path.name)
        assert obj.record_class == "synthetic_fixture"
        assert "SYNTHETIC" in obj.issuer.name
        assert obj.scope.site_id.startswith("SYNTHETIC") or obj.scope.site_id == ALL_SITES


def test_every_spec_ic4_element_is_a_required_field() -> None:
    # spec §10A IC-4: type, issuer, document hash, dates, expiry, scope (site/stream), verifier
    required = {
        "evidence_type",
        "issuer",
        "document_hash",
        "issued_on",
        "effective_from",
        "expiry",
        "scope",
        "verification",
    }
    fields = EvidenceObject.model_fields
    assert required <= set(fields)
    assert all(fields[f].is_required() for f in required)


@pytest.mark.parametrize("field", sorted(f for f in EvidenceObject.model_fields))
def test_null_is_refused_for_every_field(field: str) -> None:
    data = load(VALID)
    data[field] = None
    with pytest.raises(ValidationError):
        EvidenceObject.model_validate_json(dumps(data))


def test_instances_are_immutable() -> None:
    obj = _obj(VALID)
    with pytest.raises(ValidationError):
        obj.title = "changed"  # type: ignore[misc]


def test_dates_must_be_iso_calendar_dates() -> None:
    for bad in ("2026-10-01T00:00:00", "01/10/2026", 1_759_276_800):
        data = load(VALID)
        data["issued_on"] = bad
        with pytest.raises(ValidationError):
            EvidenceObject.model_validate_json(dumps(data))


def test_expiry_must_follow_issue_and_effective_dates() -> None:
    data = load(VALID)
    data["expiry"] = {"kind": "expires_on", "expires_on": data["issued_on"]}
    with pytest.raises(ValidationError, match="after issued_on"):
        EvidenceObject.model_validate_json(dumps(data))


def test_verification_cannot_precede_issue() -> None:
    data = load(VALID)
    data["verification"]["verified_on"] = "2026-09-30"
    with pytest.raises(ValidationError, match="verified_on"):
        EvidenceObject.model_validate_json(dumps(data))


# ---------------------------------------------------------------- 2. expired is not absent


def test_an_expired_object_validates_and_reports_expired() -> None:
    obj = _obj(EXPIRED)  # constructing it must not raise
    assert obj.status_at(date(2026, 11, 16)) is EvidenceStatus.EXPIRED


def test_expiry_boundary_is_exclusive() -> None:
    obj = _obj(EXPIRED)  # expires_on 2026-10-01
    assert obj.status_at(date(2026, 9, 30)) is EvidenceStatus.VALID
    assert obj.status_at(date(2026, 10, 1)) is EvidenceStatus.EXPIRED


@pytest.mark.parametrize("days_after", [0, 1, 30, 365, 3650])
def test_assess_reports_expired_never_absent_for_a_present_expired_object(days_after: int) -> None:
    obj = _obj(EXPIRED)
    as_of = date(2026, 10, 1) + timedelta(days=days_after)
    scope = StreamScope(
        kind="stream", stream="BALANCING_MECHANISM", site_id="SYNTHETIC-SITE-HYBRID"
    )
    check = assess(
        [obj],
        evidence_type=EvidenceType.AGGREGATOR_AGREEMENT,
        scope=scope,
        as_of=as_of,
        include_synthetic=True,
    )
    assert check.status is EvidenceStatus.EXPIRED
    assert check.matches == ((obj.content_hash(), EvidenceStatus.EXPIRED),)


def test_assess_reports_absent_only_when_nothing_matches() -> None:
    scope = SiteScope(kind="site", site_id="SYNTHETIC-SITE-HYBRID")
    check = assess(
        [_obj(EXPIRED), _obj(VALID)],
        evidence_type=EvidenceType.G99_APPROVAL,
        scope=scope,
        as_of=date(2026, 11, 16),
        include_synthetic=True,
    )
    assert check.status is EvidenceStatus.ABSENT
    assert check.matches == ()
    assert check.best_is_verified is False


def test_a_valid_object_outranks_an_expired_one_and_both_are_listed() -> None:
    expired = _obj(EXPIRED)
    data = load(EXPIRED)
    data["expiry"] = {"kind": "expires_on", "expires_on": "2027-10-01"}
    data["issuer"]["reference"] = "SYNTH-AGG-0008"
    renewed = EvidenceObject.model_validate_json(dumps(data))
    scope = StreamScope(
        kind="stream", stream="BALANCING_MECHANISM", site_id="SYNTHETIC-SITE-HYBRID"
    )
    check = assess(
        [expired, renewed],
        evidence_type=EvidenceType.AGGREGATOR_AGREEMENT,
        scope=scope,
        as_of=date(2026, 11, 16),
        include_synthetic=True,
    )
    assert check.status is EvidenceStatus.VALID
    assert {s for _, s in check.matches} == {EvidenceStatus.VALID, EvidenceStatus.EXPIRED}


def test_unknown_validity_and_no_expiry_statuses() -> None:
    assert _obj(UNVERIFIED).status_at(date(2026, 11, 16)) is EvidenceStatus.VALIDITY_UNKNOWN
    assert _obj(NO_EXPIRY).status_at(date(2040, 1, 1)) is EvidenceStatus.VALID
    assert _obj(NO_EXPIRY).status_at(date(2026, 11, 1)) is EvidenceStatus.NOT_YET_EFFECTIVE
    assert _obj(UNVERIFIED).is_verified is False


def test_synthetic_fixtures_are_ignored_unless_asked_for() -> None:
    scope = SiteScope(kind="site", site_id="SYNTHETIC-SITE-HYBRID")
    kw: dict[str, Any] = {
        "evidence_type": EvidenceType.CONNECTION_OFFER,
        "scope": scope,
        "as_of": date(2026, 11, 16),
    }
    assert assess([_obj(VALID)], **kw).status is EvidenceStatus.ABSENT
    assert assess([_obj(VALID)], include_synthetic=True, **kw).status is EvidenceStatus.VALID


def test_scope_coverage_rules() -> None:
    site = SiteScope(kind="site", site_id="S1")
    bm_s1 = StreamScope(kind="stream", stream="BALANCING_MECHANISM", site_id="S1")
    bm_all = StreamScope(kind="stream", stream="BALANCING_MECHANISM", site_id=ALL_SITES)
    cm_s1 = StreamScope(kind="stream", stream="CAPACITY_MARKET", site_id="S1")
    assert scope_covers(site, bm_s1) and scope_covers(site, site)
    assert scope_covers(bm_all, bm_s1) and scope_covers(bm_s1, bm_s1)
    assert not scope_covers(bm_s1, site)
    assert not scope_covers(cm_s1, bm_s1)
    assert not scope_covers(SiteScope(kind="site", site_id="S2"), bm_s1)


# ------------------------------------------------------------ 3. an assumption is never evidence


def test_no_evidence_type_or_issuer_role_names_an_assumption_or_neura() -> None:
    words = ("assum", "derived", "placeholder", "pending", "study", "self", "neura", "estimate")
    for member in [*EvidenceType, *IssuerRole]:
        assert not any(w in member.value for w in words), member


@pytest.mark.parametrize("make", [_derived, _library_assumption])
def test_an_assumption_instance_cannot_become_an_evidence_object(make: Any) -> None:
    with pytest.raises(ValidationError, match="an assumption is not evidence"):
        EvidenceObject.model_validate(make())


@pytest.mark.parametrize("make", [_derived, _library_assumption])
def test_an_assumption_dump_cannot_become_an_evidence_object(make: Any) -> None:
    payload = make().model_dump(mode="json")
    with pytest.raises(ValidationError, match="an assumption is not evidence"):
        EvidenceObject.model_validate_json(dumps(payload))


@pytest.mark.parametrize("make", [_derived, _library_assumption])
def test_an_assumption_merged_onto_real_evidence_fields_is_refused(make: Any) -> None:
    payload = {**load(VALID), **make().model_dump(mode="json")}
    with pytest.raises(ValidationError, match="an assumption is not evidence"):
        EvidenceObject.model_validate_json(dumps(payload))


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
def test_assumption_marker_fields_are_refused(key: str, value: Any) -> None:
    data = load(VALID)
    data[key] = value
    with pytest.raises(ValidationError, match="an assumption is not evidence"):
        EvidenceObject.model_validate_json(dumps(data))


@pytest.mark.parametrize(
    "path,token",
    [
        (("verification", "method"), "assumed"),
        (("issuer", "reference"), "pending_permission"),
        (("title",), "placeholder"),
        (("scope", "site_id"), "derived_input"),
    ],
)
def test_assumption_status_tokens_are_refused_anywhere(path: tuple[str, ...], token: str) -> None:
    data = load(VALID)
    node = data
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = token
    with pytest.raises(ValidationError, match="an assumption is not evidence"):
        EvidenceObject.model_validate_json(dumps(data))


def test_a_document_hash_of_no_document_is_refused() -> None:
    data = load(VALID)
    data["document_hash"] = EMPTY_SHA256
    with pytest.raises(ValidationError, match="no document"):
        EvidenceObject.model_validate_json(dumps(data))


@pytest.mark.parametrize("role", ["neura", "self", "assumption", "internal"])
def test_neura_or_self_cannot_be_an_issuer(role: str) -> None:
    data = load(VALID)
    data["issuer"]["role"] = role
    with pytest.raises(ValidationError):
        EvidenceObject.model_validate_json(dumps(data))


def test_evidence_object_cannot_be_subclassed() -> None:
    with pytest.raises(TypeError, match="final"):

        class AssumedEvidence(EvidenceObject):  # type: ignore[misc]
            pass


def test_serialise_evidence_accepts_only_an_evidence_object() -> None:
    obj = _obj(VALID)
    assert serialise_evidence(obj) == canonical_json(obj)
    for bad in (_derived(), _library_assumption(), obj.model_dump(), "{}"):
        with pytest.raises(TypeError, match="only an EvidenceObject"):
            serialise_evidence(bad)  # type: ignore[arg-type]


def test_serialise_evidence_revalidates_a_bypassed_construction() -> None:
    # model_construct skips validation; the serialiser must still refuse the result.
    smuggled = EvidenceObject.model_construct(**{**_obj(VALID).__dict__, "title": "assumed"})
    with pytest.raises(ValidationError, match="an assumption is not evidence"):
        serialise_evidence(smuggled)


def test_the_type_checker_refuses_an_assumption_passed_as_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Static half of the guarantee: mypy rejects the call before it ever runs."""
    mypy_api = pytest.importorskip("mypy.api")
    snippet = tmp_path / "assumption_as_evidence.py"
    snippet.write_text(
        "from gridq.contracts.evidence import serialise_evidence, evidence_ref\n"
        "from gridq.schemas.derived import DerivedAssumption\n"
        "def leak(a: DerivedAssumption) -> None:\n"
        "    serialise_evidence(a)\n"
        "    evidence_ref(a)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MYPYPATH", str(Path(__file__).resolve().parents[2] / "src"))
    stdout, _, status = mypy_api.run(
        [
            str(snippet),
            "--config-file=",
            "--follow-imports=silent",
            "--ignore-missing-imports",
            "--no-error-summary",
            "--python-version=3.12",
            f"--cache-dir={tmp_path / '.mypy_cache'}",
        ]
    )
    assert status == 1, stdout
    assert stdout.count("[arg-type]") == 2, stdout


def test_evidence_ref_points_at_the_content_hash() -> None:
    obj = _obj(VALID)
    ref = evidence_ref(obj)
    assert ref.evidence_hash == obj.content_hash()
    assert (ref.contract, ref.contract_version) == ("IC-4", obj.contract_version)
