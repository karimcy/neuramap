"""IC-13 fixture builder and TEST-ONLY token signer (tests/contracts/; never imported by src/).

Signing lives here, not in `gridq.contracts.identity`: the contract only *verifies*. The key is
the committed fixture ``fixtures/ic13_TEST_ONLY_signing_key.jwk.json`` (kid ``TEST-ONLY-...``),
which `validate_token` refuses unless ``allow_fixture_keys=True``.

RS256 signatures are deterministic, so the committed tokens and audit log are reproducible from
the key and the claims below; ``test_ic13_*`` asserts that the committed files equal a fresh
build. Regenerate after a deliberate change with::

    python tests/contracts/_ic13_fixtures.py
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gridq.contracts._base import canonical_json, not_applicable
from gridq.contracts.audit import (
    AuditAction,
    AuditEvent,
    ChainHead,
    ControlCommand,
    audit_access,
)
from gridq.contracts.identity import IdentityClaims, ResourceKind, ResourceRef, b64url_decode
from gridq.contracts.identity import b64url_encode as _b64

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SIGNING_KEY = FIXTURES / "ic13_TEST_ONLY_signing_key.jwk.json"
JWKS = FIXTURES / "ic13_TEST_ONLY_jwks.json"
TOKENS = FIXTURES / "ic13_tokens.fixture.json"
AUDIT_LOG = FIXTURES / "ic13_audit_log.synthetic.jsonl"

ISSUER = "https://identity.invalid/ic13-test-fixture"
AUDIENCE = "gridq-api-ic13-fixture"
ISSUED_AT = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)
VALID_AT = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
LIFETIME = timedelta(hours=8)

NEURA = "SYNTHETIC-ORG-NEURA"
CUST = "SYNTHETIC-ORG-CUSTOMER-A"
OTHER_CUST = "SYNTHETIC-ORG-CUSTOMER-B"
LENDER = "SYNTHETIC-ORG-LENDER-A"
VERIFIER = "SYNTHETIC-ORG-VERIFIER-A"
SITE = "SYNTHETIC-SITE-A1"

_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _scope(resource: str, org: str, rid: str, *actions: str) -> dict[str, Any]:
    return {"resource": resource, "org_id": org, "resource_id": rid, "actions": sorted(actions)}


def _claims(role: str, org: str, kind: str, n: int, scopes: list[dict[str, Any]], amr: list[str]):
    iat = int(ISSUED_AT.timestamp())
    return {
        "contract": "IC-13",
        "contract_version": "1.0.0",
        "iss": ISSUER,
        "sub": f"SYNTHETIC-USER-{n:04d}",
        "aud": AUDIENCE,
        "iat": iat,
        "nbf": iat,
        "exp": iat + int(LIFETIME.total_seconds()),
        "jti": f"SYNTHETIC-TOKEN-{role}",
        "org": {"org_id": org, "kind": kind},
        "role": role,
        "scopes": sorted(scopes, key=lambda s: (s["resource"], s["org_id"], s["resource_id"])),
        "amr": sorted(amr),
    }


#: One claims payload per spec E-04 role.
CLAIMS: dict[str, dict[str, Any]] = {
    "neura_ops": _claims(
        "neura_ops",
        NEURA,
        "neura",
        1,
        [
            _scope("device", "*", "*", "control_write", "read"),
            _scope("site", "*", "*", "read", "write"),
            _scope("telemetry", "*", "*", "read"),
        ],
        ["mfa", "otp", "pwd"],
    ),
    "customer": _claims(
        "customer",
        CUST,
        "customer",
        2,
        [
            _scope("customer_data", CUST, "*", "read", "write"),
            _scope("site", CUST, SITE, "read", "write"),
            _scope("telemetry", CUST, "*", "read"),
        ],
        ["mfa", "otp", "pwd"],
    ),
    "lender_readonly": _claims(
        "lender_readonly",
        LENDER,
        "lender",
        3,
        [
            _scope("ledger", CUST, "*", "read"),
            _scope("pack", CUST, "*", "read"),
            _scope("site", CUST, SITE, "read"),
        ],
        ["pwd"],
    ),
    "verifier": _claims(
        "verifier",
        VERIFIER,
        "verifier",
        4,
        [
            _scope("ledger", CUST, "*", "read"),
            _scope("method", NEURA, "*", "read"),
            _scope("telemetry", CUST, SITE, "read"),
        ],
        ["mfa", "otp", "pwd"],
    ),
}


def claims_fixture_path(role: str) -> Path:
    return FIXTURES / f"ic13_claims_{role}.synthetic.json"


# ----------------------------------------------------------------------------- TEST-ONLY signer


def _key() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SIGNING_KEY.read_text(encoding="utf-8"))
    assert data["kid"].startswith("TEST-ONLY-"), "the signer only ever uses the fixture key"
    return data


def _int(text: str) -> int:
    return int.from_bytes(b64url_decode(text), "big")


def sign_raw(header: dict[str, Any], payload: bytes) -> str:
    """RS256-sign ``payload`` under ``header`` with the fixture key (RFC 8017 §8.2.1)."""
    k = _key()
    n, d = _int(k["n"]), _int(k["d"])
    size = (n.bit_length() + 7) // 8
    h = _b64(json.dumps(header, sort_keys=True, separators=(",", ":")).encode())
    signing_input = f"{h}.{_b64(payload)}".encode("ascii")
    t = _SHA256_DIGEST_INFO + hashlib.sha256(signing_input).digest()
    em = b"\x00\x01" + b"\xff" * (size - len(t) - 3) + b"\x00" + t
    sig = pow(int.from_bytes(em, "big"), d, n).to_bytes(size, "big")
    return f"{signing_input.decode()}.{_b64(sig)}"


def sign(payload: dict[str, Any], *, kid: str | None = None) -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": kid or _key()["kid"]}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sign_raw(header, body)


# ----------------------------------------------------------------------------- build


def build_tokens() -> dict[str, Any]:
    return {
        "_note": (
            "TEST FIXTURE. Tokens signed with the TEST-ONLY fixture key; validate with "
            "allow_fixture_keys=True, issuer, audience and valid_at below. Not real credentials."
        ),
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "valid_at": VALID_AT.isoformat().replace("+00:00", "Z"),
        "jwks_file": JWKS.name,
        "tokens": {role: sign(c) for role, c in sorted(CLAIMS.items())},
    }


def _claims_model(role: str) -> IdentityClaims:
    return IdentityClaims.model_validate_json(json.dumps(CLAIMS[role]))


def build_audit_log() -> list[AuditEvent]:
    """Six events: allowed reads by lender, verifier and customer; an allowed control write by
    Neura ops; a lender's control write and a customer's cross-organisation read, both denied."""
    na = not_applicable("not a control write")
    plan: list[tuple[str, AuditAction, ResourceRef, ControlCommand | Any]] = [
        (
            "lender_readonly",
            AuditAction.CUSTOMER_DATA_READ,
            ResourceRef(kind=ResourceKind.SITE, org_id=CUST, resource_id=SITE),
            na,
        ),
        (
            "verifier",
            AuditAction.CUSTOMER_DATA_READ,
            ResourceRef(
                kind=ResourceKind.LEDGER, org_id=CUST, resource_id="SYNTHETIC-LEDGER-2026-10"
            ),
            na,
        ),
        (
            "neura_ops",
            AuditAction.CONTROL_WRITE,
            ResourceRef(kind=ResourceKind.DEVICE, org_id=CUST, resource_id="SYNTHETIC-BESS-01"),
            ControlCommand(
                kind="control_command",
                command_id="SYNTHETIC-CMD-0001",
                point="import_limit_setpoint",
                value=4.5,
                unit="MW",
            ),
        ),
        (
            "lender_readonly",
            AuditAction.CONTROL_WRITE,
            ResourceRef(kind=ResourceKind.DEVICE, org_id=CUST, resource_id="SYNTHETIC-BESS-01"),
            ControlCommand(
                kind="control_command",
                command_id="SYNTHETIC-CMD-0002",
                point="import_limit_setpoint",
                value=0.0,
                unit="MW",
            ),
        ),
        (
            "customer",
            AuditAction.CUSTOMER_DATA_READ,
            ResourceRef(kind=ResourceKind.TELEMETRY, org_id=CUST, resource_id=SITE),
            na,
        ),
        (
            "customer",
            AuditAction.CUSTOMER_DATA_READ,
            ResourceRef(kind=ResourceKind.SITE, org_id=OTHER_CUST, resource_id="SYNTHETIC-SITE-B1"),
            na,
        ),
    ]
    head: ChainHead | None = None
    out: list[AuditEvent] = []
    for i, (role, action, target, control) in enumerate(plan):
        e = audit_access(
            head,
            _claims_model(role),
            event_id=f"SYNTHETIC-EVT-{i:04d}",
            occurred_at=VALID_AT + timedelta(minutes=i),
            action=action,
            target=target,
            control=control,
        )
        out.append(e)
        head = ChainHead(seq=e.seq, event_hash=e.content_hash())
    return out


def render_audit_log(events: list[AuditEvent]) -> str:
    """JSON Lines, one canonical event per line: the append-only on-disk form."""
    return "".join(canonical_json(e) + "\n" for e in events)


def render_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    for role, c in CLAIMS.items():
        claims_fixture_path(role).write_text(render_json(c), encoding="utf-8")
    TOKENS.write_text(render_json(build_tokens()), encoding="utf-8")
    AUDIT_LOG.write_text(render_audit_log(build_audit_log()), encoding="utf-8")


if __name__ == "__main__":
    main()
