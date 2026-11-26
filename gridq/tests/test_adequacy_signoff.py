"""WP-SITE-04 acceptance 2: critical-facilities sign-off of the floor is an IC-4 evidence object.

Without a valid, verified sign-off of *this* floor statement the floor is unsigned, no output
reads as approved, and the run says what is missing (D173). IC-4 1.2.0's existing
``critical_facilities_signoff`` type fits; no IC-4 change is made.
"""

from __future__ import annotations

import json
import re
from datetime import date
from fractions import Fraction
from typing import Any

import pytest
from pydantic import ValidationError
from tests import _adequacy_support as A

from gridq.adequacy import (
    AdequacyResult,
    Approved,
    Unsigned,
    UnsignedReason,
    admit_floor_signoff,
    approval_label,
    floor_statement_hash,
    render_floor_statement,
    run_adequacy,
    size_floor,
)
from gridq.adequacy.floor import ResilienceFloorPolicy
from gridq.adequacy.simulator import render_summary
from gridq.contracts.evidence import EvidenceType

AS_OF = date(2027, 1, 10)
FORBIDDEN = re.compile(r"approved|signed off|sign-off accepted|accepted", re.IGNORECASE)


def _policy(kw: int = 400, **req: Any) -> ResilienceFloorPolicy:
    b = A.bess(eta=Fraction(1), initial=1500, operating=1500)
    return size_floor(A.requirement(kw=kw, **req), b, A.flat_partition(), load=A.minute_load())


def _hash(policy: ResilienceFloorPolicy) -> str:
    h = floor_statement_hash(policy)
    assert isinstance(h, str)
    return h


def _run(policy: ResilienceFloorPolicy, evidence: Any = (), **kw: Any) -> AdequacyResult:
    b = A.bess(eta=Fraction(1), initial=1500, operating=1500)
    ss = A.scenario_set([("a", Fraction(1), [(600, 120, 0.8, 600.0)])])
    return run_adequacy(
        ss, A.minute_load(), policy, b, A.config(), as_of=AS_OF, evidence=evidence, **kw
    )


def test_ic4_has_the_type_and_the_inventory_maps_it() -> None:
    assert EvidenceType.CRITICAL_FACILITIES_SIGNOFF.value == "critical_facilities_signoff"


def test_statement_is_canonical_bytes_naming_the_floor() -> None:
    p = _policy()
    raw = render_floor_statement(p)
    doc = json.loads(raw)
    assert doc["format"] == "gridq.resilience_floor_statement/1.0.0"
    assert doc["flat_floor_kwh"] == 400 and doc["policy_hash"] == p.content_hash()
    assert "never available to markets" in doc["declaration"]
    assert render_floor_statement(_policy()) == raw


def test_admissible_signoff_approves() -> None:
    p = _policy()
    a = admit_floor_signoff(p, [A.signoff(_hash(p))], as_of=AS_OF)
    assert isinstance(a, Approved) and a.issuer_role == "customer" and a.synthetic
    assert approval_label(a).startswith("Floor signed by customer")


def test_host_may_sign() -> None:
    p = _policy()
    ev = A.signoff(_hash(p), issuer={"name": "SYNTHETIC host", "role": "host", "reference": "X"})
    assert isinstance(admit_floor_signoff(p, [ev], as_of=AS_OF), Approved)


REASONS: dict[UnsignedReason, dict[str, Any]] = {
    UnsignedReason.WRONG_SCOPE: {"scope": {"kind": "site", "site_id": "SYNTHETIC-OTHER"}},
    UnsignedReason.WRONG_ISSUER_ROLE: {
        "issuer": {"name": "SYNTHETIC DNO", "role": "dno", "reference": "X"}
    },
    UnsignedReason.EXPIRED: {"expiry": {"kind": "expires_on", "expires_on": "2027-01-09"}},
    UnsignedReason.NOT_YET_EFFECTIVE: {"effective_from": "2027-01-11"},
    UnsignedReason.VALIDITY_UNKNOWN: {"effective_from": {"kind": "unknown", "reason": "not dated"}},
    UnsignedReason.UNVERIFIED: {"verification": {"kind": "unverified", "reason": "not checked"}},
    UnsignedReason.VERIFICATION_METHOD_INSUFFICIENT: {
        "verification": {
            "kind": "verified",
            "verifier": "SYNTHETIC verifier",
            "verifier_role": "neura_internal",
            "method": "document_inspection",
            "verified_on": "2027-01-05",
        }
    },
    UnsignedReason.VERIFIED_AFTER_AS_OF: {
        "verification": {
            "kind": "verified",
            "verifier": "SYNTHETIC verifier",
            "verifier_role": "neura_internal",
            "method": "confirmed_with_issuer",
            "verified_on": "2027-01-12",
        }
    },
}


@pytest.mark.parametrize("reason", list(REASONS), ids=lambda r: r.value)
def test_each_failure_leaves_the_floor_unsigned(reason: UnsignedReason) -> None:
    p = _policy()
    a = admit_floor_signoff(p, [A.signoff(_hash(p), **REASONS[reason])], as_of=AS_OF)
    assert isinstance(a, Unsigned) and a.reason is reason
    assert approval_label(a).startswith("FLOOR UNSIGNED: ")
    assert a.requests and a.requests[0].closes_with == (EvidenceType.CRITICAL_FACILITIES_SIGNOFF,)
    assert _hash(p) in a.requests[0].request


def test_absent() -> None:
    a = admit_floor_signoff(_policy(), [], as_of=AS_OF)
    assert isinstance(a, Unsigned) and a.reason is UnsignedReason.ABSENT


def test_other_evidence_types_are_not_candidates() -> None:
    p = _policy()
    ev = A.signoff(_hash(p), evidence_type="control_authority_signoff")
    a = admit_floor_signoff(p, [ev], as_of=AS_OF)
    assert isinstance(a, Unsigned) and a.reason is UnsignedReason.ABSENT


def test_signoff_of_a_300_kwh_floor_does_not_sign_a_400_kwh_floor() -> None:
    p300, p400 = _policy(kw=300), _policy(kw=400)
    assert p300.state.flat_floor_kwh == 300 and p400.state.flat_floor_kwh == 400  # type: ignore[union-attr]
    a = admit_floor_signoff(p400, [A.signoff(_hash(p300))], as_of=AS_OF)
    assert isinstance(a, Unsigned) and a.reason is UnsignedReason.HASH_MISMATCH


def test_changed_requirement_voids_the_signoff() -> None:
    """Same floor number, different requirement (a margin is now declared as zero kWh): the
    statement's bytes differ, so the old sign-off binds nothing."""
    old = _policy()
    new = _policy(
        margin={
            "kind": "stated_margin",
            "basis": "kwh",
            "value": {"numerator": 0, "denominator": 1},
        }
    )
    assert old.state.flat_floor_kwh == new.state.flat_floor_kwh  # type: ignore[union-attr]
    a = admit_floor_signoff(new, [A.signoff(_hash(old))], as_of=AS_OF)
    assert isinstance(a, Unsigned) and a.reason is UnsignedReason.HASH_MISMATCH


def test_synthetic_signoff_is_refused_for_a_registered_floor() -> None:
    """Built inline, never persisted: a registered-class policy exists here only to prove the
    synthetic fixture cannot sign it."""
    site = "TEST-REGISTERED-CLASS-SITE"
    load = A.MinuteLoadArray(
        site_id=site,
        record_class="registered",
        horizon_start=A.DAY0,
        facility_w=A.minute_load().facility_w,
        it_w=A.minute_load().it_w,
    )
    b = A.bess(site=site, record_class="registered")
    req = A.requirement(site=site, record_class="registered")
    p = size_floor(req, b, A.flat_partition(), load=load)
    ev = A.signoff(_hash(p), scope={"kind": "site", "site_id": site})
    a = admit_floor_signoff(p, [ev], as_of=AS_OF)
    assert isinstance(a, Unsigned) and a.reason is UnsignedReason.SYNTHETIC


def test_unsized_floor_cannot_be_signed() -> None:
    p = _policy(unknown=("duration",))
    a = admit_floor_signoff(p, [A.signoff("sha256:" + "0" * 64)], as_of=AS_OF)
    assert isinstance(a, Unsigned) and a.reason is UnsignedReason.FLOOR_UNKNOWN
    assert a.requests[0].prerequisites  # sign-off waits on the sizing asks


def test_infeasible_floor_cannot_be_signed() -> None:
    p = _policy(kw=1500)
    assert p.state.kind == "infeasible"
    a = admit_floor_signoff(p, [], as_of=AS_OF)
    assert isinstance(a, Unsigned) and a.reason is UnsignedReason.FLOOR_INFEASIBLE


def test_furthest_candidate_decides_the_reason() -> None:
    p = _policy()
    evs = [
        A.signoff(_hash(_policy(kw=300))),  # hash mismatch
        A.signoff(_hash(p), **REASONS[UnsignedReason.UNVERIFIED]),
        A.signoff(_hash(p), **REASONS[UnsignedReason.EXPIRED]),
    ]
    a = admit_floor_signoff(p, evs, as_of=AS_OF)
    assert isinstance(a, Unsigned) and a.reason is UnsignedReason.UNVERIFIED
    assert len(a.candidates) == 3
    good = admit_floor_signoff(p, [*evs, A.signoff(_hash(p))], as_of=AS_OF)
    assert isinstance(good, Approved)


def test_unsigned_run_never_reads_as_approved_and_says_what_is_missing() -> None:
    p = _policy()
    r = _run(p)
    assert isinstance(r.approval, Unsigned) and not r.approved_for_use
    text = render_summary(r)
    assert text.splitlines()[0].startswith("FLOOR UNSIGNED: ")
    assert not FORBIDDEN.search(text)
    assert '"approved"' not in r.model_dump_json()
    assert "Missing: Critical-facilities sign-off" in text and _hash(p) in text


def test_signing_changes_the_label_not_the_physics() -> None:
    p = _policy()
    unsigned, signed = _run(p), _run(p, [A.signoff(_hash(p))])
    assert signed.approved_for_use and isinstance(signed.approval, Approved)
    assert unsigned.simulation_hash == signed.simulation_hash
    assert unsigned.outcome == signed.outcome
    assert unsigned.content_hash() != signed.content_hash()


def test_forged_approval_is_refused() -> None:
    p = _policy()
    unsigned = _run(p)
    forged = admit_floor_signoff(_policy(kw=300), [A.signoff(_hash(_policy(kw=300)))], as_of=AS_OF)
    assert isinstance(forged, Approved)
    fields = {k: getattr(unsigned, k) for k in AdequacyResult.model_fields}
    fields.update(approval=forged, approval_label=approval_label(forged), approved_for_use=True)
    with pytest.raises(ValidationError, match="sign-off bar"):
        AdequacyResult(**fields)
    # Nor can an unsigned record claim to be approved for use.
    fields = {k: getattr(unsigned, k) for k in AdequacyResult.model_fields}
    fields.update(approved_for_use=True)
    with pytest.raises(ValidationError, match="derived"):
        AdequacyResult(**fields)
    fields = {k: getattr(unsigned, k) for k in AdequacyResult.model_fields}
    fields.update(approval_label="Floor signed by customer")
    with pytest.raises(ValidationError, match="closed table"):
        AdequacyResult(**fields)


def test_signed_floor_with_nonconforming_envelope_is_not_for_use() -> None:
    """The policy floor (527 kWh at 19/20) is above the envelope's 400 kWh: a finding, so even a
    signed floor does not make the run approved for use."""
    b = A.bess(initial=1500, operating=1500)
    p = size_floor(A.requirement(kw=500), b, A.flat_partition(), load=A.minute_load())
    ss = A.scenario_set([("a", Fraction(1), [(600, 120, 0.8, 600.0)])])
    r = run_adequacy(
        ss,
        A.minute_load(),
        p,
        b,
        A.config(),
        as_of=AS_OF,
        evidence=[A.signoff(_hash(p))],
        envelope=A.envelope(),
    )
    assert isinstance(r.approval, Approved)
    assert r.conformance.status == "nonconforming"  # type: ignore[union-attr]
    codes = {f.code for f in r.conformance.findings}  # type: ignore[union-attr]
    assert "ENVELOPE_FLOOR_BELOW_POLICY" in codes
    assert "ENVELOPE_RESILIENCE_KW_BELOW_PROTECTED_LOAD" in codes
    assert not r.approved_for_use
    assert "Not for use" in render_summary(r)
