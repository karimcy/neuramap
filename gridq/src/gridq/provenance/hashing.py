"""Deterministic, canonical JSON + hashing utilities.

These make a quote reproducible: the same input always produces the same hash, and a
changed input always produces a different one. Used to stamp every run.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def _default(obj: Any) -> Any:
    """Render non-JSON-native values canonically for hashing."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=repr)
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"cannot serialise {type(obj).__name__} for hashing")


def stable_json_dumps(obj: Any) -> str:
    """Serialise ``obj`` to canonical JSON: sorted keys, compact, deterministic.

    Key order does not affect the output, so equal-by-value objects serialise identically.
    """
    return json.dumps(
        obj,
        default=_default,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def hash_json(obj: Any) -> str:
    """Return ``sha256:<hex>`` of the canonical JSON of ``obj``."""
    digest = hashlib.sha256(stable_json_dumps(obj).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def hash_file(path: str | Path) -> str:
    """Return ``sha256:<hex>`` of a file's bytes (streamed)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"
