"""RunContext — the provenance stamp attached to every run.

Pins a run to its input hash + assumption/code/schema versions so any output can be
traced and reproduced. The ``run_id`` is derived deterministically from those pins
(not from the clock), so identical inputs yield the same run_id.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from gridq.provenance.git import get_code_version
from gridq.provenance.hashing import hash_json
from gridq.schemas.provenance import GridQModel


class RunContext(GridQModel):
    """Identity + provenance for one engine/finance/quote run.

    Inherits ``schema_version`` from ``GridQModel``; here it records the schema version
    the run was built against (set explicitly by ``build_run_context``).
    """

    run_id: str
    input_hash: str
    assumption_version: str
    code_version: str
    # schema_version is inherited from GridQModel and set per run.
    created_at: datetime = Field(default_factory=datetime.now)
    warnings: list[str] = Field(default_factory=list)


def build_run_context(
    input_obj: Any,
    assumption_version: str,
    schema_version: str,
    *,
    warnings: list[str] | None = None,
    created_at: datetime | None = None,
) -> RunContext:
    """Build a `RunContext` for a run over ``input_obj``.

    Hashes the input, captures the code version, and derives a deterministic ``run_id``
    from the pinning identifiers. ``created_at`` is metadata and defaults to now.

    **Caller contract: ``input_obj`` must contain no code-version-bearing field.** The four pins
    below are combined here precisely because they are *independent*: ``input_hash`` answers "did
    the inputs change?", and code identity enters ``run_id`` through its own ``code_version`` pin.
    An ``input_obj`` that carries ``code_version`` — directly, or via a derived id such as a
    `FinancialCase.case_id` that was itself hashed over one — collapses that independence: the
    resulting ``input_hash`` moves on every commit and can no longer distinguish "same inputs, new
    code" from "the inputs changed". This is not enforced here (the input is deliberately opaque);
    it is the caller's job, and `quotes.assembly._canonical_run_input` is where it is discharged.
    """
    input_hash = hash_json(input_obj)
    code_version = get_code_version()
    run_seed = hash_json(
        {
            "input_hash": input_hash,
            "assumption_version": assumption_version,
            "code_version": code_version,
            "schema_version": schema_version,
        }
    )
    run_id = "run_" + run_seed.split(":", 1)[1][:16]
    return RunContext(
        run_id=run_id,
        input_hash=input_hash,
        assumption_version=assumption_version,
        code_version=code_version,
        schema_version=schema_version,
        created_at=created_at or datetime.now(),
        warnings=list(warnings) if warnings else [],
    )
