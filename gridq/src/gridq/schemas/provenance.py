"""Schema base model + provenance container.

`GridQModel` is the base every schema inherits: it forbids unknown fields and carries
`schema_version`, satisfying the rule that every model is versioned. `Provenance` is a
reusable block of the determinism/traceability identifiers.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0.1.0"


class GridQModel(BaseModel):
    """Base for all GridQ schema models.

    - ``extra="forbid"`` — unknown fields are a validation error (catches typos/drift).
    - ``schema_version`` — present on every model (defaults to the package version).

    **Deliberately NOT ``frozen`` and NOT ``validate_assignment``, and this is not an oversight.**
    Both were measured against the real `SiteModel` before being rejected, because a model that
    cannot be mutated after construction is otherwise the stronger contract for anything that feeds
    ``input_hash``:

    - ``validate_assignment=True`` → **RecursionError** building any site that declares
      ``stream_consents``. `SiteModel._canonicalise_stream_consents` assigns the sorted tuple back
      to ``self`` inside a ``mode="after"`` validator; re-validating on assignment re-enters the
      validator, which assigns again, without a fixed point.
    - ``frozen=True`` → ``ValidationError`` (``frozen_instance``) building any site that declares
      ``stream_consents`` *or* an empty ``derived_assumptions`` list. Both of `SiteModel`'s
      after-validators normalise by assigning to ``self``, and the empty-list case is precisely the
      D42/D44 hash-stability path (``[]`` → ``None`` so it is popped from the canonical run input).

    So the flags cannot be set here without first rewriting those validators to return a new model.
    Where post-construction immutability genuinely matters, it is set **on the individual model**:
    see `gridq.schemas.derived.DerivedAssumption`, which is ``frozen`` because a record that
    validated as a derived fact must not be able to become a fabricated consent afterwards.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)


class Provenance(GridQModel):
    """Reusable traceability block — pins a computation to its inputs and versions.

    All optional so it can be attached incrementally; populated fully on a finished run.
    """

    input_hash: str | None = None
    assumption_version: str | None = None
    code_version: str | None = None
    model_version: str | None = None
    seed: int | None = None
    output_hash: str | None = None
    reproduction_env: str | None = None
