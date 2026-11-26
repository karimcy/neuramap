"""IC-13 (roadmap NEW-IC-13) identity claims and audit events v1.0.0 conformance (WP-PLAT-05).

Acceptance:
1. Contract version is tagged and conformance tests pass.
2. Roles cover Neura ops, customer, lender read-only and verifier.
3. A consumer can validate a token and emit an audit event using fixtures only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import types
import typing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import _ic13_fixtures as fx
import pytest
from _contracts_support import IC13_AUDIT_HEAD, IC13_AUDIT_LOG, IC13_CLAIMS_FIXTURES, load
from pydantic import ValidationError

from gridq.contracts import audit, identity
from gridq.contracts._base import ContractModel, canonical_json
from gridq.contracts.audit import (
    GENESIS_PREV_HASH,
    AuditAction,
    AuditEvent,
    ChainBroken,
    ChainHead,
    ControlCommand,
    audit_access,
    verify_chain,
)
from gridq.contracts.identity import (
    CONTRACT_VERSION,
    GIT_TAG,
    MAX_TOKEN_LIFETIME_S,
    ROLE_ACTIONS,
    Action,
    IdentityClaims,
    Jwk,
    Jwks,
    ResourceKind,
    ResourceRef,
    Role,
    TokenInvalid,
    authorise,
    b64url_encode,
    validate_token,
)

SRC = Path(__file__).resolve().parents[2] / "src"
TOKENS = json.loads(fx.TOKENS.read_text(encoding="utf-8"))
JWKS = Jwks.model_validate_json(fx.JWKS.read_text(encoding="utf-8"))
AT = fx.VALID_AT


def _validate(token: str, **kw: Any) -> IdentityClaims:
    args: dict[str, Any] = {
        "issuer": fx.ISSUER,
        "audience": fx.AUDIENCE,
        "at": AT,
        "allow_fixture_keys": True,
    }
    args.update(kw)
    return validate_token(token, JWKS, **args)


def _claims(role: str) -> dict[str, Any]:
    return json.loads(json.dumps(fx.CLAIMS[role]))


def _model(data: dict[str, Any]) -> IdentityClaims:
    return IdentityClaims.model_validate_json(json.dumps(data))


def _log() -> list[AuditEvent]:
    return [
        AuditEvent.model_validate_json(line) for line in IC13_AUDIT_LOG.read_text().splitlines()
    ]


def _code(exc: pytest.ExceptionInfo[TokenInvalid]) -> str:
    return exc.value.code


# ---------------------------------------------------------------- 1. version tagged, fixtures pass


def test_contract_version_is_tagged_in_both_modules_and_on_every_instance() -> None:
    assert CONTRACT_VERSION == "1.0.0" == audit.CONTRACT_VERSION
    assert GIT_TAG == f"contracts/ic13-identity-audit/v{CONTRACT_VERSION}" == audit.GIT_TAG
    for path in IC13_CLAIMS_FIXTURES:
        c = _model(load(path.name))
        assert (c.contract, c.contract_version) == ("IC-13", CONTRACT_VERSION)
    for e in _log():
        assert (e.contract, e.contract_version) == ("IC-13", CONTRACT_VERSION)


@pytest.mark.parametrize("field,value", [("contract_version", "1.1.0"), ("contract", "IC-4")])
def test_claims_of_another_contract_or_version_are_refused(field: str, value: str) -> None:
    data = _claims("customer")
    data[field] = value
    with pytest.raises(ValidationError):
        _model(data)
    with pytest.raises(TokenInvalid) as exc:
        _validate(fx.sign(data))
    assert _code(exc) == "bad_claims"


def test_committed_fixtures_equal_a_fresh_build() -> None:
    for role, c in fx.CLAIMS.items():
        assert fx.claims_fixture_path(role).read_text(encoding="utf-8") == fx.render_json(c)
    assert fx.TOKENS.read_text(encoding="utf-8") == fx.render_json(fx.build_tokens())
    assert IC13_AUDIT_LOG.read_text(encoding="utf-8") == fx.render_audit_log(fx.build_audit_log())


def _allows_none(annotation: Any) -> bool:
    if annotation is None or annotation is type(None):
        return True
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _allows_none(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        return any(_allows_none(a) for a in typing.get_args(annotation))
    return origin is typing.Literal and None in typing.get_args(annotation)


def test_no_field_of_any_ic13_model_admits_none() -> None:
    n = 0
    for mod in (identity, audit):
        for o in vars(mod).values():
            if isinstance(o, type) and issubclass(o, ContractModel) and o is not ContractModel:
                for name, f in o.model_fields.items():
                    assert not _allows_none(f.annotation), f"{o.__name__}.{name}"
                n += 1
    assert n >= 10


@pytest.mark.parametrize("field", sorted(IdentityClaims.model_fields))
def test_null_is_refused_for_every_claim(field: str) -> None:
    data = _claims("customer")
    data[field] = None
    with pytest.raises(ValidationError):
        _model(data)


def test_claims_carry_no_personal_data_fields() -> None:
    assert not {"email", "name", "given_name", "family_name", "phone_number"} & set(
        IdentityClaims.model_fields
    )
    data = _claims("customer")
    data["email"] = "someone@example.invalid"
    with pytest.raises(ValidationError, match="Extra inputs"):
        _model(data)


def test_final_and_immutable() -> None:
    with pytest.raises(TypeError, match="final"):

        class Wider(IdentityClaims):  # type: ignore[misc]
            pass

    with pytest.raises(TypeError, match="final"):

        class Looser(AuditEvent):  # type: ignore[misc]
            pass

    c = _model(_claims("customer"))
    with pytest.raises(ValidationError):
        c.role = Role.NEURA_OPS  # type: ignore[misc]


# ---------------------------------------------------------------- 2. roles


def test_roles_are_exactly_the_four_spec_e04_roles_each_with_a_fixture_and_token() -> None:
    assert {r.value for r in Role} == {"neura_ops", "customer", "lender_readonly", "verifier"}
    assert {p.name for p in IC13_CLAIMS_FIXTURES} == {
        f"ic13_claims_{r.value}.synthetic.json" for r in Role
    }
    assert set(TOKENS["tokens"]) == {r.value for r in Role}
    for role in Role:
        assert _validate(TOKENS["tokens"][role.value]).role is role


def test_lender_and_verifier_are_read_only_and_only_ops_control() -> None:
    assert ROLE_ACTIONS[Role.LENDER_READONLY] == ROLE_ACTIONS[Role.VERIFIER] == {Action.READ}
    assert Action.CONTROL_WRITE not in ROLE_ACTIONS[Role.CUSTOMER]
    assert [r for r in Role if Action.CONTROL_WRITE in ROLE_ACTIONS[r]] == [Role.NEURA_OPS]


@pytest.mark.parametrize(
    "role,scope,match",
    [
        ("lender_readonly", ("site", fx.CUST, fx.SITE, ["read", "write"]), "may not hold"),
        ("verifier", ("ledger", fx.CUST, "*", ["read", "write"]), "may not hold"),
        ("customer", ("device", fx.CUST, "*", ["control_write"]), "may not hold"),
        ("customer", ("site", fx.OTHER_CUST, "*", ["read"]), "own organisation"),
        ("lender_readonly", ("site", "*", "*", ["read"]), "wildcard organisation"),
        ("neura_ops", ("site", fx.CUST, fx.SITE, ["control_write"]), "only to a device"),
        ("neura_ops", ("device", fx.CUST, "*", ["write"]), "control_write"),
        ("neura_ops", ("site", "*", fx.SITE, ["read"]), "wildcard resource_id"),
        ("neura_ops", ("site", fx.CUST, "*", ["write", "read"]), "sorted and unique"),
    ],
)
def test_scopes_a_role_may_not_hold_are_refused(
    role: str, scope: tuple[str, str, str, list[str]], match: str
) -> None:
    data = _claims(role)
    r, o, i, a = scope
    data["scopes"] = [{"resource": r, "org_id": o, "resource_id": i, "actions": a}]
    with pytest.raises(ValidationError, match=match):
        _model(data)
    # and a token carrying it is refused even when correctly signed
    with pytest.raises(TokenInvalid) as exc:
        _validate(fx.sign(data))
    assert _code(exc) == "bad_claims"


def test_role_must_match_the_organisation_kind() -> None:
    data = _claims("lender_readonly")
    data["role"] = "neura_ops"
    with pytest.raises(ValidationError, match="belongs to a neura organisation"):
        _model(data)


def test_control_write_requires_mfa() -> None:
    data = _claims("neura_ops")
    data["amr"] = ["pwd"]
    with pytest.raises(ValidationError, match="multi-factor"):
        _model(data)


def test_token_times_and_lifetime() -> None:
    data = _claims("customer")
    data["exp"] = data["iat"] + MAX_TOKEN_LIFETIME_S + 1
    with pytest.raises(ValidationError, match="lifetime"):
        _model(data)
    data = _claims("customer")
    data["nbf"] = data["exp"]
    with pytest.raises(ValidationError, match="iat <= nbf < exp"):
        _model(data)


def test_scopes_must_be_in_canonical_order() -> None:
    data = _claims("customer")
    data["scopes"].reverse()
    with pytest.raises(ValidationError, match="sorted"):
        _model(data)


def test_authorise_by_role() -> None:
    site = ResourceRef(kind=ResourceKind.SITE, org_id=fx.CUST, resource_id=fx.SITE)
    other = ResourceRef(kind=ResourceKind.SITE, org_id=fx.OTHER_CUST, resource_id="S")
    bess = ResourceRef(kind=ResourceKind.DEVICE, org_id=fx.CUST, resource_id="B")
    c = {r: _model(_claims(r.value)) for r in Role}
    assert authorise(c[Role.LENDER_READONLY], Action.READ, site) is not None
    assert authorise(c[Role.LENDER_READONLY], Action.WRITE, site) is None
    assert authorise(c[Role.CUSTOMER], Action.WRITE, site) is not None
    assert authorise(c[Role.CUSTOMER], Action.READ, other) is None
    assert authorise(c[Role.NEURA_OPS], Action.CONTROL_WRITE, bess) is not None
    for r in (Role.CUSTOMER, Role.LENDER_READONLY, Role.VERIFIER):
        assert authorise(c[r], Action.CONTROL_WRITE, bess) is None


# ---------------------------------------------------------------- token validation


def test_every_fixture_token_validates_offline_with_fixture_keys_allowed() -> None:
    for role, token in TOKENS["tokens"].items():
        c = _validate(token)
        assert c == _model(fx.CLAIMS[role])
        assert c.content_hash() == _model(load(f"ic13_claims_{role}.synthetic.json")).content_hash()


def test_fixture_key_is_refused_by_default() -> None:
    assert all(k.is_fixture for k in JWKS.keys)
    with pytest.raises(TokenInvalid) as exc:
        _validate(TOKENS["tokens"]["customer"], allow_fixture_keys=False)
    assert _code(exc) == "fixture_key"


def test_time_window_is_nbf_inclusive_exp_exclusive() -> None:
    tok = TOKENS["tokens"]["customer"]
    c = _validate(tok)
    nbf = datetime.fromtimestamp(c.nbf, UTC)
    exp = datetime.fromtimestamp(c.exp, UTC)
    assert _validate(tok, at=nbf).jti == c.jti
    with pytest.raises(TokenInvalid) as exc:
        _validate(tok, at=nbf - timedelta(seconds=1))
    assert _code(exc) == "not_yet_valid"
    with pytest.raises(TokenInvalid) as exc:
        _validate(tok, at=exp)
    assert _code(exc) == "expired"
    assert _validate(tok, at=exp, leeway_s=60).jti == c.jti
    with pytest.raises(ValueError, match="timezone-aware"):
        _validate(tok, at=datetime(2026, 11, 17, 9))


def test_wrong_issuer_or_audience_is_refused() -> None:
    tok = TOKENS["tokens"]["verifier"]
    with pytest.raises(TokenInvalid) as exc:
        _validate(tok, issuer="https://elsewhere.invalid")
    assert _code(exc) == "wrong_issuer"
    with pytest.raises(TokenInvalid) as exc:
        _validate(tok, audience="another-api")
    assert _code(exc) == "wrong_audience"


def test_a_tampered_payload_fails_the_signature() -> None:
    h, p, s = TOKENS["tokens"]["lender_readonly"].split(".")
    data = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    data["role"] = "neura_ops"
    forged = f"{h}.{b64url_encode(json.dumps(data).encode())}.{s}"
    with pytest.raises(TokenInvalid) as exc:
        _validate(forged)
    assert _code(exc) == "bad_signature"


def test_alg_none_and_hs256_key_confusion_are_refused() -> None:
    payload = b64url_encode(json.dumps(fx.CLAIMS["neura_ops"]).encode())
    kid = JWKS.keys[0].kid
    none_h = b64url_encode(json.dumps({"alg": "none", "typ": "JWT", "kid": kid}).encode())
    with pytest.raises(TokenInvalid) as exc:
        _validate(f"{none_h}.{payload}.")
    assert _code(exc) == "bad_header"
    # HS256 "signed" with the public key as the HMAC secret: the classic confusion attack
    hs_h = b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    secret = fx.JWKS.read_bytes()
    mac = hmac.new(secret, f"{hs_h}.{payload}".encode(), hashlib.sha256).digest()
    with pytest.raises(TokenInvalid) as exc:
        _validate(f"{hs_h}.{payload}.{b64url_encode(mac)}")
    assert _code(exc) == "bad_header"


def test_unknown_kid_extra_header_and_duplicate_keys_are_refused() -> None:
    with pytest.raises(TokenInvalid) as exc:
        _validate(fx.sign(fx.CLAIMS["customer"], kid="TEST-ONLY-other"))
    assert _code(exc) == "unknown_key"
    header = {"alg": "RS256", "typ": "JWT", "kid": JWKS.keys[0].kid, "jku": "https://x.invalid"}
    with pytest.raises(TokenInvalid) as exc:
        _validate(fx.sign_raw(header, json.dumps(fx.CLAIMS["customer"]).encode()))
    assert _code(exc) == "bad_header"
    body = json.dumps(fx.CLAIMS["lender_readonly"])[:-1] + ', "role": "neura_ops"}'
    header = {"alg": "RS256", "typ": "JWT", "kid": JWKS.keys[0].kid}
    with pytest.raises(TokenInvalid) as exc:
        _validate(fx.sign_raw(header, body.encode()))
    assert _code(exc) == "malformed"


@pytest.mark.parametrize(
    "mangle",
    [
        lambda t: t.rsplit(".", 1)[0],  # two parts
        lambda t: t + "=",  # padding
        lambda t: t.replace(".", ".+", 1),  # non-base64url character
        lambda t: t[:-4],  # short signature
    ],
)
def test_malformed_tokens_are_refused(mangle: Any) -> None:
    with pytest.raises(TokenInvalid) as exc:
        _validate(mangle(TOKENS["tokens"]["customer"]))
    assert _code(exc) in {"malformed", "bad_signature"}


def test_weak_keys_and_private_members_are_refused() -> None:
    pub = JWKS.keys[0].model_dump()
    with pytest.raises(ValidationError, match="65537"):
        Jwk.model_validate_json(json.dumps({**pub, "e": b64url_encode(b"\x03")}))
    short = b64url_encode((2**1023 + 1).to_bytes(128, "big"))
    with pytest.raises(ValidationError, match="2048"):
        Jwk.model_validate_json(json.dumps({**pub, "n": short}))
    private = json.loads(fx.SIGNING_KEY.read_text(encoding="utf-8"))
    with pytest.raises(ValidationError, match="Extra inputs"):
        Jwk.model_validate_json(json.dumps(private))


def test_fixture_key_files_are_marked_test_only() -> None:
    private = json.loads(fx.SIGNING_KEY.read_text(encoding="utf-8"))
    assert private["kid"].startswith(identity.FIXTURE_KID_PREFIX)
    assert "TEST-ONLY" in private["_WARNING"] and "never" in private["_WARNING"]
    assert "TEST_ONLY" in fx.SIGNING_KEY.name and "TEST_ONLY" in fx.JWKS.name


def test_interoperates_with_the_cryptography_library() -> None:
    """Standard RS256 both ways: our tokens verify under `cryptography`, and its tokens under us."""
    crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    n, e = JWKS.keys[0].numbers()
    pub = crypto.RSAPublicNumbers(e, n).public_key()
    h, p, s = TOKENS["tokens"]["neura_ops"].split(".")
    sig = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    pub.verify(sig, f"{h}.{p}".encode(), padding.PKCS1v15(), hashes.SHA256())

    k = json.loads(fx.SIGNING_KEY.read_text(encoding="utf-8"))

    def i(x: str) -> int:
        return int.from_bytes(identity.b64url_decode(x), "big")

    priv = crypto.RSAPrivateNumbers(
        i(k["p"]),
        i(k["q"]),
        i(k["d"]),
        i(k["dp"]),
        i(k["dq"]),
        i(k["qi"]),
        crypto.RSAPublicNumbers(e, n),
    ).private_key()
    hdr = b64url_encode(json.dumps({"alg": "RS256", "typ": "JWT", "kid": k["kid"]}).encode())
    body = b64url_encode(json.dumps(fx.CLAIMS["verifier"]).encode())
    their_sig = priv.sign(f"{hdr}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    assert _validate(f"{hdr}.{body}.{b64url_encode(their_sig)}").role is Role.VERIFIER


# ---------------------------------------------------------------- audit events


def test_committed_log_is_a_valid_chain_with_the_pinned_head() -> None:
    events = _log()
    head = verify_chain(events)
    assert head == ChainHead(seq=5, event_hash=IC13_AUDIT_HEAD)
    assert events[0].prev_hash == GENESIS_PREV_HASH
    for line, e in zip(IC13_AUDIT_LOG.read_text().splitlines(), events, strict=True):
        assert line == canonical_json(e)  # each line is the canonical form: hash-stable


def test_the_log_covers_reads_and_control_writes_allowed_and_denied() -> None:
    kinds = {(e.action, e.outcome, e.actor.role) for e in _log()}
    assert (AuditAction.CONTROL_WRITE, "allowed", Role.NEURA_OPS) in kinds
    assert (AuditAction.CONTROL_WRITE, "denied", Role.LENDER_READONLY) in kinds
    assert (AuditAction.CUSTOMER_DATA_READ, "allowed", Role.VERIFIER) in kinds
    assert (AuditAction.CUSTOMER_DATA_READ, "denied", Role.CUSTOMER) in kinds


def test_every_event_carries_actor_scope_target_action_time_and_prev_hash() -> None:
    f = AuditEvent.model_fields
    for name in ("actor", "scope", "target", "action", "occurred_at", "prev_hash", "seq"):
        assert f[name].is_required(), name


def _lines() -> list[dict[str, Any]]:
    return [json.loads(x) for x in IC13_AUDIT_LOG.read_text().splitlines()]


def _parse(rows: list[dict[str, Any]]) -> list[AuditEvent]:
    return [AuditEvent.model_validate_json(json.dumps(r)) for r in rows]


def test_editing_an_event_breaks_the_next_link() -> None:
    rows = _lines()
    rows[2]["control"]["value"] = 9.9  # quietly change the setpoint that was written
    with pytest.raises(ChainBroken) as exc:
        verify_chain(_parse(rows))
    assert exc.value.seq == 3


@pytest.mark.parametrize("op", ["delete", "reorder", "duplicate"])
def test_deleting_reordering_or_replaying_events_is_detected(op: str) -> None:
    rows = _lines()
    if op == "delete":
        del rows[1]
    elif op == "reorder":
        rows[1], rows[2] = rows[2], rows[1]
    else:
        rows.insert(2, rows[1])
    with pytest.raises(ChainBroken):
        verify_chain(_parse(rows))


def test_truncation_is_detected_only_against_an_anchored_head() -> None:
    head = verify_chain(_log()[:4])  # internally consistent ...
    assert head.event_hash != IC13_AUDIT_HEAD  # ... but not the anchored head


def test_time_cannot_go_backwards() -> None:
    events = _log()
    head = verify_chain(events)
    claims = _model(fx.CLAIMS["customer"])
    target = ResourceRef(kind=ResourceKind.TELEMETRY, org_id=fx.CUST, resource_id=fx.SITE)
    late = audit_access(
        head,
        claims,
        event_id="X-1",
        occurred_at=AT - timedelta(days=1),
        action=AuditAction.CUSTOMER_DATA_READ,
        target=target,
        control=identity_na(),
    )
    with pytest.raises(ChainBroken, match="backwards"):
        verify_chain([*events, late])


def identity_na() -> Any:
    from gridq.contracts._base import not_applicable

    return not_applicable("not a control write")


@pytest.mark.parametrize(
    "edit,match",
    [
        (lambda r: r.update(seq=0), "genesis"),
        (lambda r: r.update(prev_hash=GENESIS_PREV_HASH), "genesis"),
        (lambda r: r["actor"].update(role="lender_readonly", org_kind="lender"), "only neura_ops"),
        (lambda r: r.update(scope={"kind": "not_applicable", "reason": "x"}), "names the scope"),
        (lambda r: r.update(control={"kind": "not_applicable", "reason": "x"}), "ControlCommand"),
        (lambda r: r["target"].update(kind="site"), "targets a device"),
        (lambda r: r.update(outcome="denied"), "not_applicable"),
    ],
)
def test_event_level_rules(edit: Any, match: str) -> None:
    row = _lines()[2]  # the allowed control write
    edit(row)
    with pytest.raises(ValidationError, match=match):
        AuditEvent.model_validate_json(json.dumps(row))


def test_an_allowed_event_scope_must_cover_its_target() -> None:
    row = _lines()[0]  # the lender's allowed read of one named site
    row["target"]["resource_id"] = "SYNTHETIC-SITE-A2"
    with pytest.raises(ValidationError, match="does not cover"):
        AuditEvent.model_validate_json(json.dumps(row))


def test_a_denied_event_states_its_reason_and_a_read_carries_no_command() -> None:
    row = _lines()[3]  # the lender's denied control write
    row["reason"] = {"kind": "not_applicable", "reason": "x"}
    with pytest.raises(ValidationError, match="states its reason"):
        AuditEvent.model_validate_json(json.dumps(row))
    row = _lines()[0]
    row["control"] = {
        "kind": "control_command",
        "command_id": "C",
        "point": "p",
        "value": 1.0,
        "unit": "MW",
    }
    with pytest.raises(ValidationError, match="only a control write"):
        AuditEvent.model_validate_json(json.dumps(row))


def test_audit_access_denies_a_control_write_by_a_non_ops_role_even_with_a_device_scope() -> None:
    # A customer cannot hold a control_write scope at all; the guard in audit_access is
    # belt-and-braces for a future role. Here: a lender gets a denied event, not an exception.
    claims = _model(fx.CLAIMS["lender_readonly"])
    cmd = ControlCommand(kind="control_command", command_id="C-9", point="p", value=1.0, unit="MW")
    e = audit_access(
        None,
        claims,
        event_id="X-2",
        occurred_at=AT,
        action=AuditAction.CONTROL_WRITE,
        target=ResourceRef(kind=ResourceKind.DEVICE, org_id=fx.CUST, resource_id="B"),
        control=cmd,
    )
    assert e.outcome == "denied" and e.seq == 0 and e.prev_hash == GENESIS_PREV_HASH


# ---------------------------------------------------------------- 3. consumer, fixtures only

_CONSUMER = """
import json, pathlib, socket, sys
from datetime import datetime, timedelta

class _NoNetwork(socket.socket):
    def __init__(self, *a, **k):
        raise RuntimeError("the consumer tried to use the network")
socket.socket = _NoNetwork

from gridq.contracts._base import not_applicable
from gridq.contracts.audit import AuditAction, AuditEvent, audit_access, verify_chain
from gridq.contracts.identity import Jwks, ResourceKind, ResourceRef, validate_token

fixtures = pathlib.Path(sys.argv[1])
bundle = json.loads((fixtures / "ic13_tokens.fixture.json").read_text())
jwks = Jwks.model_validate_json((fixtures / bundle["jwks_file"]).read_text())
at = datetime.fromisoformat(bundle["valid_at"].replace("Z", "+00:00"))

claims = validate_token(bundle["tokens"]["lender_readonly"], jwks, issuer=bundle["issuer"],
                        audience=bundle["audience"], at=at, allow_fixture_keys=True)
log = [AuditEvent.model_validate_json(l)
       for l in (fixtures / "ic13_audit_log.synthetic.jsonl").read_text().splitlines()]
head = verify_chain(log)
event = audit_access(head, claims, event_id="CONSUMER-EVT-1", occurred_at=at + timedelta(hours=1),
                     action=AuditAction.CUSTOMER_DATA_READ,
                     target=ResourceRef(kind=ResourceKind.PACK, org_id="SYNTHETIC-ORG-CUSTOMER-A",
                                        resource_id="SYNTHETIC-PACK-1"),
                     control=not_applicable("not a control write"))
new_head = verify_chain([event], after=head)
engine = sorted(
    m for m in sys.modules if m.startswith("gridq.") and not m.startswith("gridq.contracts")
)
third = sorted(m for m in ("cryptography", "jwt", "joserfc", "authlib", "numpy", "pandas")
               if m in sys.modules)
print(json.dumps({"role": claims.role.value, "outcome": event.outcome, "seq": event.seq,
                  "prev": event.prev_hash, "head": new_head.event_hash,
                  "line": event.model_dump_json(),
                  "engine": engine, "third": third}))
"""


def test_a_consumer_validates_a_token_and_emits_an_audit_event_from_fixtures_only() -> None:
    """Fresh interpreter, sockets disabled, contract package only (no crypto library)."""
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    res = subprocess.run(
        [sys.executable, "-c", _CONSUMER, str(fx.FIXTURES)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    out = json.loads(res.stdout)
    assert out["engine"] == [] and out["third"] == [], out
    assert (out["role"], out["outcome"], out["seq"]) == ("lender_readonly", "allowed", 6)
    assert out["prev"] == IC13_AUDIT_HEAD
    event = AuditEvent.model_validate_json(out["line"])
    assert verify_chain([*_log(), event]).event_hash == out["head"]
