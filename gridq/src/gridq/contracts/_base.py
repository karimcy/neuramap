"""Shared base for the frozen interface contracts (spec §10A, IC-n).

Four things live here because every contract needs them, and they must behave identically:

1. **`ContractModel`**: frozen, strict, ``extra="forbid"``, no NaN/inf. A contract instance is a
   value that gets hashed and referenced; it must not change after it validated, must not coerce
   ``"5"`` into ``5.0`` silently, and must refuse a typo'd field.
2. **`Unknown` / `NotApplicable`**: the explicit replacements for ``None``. No field in any
   contract accepts ``None`` (a test walks every annotation to prove it). A value that exists in
   the world but is not known to us is ``Unknown(reason=...)``; a term that does not exist for
   this agreement is ``NotApplicable(reason=...)``. Both carry a reason, so a reader always learns
   *why* there is no value. This mirrors GridQ's D45 rule ("a null is never an acceptable input").
3. **Canonical hashing** (`canonical_json`, `content_hash`): how an instance becomes the
   ``sha256:<hex>`` it is referenced by. Defined once, below, and pinned by golden-hash tests.
4. **`Version1x`** (added with IC-4 1.1.0, WP-BO-06): the version a *reference* to another
   contract carries. It accepts any ``1.<minor>.<patch>``; a contract's own ``contract_version``
   stays an exact literal. The reference's hash is the binding, and `evidence.resolve_ref` checks
   the version against the object it points at.

These contracts deliberately do **not** inherit `gridq.schemas.GridQModel`: that base carries
GridQ's ``schema_version = "0.1.0"`` and is intentionally not frozen. A contract carries its own
``contract`` id and ``contract_version`` instead. Nor do they import GridQ workstream code: the
roadmap requires contracts to be a leaf in the import graph (roadmap.yaml ``meta.principles``).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints

__all__ = [
    "EMPTY_SHA256",
    "IsoDate",
    "SHA256_PATTERN",
    "ContractModel",
    "UtcInstant",
    "VERSION_1X_PATTERN",
    "Version1x",
    "NonEmptyStr",
    "NotApplicable",
    "Sha256",
    "Unknown",
    "canonical_json",
    "content_hash",
    "not_applicable",
    "unknown",
]

#: The only accepted hash form: ``sha256:`` plus 64 lower-case hex digits (GridQ's existing
#: `gridq.provenance.hashing` form, so a contract hash and a run hash read the same way).
SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
Sha256 = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]

#: SHA-256 of zero bytes. A document hash equal to this is the hash of *no document*, which is
#: exactly what an assumption has, so the evidence contract refuses it.
EMPTY_SHA256 = "sha256:" + hashlib.sha256(b"").hexdigest()

NonEmptyStr = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]

#: A version inside a reference to another contract: any ``1.<minor>.<patch>``, no leading zeros,
#: ASCII digits only (``[0-9]``, not ``\d``: pydantic's regex engine reads ``\d`` as any Unicode
#: digit, a JSON Schema validator as ASCII only, and the two must agree). A contract's own
#: ``contract_version`` is never this type: it stays an exact ``Literal`` (WP-BO-06, plan §5).
VERSION_1X_PATTERN = r"^1\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
Version1x = Annotated[str, StringConstraints(pattern=VERSION_1X_PATTERN)]

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _iso_date(v: Any) -> Any:
    """Accept a `date` or an ISO-8601 ``YYYY-MM-DD`` string; refuse datetimes and timestamps.

    Needed because strict mode refuses date strings once a ``mode="before"`` validator has turned
    JSON input into Python input, and lax mode would also accept integer timestamps.
    """
    if isinstance(v, datetime):
        raise ValueError("expected a calendar date, not a datetime")
    if isinstance(v, date):
        return v
    if isinstance(v, str) and _ISO_DATE.fullmatch(v):
        return date.fromisoformat(v)
    raise ValueError("expected an ISO-8601 date YYYY-MM-DD")


#: A calendar date, wire format ``YYYY-MM-DD``. Every date in every contract uses this type.
IsoDate = Annotated[date, BeforeValidator(_iso_date)]

_ISO_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})")


def _utc_instant(v: Any) -> Any:
    """Accept an offset-carrying ISO-8601 instant (or an aware `datetime`); store it in UTC.

    A naive time is refused: it is ambiguous across the GB clock change. Converting every input
    to UTC is what makes ``01:00+01:00`` and ``00:00Z`` the same value and the same hash.
    Added with IC-2 and IC-13 (2026-11-17); IC-1 and IC-4 carry no instants and are unchanged.
    """
    if isinstance(v, str) and _ISO_INSTANT.fullmatch(v):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if not isinstance(v, datetime):
        raise ValueError(
            "expected an ISO-8601 instant with a UTC offset, e.g. 2026-06-11T00:00:00Z"
        )
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError("an instant must carry a UTC offset; a naive time is ambiguous")
    return v.astimezone(UTC)


#: An instant in time, held in UTC; wire format ``YYYY-MM-DDTHH:MM:SS[.ffffff]Z``.
UtcInstant = Annotated[datetime, BeforeValidator(_utc_instant)]


class ContractModel(BaseModel):
    """Base for every contract model: frozen, strict, closed, finite.

    - ``frozen``: an instance is hashed and referenced by that hash; it must not mutate.
    - ``strict``: no silent coercion (``"5"`` is not a float, ``1`` is not ``True``). JSON input
      still accepts ISO-8601 strings for dates, which is the wire format.
    - ``extra="forbid"``: an unknown field is a validation error.
    - ``allow_inf_nan=False``: NaN/inf have no canonical JSON form and are never a real term.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )


class Unknown(ContractModel):
    """A value that exists but is not known. Always explicit, always with a reason; never ``None``.

    Serialised as ``{"kind": "unknown", "reason": "..."}``. ``kind`` is required (no default) so
    a hand-written payload that omits it is refused rather than guessed at.
    """

    kind: Literal["unknown"]
    reason: NonEmptyStr


class NotApplicable(ContractModel):
    """A term that does not exist for this record (e.g. a LIFO position on a firm connection).

    Distinct from `Unknown`: "not applicable" is a statement about the agreement, "unknown" is a
    statement about our knowledge of it. Both carry a reason.
    """

    kind: Literal["not_applicable"]
    reason: NonEmptyStr


def unknown(reason: str) -> Unknown:
    """Build an `Unknown` with its reason."""
    return Unknown(kind="unknown", reason=reason)


def not_applicable(reason: str) -> NotApplicable:
    """Build a `NotApplicable` with its reason."""
    return NotApplicable(kind="not_applicable", reason=reason)


# --------------------------------------------------------------------------------------------
# Canonical form and hash
# --------------------------------------------------------------------------------------------


def _normalise(node: Any) -> Any:
    """Recursively normalise a JSON-mode dump so equal values have one byte form.

    - strings (values and keys) are Unicode NFC, so a composed and a decomposed "é" hash equal;
    - ``-0.0`` becomes ``0.0``;
    - integral floats stay floats (``20.0``): pydantic has already typed every number, so a float
      field is always dumped as a float whatever the input wrote (``20`` or ``20.0``).
    """
    if isinstance(node, dict):
        return {unicodedata.normalize("NFC", str(k)): _normalise(v) for k, v in node.items()}
    if isinstance(node, list | tuple):
        return [_normalise(v) for v in node]
    if isinstance(node, str):
        return unicodedata.normalize("NFC", node)
    if isinstance(node, float) and node == 0.0:
        return 0.0
    return node


def canonical_json(model: ContractModel) -> str:
    """The canonical JSON text of a contract instance.

    Definition (part of each contract's frozen version):

    1. ``model.model_dump(mode="json")``: dates as ISO-8601 ``YYYY-MM-DD``, enums as their
       values, tuples as arrays, every field present (nothing excluded, no aliases);
    2. `_normalise` (NFC strings, ``-0.0`` → ``0.0``);
    3. ``json.dumps`` with ``sort_keys=True``, ``separators=(",", ":")``, ``ensure_ascii=False``,
       ``allow_nan=False``. Floats use Python's shortest round-trip ``repr``.

    This is GridQ's existing `stable_json_dumps` convention plus step 2. It is *not* RFC 8785
    (JCS): a non-Python consumer recomputing a hash must reproduce Python float formatting.
    """
    return json.dumps(
        _normalise(model.model_dump(mode="json")),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def content_hash(model: ContractModel) -> str:
    """``sha256:<hex>`` of the UTF-8 bytes of `canonical_json`. How instances are referenced."""
    return "sha256:" + hashlib.sha256(canonical_json(model).encode("utf-8")).hexdigest()
