"""The library's identity: id, semantic version, source hash, and the stamp and pin built from them.

**Version discipline (D177).** ``LIBRARY_VERSION`` is a semantic version; the library hash
is the SHA-256 of the package's own source (every ``*.py`` file here except ``_releases.py``, by
sorted name, newlines normalised). ``_releases.RELEASES`` pins the hash each released version had,
and a test fails when the source moves without a version and a release entry moving with it. So a
result stamped ``1.0.0`` was solved with exactly the ``1.0.0`` constraint code: a patch for a
change that moves no row or bound, a minor for an added constraint, a major for any change to an
existing constraint's rows, bounds or defaults.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Final

from gridq.constraint_library.catalogue import constraint_ids
from gridq.contracts.dispatch_envelope import (
    ConstraintLibraryPin,
    DispatchEnvelope,
    LibraryCheck,
    check_constraint_library,
)
from gridq.schemas import ConstraintLibraryRef

LIBRARY_ID: Final = "gridq.constraint_library"
LIBRARY_VERSION: Final = "1.0.1"

_PACKAGE_DIR: Final = Path(__file__).resolve().parent
#: The one file the hash leaves out: it records the hash, so it cannot be inside it.
_RELEASES_FILE: Final = "_releases.py"


def source_files() -> tuple[Path, ...]:
    """The files the library hash covers, in hashing order."""
    return tuple(
        sorted(
            (p for p in _PACKAGE_DIR.glob("*.py") if p.name != _RELEASES_FILE),
            key=lambda p: p.name,
        )
    )


@lru_cache(maxsize=1)
def library_hash() -> str:
    """``sha256:`` + the hex SHA-256 of the library source (name, NUL, text, NUL per file)."""
    digest = hashlib.sha256()
    for path in source_files():
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        digest.update(path.name.encode("utf-8") + b"\0" + text.encode("utf-8") + b"\0")
    return "sha256:" + digest.hexdigest()


def library_ref() -> ConstraintLibraryRef:
    """The stamp every `OptimisationResult` carries (IC-12 3.2.0)."""
    return ConstraintLibraryRef(
        library_id=LIBRARY_ID,
        library_version=LIBRARY_VERSION,
        library_hash=library_hash(),
        constraint_ids=constraint_ids(),
    )


def library_pin() -> ConstraintLibraryPin:
    """The IC-3 pin an envelope exporter writes into ``source.constraint_library`` (WP-SIZE-08)."""
    return ConstraintLibraryPin(
        kind="constraint_library_pin",
        library_version=LIBRARY_VERSION,
        library_hash=library_hash(),
        constraint_ids=constraint_ids(),
    )


def check_envelope(envelope: DispatchEnvelope) -> LibraryCheck:
    """This library against an envelope's pin: what live dispatch runs before honouring it."""
    return check_constraint_library(
        envelope, library_version=LIBRARY_VERSION, library_hash=library_hash()
    )
