"""The constraint-library stamp a result carries (IC-12 3.2.0, WP-SIZE-02, D178).

`ConstraintLibraryRef` names the versioned dispatch constraint set (`gridq.constraint_library`)
that produced an `OptimisationResult`: its id, its semantic version, the SHA-256 of its source and
the sorted ids of the constraints it defines. The fields mirror IC-3's `ConstraintLibraryPin`, so
an envelope exporter restates the stamp as the pin and live dispatch compares its own library with
it (`gridq.contracts.dispatch_envelope.check_constraint_library`).

The library is code, so the stamp is metadata in the sense of ``code_version``: it is pinned in the
run's identity by ``code_version`` through ``run_id`` and is kept out of ``input_hash``
(`gridq.quotes.assembly._canonical_result_dump`). A result built without one (a 3.1.0 payload, a
hand-built fixture) dumps with the member absent, byte for byte as before.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, Field, field_validator

from gridq.contracts._base import SHA256_PATTERN
from gridq.schemas.provenance import GridQModel

__all__ = ["CONSTRAINT_ID_PATTERN", "ConstraintLibraryRef"]

#: The id pattern IC-3 pins constraint ids with (`dispatch_envelope._REGISTRY_ID`).
CONSTRAINT_ID_PATTERN = r"^[a-z][a-z0-9_]*$"


class ConstraintLibraryRef(GridQModel):
    """Which constraint library, at which version and source hash, the result was solved with."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    library_id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
    library_version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    library_hash: str = Field(pattern=SHA256_PATTERN)
    constraint_ids: tuple[Annotated[str, Field(pattern=CONSTRAINT_ID_PATTERN)], ...] = Field(
        min_length=1
    )

    @field_validator("constraint_ids")
    @classmethod
    def _canonical(cls, ids: tuple[str, ...]) -> tuple[str, ...]:
        if list(ids) != sorted(set(ids)):
            raise ValueError("constraint_ids must be sorted with no repeats")
        return ids
