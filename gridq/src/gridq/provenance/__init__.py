"""Provenance (X.2) — hashing, code version, and the per-run RunContext.

Canonical hashing makes runs reproducible; `build_run_context` stamps every run with
its input hash + assumption/code/schema versions. See docs/provenance.md.

`claims` holds the other half: the `PublicationReceipt` that pins a run's artifacts by content, and
`publication_claim`, the exact — and testable — sentence a pack rendered from those artifacts is
allowed to print about where its figures came from.

(Distinct from `gridq.schemas.provenance`, which holds the `GridQModel` base and the
reusable `Provenance` model that this package's `RunContext` builds on.)
"""

from __future__ import annotations

from gridq.provenance.claims import (
    CLAIM_VERSION,
    RECOMPUTED_AT_RENDER,
    PublicationReceipt,
    RunArtifact,
    publication_claim,
)
from gridq.provenance.git import get_code_version, get_git_commit
from gridq.provenance.hashing import hash_file, hash_json, stable_json_dumps
from gridq.provenance.run_context import RunContext, build_run_context

__all__ = [
    "stable_json_dumps",
    "hash_json",
    "hash_file",
    "get_git_commit",
    "get_code_version",
    "build_run_context",
    "RunContext",
    "PublicationReceipt",
    "RunArtifact",
    "publication_claim",
    "CLAIM_VERSION",
    "RECOMPUTED_AT_RENDER",
]
