"""IC-13 identity claims: frozen contract, version 1.0.0 (roadmap WP-PLAT-05, spec E-04).

The roadmap calls this NEW-IC-13: spec §10A has no identity contract, yet every UI and service
consumes identity and writes audit events. This module freezes the **claims** a Neura access token
carries (organisation, user, role, scopes) and how a consumer **validates** such a token offline.
`gridq.contracts.audit` freezes the audit event; both share this version and git tag.

**Token format.** A JWT (RFC 7519) in JWS compact serialisation (RFC 7515), signed **RS256**
(RSASSA-PKCS1-v1_5 with SHA-256, RFC 7518 §3.3), keys published as a JWK Set (RFC 7517).
Verification here uses only the standard library: RFC 8017 §8.2.2 verification is one modular
exponentiation and a constant-time comparison of the full re-encoded block (never a parse of the
decrypted block, which is what Bleichenbacher-style forgeries exploit). GridQ's runtime
dependencies are pydantic only; ``cryptography`` is installed solely via the optional ``app``
extra, and contracts are a leaf, so no crypto dependency is added. The tests cross-check this
verifier against ``cryptography`` when it is installed. Signing is the identity service's job
(WP-PLAT-06) and is not in this module.

**Refused, by construction:** any ``alg`` but RS256 (so ``none`` and the HS256 key-confusion attack
are impossible); a key under 2048 bits or with an exponent other than 65537; a ``kid`` not in the
key set; non-canonical base64url; duplicate JSON keys; unknown header fields or claims; a token
used outside ``nbf <= at < exp``; a lifetime over `MAX_TOKEN_LIFETIME_S`; and **fixture keys**
(``kid`` starting ``TEST-ONLY-``) unless the caller passes ``allow_fixture_keys=True``.

**Roles** (spec E-04): Neura ops, customer, lender read-only, verifier. What each may do is in
`ROLE_ACTIONS`, and the claims model enforces it, so a lender token carrying a write scope cannot
even be constructed. A role belongs to exactly one kind of organisation (`ROLE_ORG_KIND`). A
control write needs MFA (``amr`` contains ``mfa``). A customer's scopes are confined to its own
organisation. Only Neura ops may hold a wildcard organisation.

**No PII.** Claims carry opaque ids only (no name, no e-mail): an audit log copies the actor into
every event, and E-09 data protection is simpler if the log never holds personal data.

Determinism: nothing reads the clock. Validity is always checked *at* an explicit instant.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final, Literal, final

from pydantic import Field, ValidationError, model_validator

from gridq.contracts._base import ContractModel, NonEmptyStr, content_hash

__all__ = [
    "ALL",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "FIXTURE_KID_PREFIX",
    "GIT_TAG",
    "MAX_TOKEN_LIFETIME_S",
    "ROLE_ACTIONS",
    "ROLE_ORG_KIND",
    "Action",
    "AuthMethod",
    "IdentityClaims",
    "Jwk",
    "Jwks",
    "JwsHeader",
    "OrgKind",
    "Organisation",
    "ResourceKind",
    "ResourceRef",
    "Role",
    "ScopeGrant",
    "TokenInvalid",
    "authorise",
    "b64url_decode",
    "b64url_encode",
    "validate_token",
]

#: The roadmap's name is NEW-IC-13; the payload literal is "IC-13".
CONTRACT_ID: Final = "IC-13"
CONTRACT_VERSION: Final = "1.0.0"
GIT_TAG: Final = "contracts/ic13-identity-audit/v1.0.0"

#: Twelve hours. A token outliving a working shift should be refreshed, not trusted.
MAX_TOKEN_LIFETIME_S: Final = 12 * 3600
#: A key whose ``kid`` starts with this is a committed test fixture and is refused by default.
FIXTURE_KID_PREFIX: Final = "TEST-ONLY-"
#: Scope wildcard: every organisation (Neura ops only) or every resource of one organisation.
ALL: Final = "*"

_MIN_RSA_BITS: Final = 2048
_RSA_E: Final = 65537
#: DER prefix of DigestInfo for SHA-256 (RFC 8017 §9.2, note 1).
_SHA256_DIGEST_INFO: Final = bytes.fromhex("3031300d060960864801650304020105000420")
_B64URL = re.compile(r"^[A-Za-z0-9_-]*$")


class Role(StrEnum):
    """Spec E-04: Neura ops / customer / lender read-only / verifier."""

    NEURA_OPS = "neura_ops"
    CUSTOMER = "customer"
    LENDER_READONLY = "lender_readonly"
    VERIFIER = "verifier"


class OrgKind(StrEnum):
    NEURA = "neura"
    CUSTOMER = "customer"
    LENDER = "lender"
    VERIFIER = "verifier"


class Action(StrEnum):
    READ = "read"
    #: A non-control write (upload site data, register evidence, approve a pack).
    WRITE = "write"
    #: A write that reaches a device (setpoint, schedule): spec B1-04 / B5.
    CONTROL_WRITE = "control_write"


class ResourceKind(StrEnum):
    SITE = "site"
    CUSTOMER_DATA = "customer_data"
    TELEMETRY = "telemetry"
    LEDGER = "ledger"
    EVIDENCE = "evidence"
    SCENARIO_SET = "scenario_set"
    PACK = "pack"
    METHOD = "method"
    #: A controllable device at a site (BESS, PPC, chiller). The only target of a control write.
    DEVICE = "device"


class AuthMethod(StrEnum):
    """RFC 8176 authentication method reference values accepted in ``amr``."""

    PWD = "pwd"
    OTP = "otp"
    HWK = "hwk"
    SWK = "swk"
    SC = "sc"
    PIN = "pin"
    FPT = "fpt"
    FACE = "face"
    MFA = "mfa"


ROLE_ACTIONS: Final[dict[Role, frozenset[Action]]] = {
    Role.NEURA_OPS: frozenset({Action.READ, Action.WRITE, Action.CONTROL_WRITE}),
    Role.CUSTOMER: frozenset({Action.READ, Action.WRITE}),
    Role.LENDER_READONLY: frozenset({Action.READ}),
    Role.VERIFIER: frozenset({Action.READ}),
}

ROLE_ORG_KIND: Final[dict[Role, OrgKind]] = {
    Role.NEURA_OPS: OrgKind.NEURA,
    Role.CUSTOMER: OrgKind.CUSTOMER,
    Role.LENDER_READONLY: OrgKind.LENDER,
    Role.VERIFIER: OrgKind.VERIFIER,
}

_Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$", max_length=200)]
_IdOrAll = Annotated[str, Field(pattern=r"^(\*|[A-Za-z0-9][A-Za-z0-9._:@/-]*)$", max_length=200)]


class Organisation(ContractModel):
    org_id: _Id
    kind: OrgKind


class ResourceRef(ContractModel):
    """One concrete resource: what an audit event targets and what `authorise` checks."""

    kind: ResourceKind
    org_id: _Id
    resource_id: _Id


class ScopeGrant(ContractModel):
    """Permission for ``actions`` on resources of ``resource`` kind, owned by ``org_id``.

    ``org_id`` / ``resource_id`` may be ``"*"`` (all). Actions are sorted and unique.
    """

    resource: ResourceKind
    org_id: _IdOrAll
    resource_id: _IdOrAll
    actions: tuple[Action, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _shape(self) -> ScopeGrant:
        if list(self.actions) != sorted(set(self.actions)):
            raise ValueError("actions must be sorted and unique (one canonical form)")
        if Action.CONTROL_WRITE in self.actions and self.resource is not ResourceKind.DEVICE:
            raise ValueError("control_write applies only to a device")
        if self.resource is ResourceKind.DEVICE and Action.WRITE in self.actions:
            raise ValueError("a device is written by control_write, never by a plain write")
        if self.org_id == ALL and self.resource_id != ALL:
            raise ValueError("a wildcard organisation needs a wildcard resource_id")
        return self

    def _key(self) -> tuple[str, str, str]:
        return (self.resource.value, self.org_id, self.resource_id)

    def covers(self, action: Action, target: ResourceRef) -> bool:
        return (
            action in self.actions
            and self.resource is target.kind
            and self.org_id in (ALL, target.org_id)
            and self.resource_id in (ALL, target.resource_id)
        )


@final
class IdentityClaims(ContractModel):
    """IC-13 v1.0.0 claims: the JWT payload of a Neura access token.

    Registered claims (RFC 7519 §4.1): ``iss``, ``sub``, ``aud``, ``iat``, ``nbf``, ``exp``,
    ``jti``. Neura claims: ``org``, ``role``, ``scopes``, ``amr`` (RFC 8176), plus the contract
    id and version so a token of another contract version is refused, not misread.
    """

    contract: Literal["IC-13"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    iss: NonEmptyStr
    #: The user: an opaque, stable id (never an e-mail address or a name).
    sub: _Id
    aud: NonEmptyStr
    iat: Annotated[int, Field(ge=0)]
    nbf: Annotated[int, Field(ge=0)]
    exp: Annotated[int, Field(ge=0)]
    jti: _Id
    org: Organisation
    role: Role
    #: Sorted by (resource, org_id, resource_id); no two grants for the same triple.
    scopes: tuple[ScopeGrant, ...] = Field(min_length=1)
    amr: tuple[AuthMethod, ...] = Field(min_length=1)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("IdentityClaims is final: a new claims shape is a new contract version")

    @model_validator(mode="after")
    def _consistent(self) -> IdentityClaims:
        if not self.iat <= self.nbf < self.exp:
            raise ValueError("token times must satisfy iat <= nbf < exp")
        if self.exp - self.iat > MAX_TOKEN_LIFETIME_S:
            raise ValueError(f"token lifetime exceeds {MAX_TOKEN_LIFETIME_S} s")
        if ROLE_ORG_KIND[self.role] is not self.org.kind:
            raise ValueError(
                f"role {self.role.value} belongs to a {ROLE_ORG_KIND[self.role].value} "
                f"organisation, not a {self.org.kind.value} one"
            )
        if list(self.amr) != sorted(set(self.amr)):
            raise ValueError("amr must be sorted and unique")
        keys = [g._key() for g in self.scopes]
        if keys != sorted(set(keys)):
            raise ValueError("scopes must be sorted by (resource, org_id, resource_id), no repeats")
        allowed = ROLE_ACTIONS[self.role]
        for g in self.scopes:
            extra = set(g.actions) - allowed
            if extra:
                raise ValueError(
                    f"role {self.role.value} may not hold {sorted(a.value for a in extra)} "
                    f"(scope on {g.resource.value})"
                )
            if g.org_id == ALL and self.role is not Role.NEURA_OPS:
                raise ValueError("only neura_ops may hold a wildcard organisation")
            if self.role is Role.CUSTOMER and g.org_id != self.org.org_id:
                raise ValueError("a customer's scopes are confined to its own organisation")
            if Action.CONTROL_WRITE in g.actions and AuthMethod.MFA not in self.amr:
                raise ValueError("a control_write scope requires multi-factor auth (amr: mfa)")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


def authorise(claims: IdentityClaims, action: Action, target: ResourceRef) -> ScopeGrant | None:
    """The first scope (in canonical order) that permits ``action`` on ``target``, else ``None``."""
    for g in claims.scopes:
        if g.covers(action, target):
            return g
    return None


# --------------------------------------------------------------------------------------------
# Keys and token validation
# --------------------------------------------------------------------------------------------


class TokenInvalid(ValueError):
    """The token was refused. ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Strict base64url (RFC 7515 §2): no padding, no other characters, canonical bits only."""
    if not _B64URL.fullmatch(text) or len(text) % 4 == 1:
        raise ValueError("not base64url without padding")
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if b64url_encode(raw) != text:
        raise ValueError("non-canonical base64url (stray low bits)")
    return raw


def _b64_uint(text: str) -> int:
    raw = b64url_decode(text)
    if not raw or raw[0] == 0:
        raise ValueError("a JWK integer must be big-endian with no leading zero byte")
    return int.from_bytes(raw, "big")


class Jwk(ContractModel):
    """A public RSA signing key (RFC 7517 / RFC 7518 §6.3.1). Private members are refused."""

    kty: Literal["RSA"]
    use: Literal["sig"]
    alg: Literal["RS256"]
    kid: _Id
    n: NonEmptyStr
    e: NonEmptyStr

    @model_validator(mode="after")
    def _strong(self) -> Jwk:
        try:
            n, e = _b64_uint(self.n), _b64_uint(self.e)
        except ValueError as exc:
            raise ValueError(f"key {self.kid}: {exc}") from None
        if n.bit_length() < _MIN_RSA_BITS:
            raise ValueError(f"key {self.kid}: RSA modulus under {_MIN_RSA_BITS} bits")
        if e != _RSA_E:
            raise ValueError(f"key {self.kid}: RSA exponent must be 65537")
        return self

    @property
    def is_fixture(self) -> bool:
        return self.kid.startswith(FIXTURE_KID_PREFIX)

    def numbers(self) -> tuple[int, int]:
        return _b64_uint(self.n), _b64_uint(self.e)


class Jwks(ContractModel):
    keys: tuple[Jwk, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Jwks:
        kids = [k.kid for k in self.keys]
        if len(kids) != len(set(kids)):
            raise ValueError("kid values must be unique in a key set")
        return self

    def get(self, kid: str) -> Jwk | None:
        return next((k for k in self.keys if k.kid == kid), None)


class JwsHeader(ContractModel):
    alg: Literal["RS256"]
    typ: Literal["JWT"]
    kid: _Id


def _rs256_verify(key: Jwk, signing_input: bytes, signature: bytes) -> bool:
    """RFC 8017 §8.2.2 RSASSA-PKCS1-v1_5-VERIFY with SHA-256, by re-encoding and comparing."""
    n, e = key.numbers()
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _SHA256_DIGEST_INFO + hashlib.sha256(signing_input).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate JSON key {k!r}")
        out[k] = v
    return out


def _json_object(raw: bytes, what: str) -> dict[str, Any]:
    """Parse once with duplicate keys refused (pydantic's JSON parser keeps the last duplicate)."""
    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError) as exc:
        raise TokenInvalid("malformed", f"{what} is not valid JSON: {exc}") from None
    if not isinstance(obj, dict):
        raise TokenInvalid("malformed", f"{what} is not a JSON object")
    return obj


def validate_token(
    token: str,
    jwks: Jwks,
    *,
    issuer: str,
    audience: str,
    at: datetime,
    leeway_s: int = 0,
    allow_fixture_keys: bool = False,
) -> IdentityClaims:
    """Validate a compact JWS token offline, as of ``at``; return its claims or raise.

    Raises `TokenInvalid` (with a stable ``code``) for every refusal.

    Order: structure → header → key → signature → claims → issuer/audience → time. The signature
    is checked before the payload is parsed into claims, so unsigned content is never interpreted.
    """
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenInvalid("malformed", "a JWS compact token has exactly three parts")
    try:
        header_raw, payload_raw, sig = (b64url_decode(p) for p in parts)
    except ValueError as exc:
        raise TokenInvalid("malformed", str(exc)) from None
    try:
        _json_object(header_raw, "header")
        header = JwsHeader.model_validate_json(header_raw)
    except ValidationError as exc:
        raise TokenInvalid(
            "bad_header", f"only alg RS256, typ JWT and a kid are accepted: {exc}"
        ) from None
    key = jwks.get(header.kid)
    if key is None:
        raise TokenInvalid("unknown_key", f"kid {header.kid!r} is not in the key set")
    if key.is_fixture and not allow_fixture_keys:
        raise TokenInvalid("fixture_key", f"kid {header.kid!r} is a test fixture key")
    if not _rs256_verify(key, f"{parts[0]}.{parts[1]}".encode("ascii"), sig):
        raise TokenInvalid("bad_signature", "RS256 signature does not verify")
    try:
        _json_object(payload_raw, "payload")
        claims = IdentityClaims.model_validate_json(payload_raw)
    except ValidationError as exc:
        raise TokenInvalid("bad_claims", str(exc)) from None
    if claims.iss != issuer:
        raise TokenInvalid("wrong_issuer", f"iss {claims.iss!r} != {issuer!r}")
    if claims.aud != audience:
        raise TokenInvalid("wrong_audience", f"aud {claims.aud!r} != {audience!r}")
    now = at.timestamp()
    if now + leeway_s < claims.nbf:
        raise TokenInvalid("not_yet_valid", "token used before nbf")
    if now - leeway_s >= claims.exp:
        raise TokenInvalid("expired", "token used at or after exp")
    return claims
