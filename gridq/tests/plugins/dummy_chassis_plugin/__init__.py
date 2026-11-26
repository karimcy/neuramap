"""A dummy chassis plugin: the WP-CORE-03 acceptance fixture.

It stands for another workstream's package. It registers one load archetype, one study basis,
one dispatch constraint and one resource through `gridq.extensions`, from a directory no
CORE-owned path contains, and names nothing in CORE that CORE had to add for it.
`tests/test_chassis_extension_registries.py` loads it both ways (explicitly, and through a
``gridq.extensions`` entry point) and fails if either needs an edit under a CORE-owned path.

Nothing here is data. The flat shape and the constraint are fixtures, labelled as such.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import ConfigDict, Field

from gridq.assumptions import AssumptionLibrary
from gridq.extensions import ConstraintSpec, ExtensionHost, ResourceKind, ResourceSpec
from gridq.ingestion.hh_synthesis import ProfileSource, ReferenceProfile
from gridq.schemas import ConfidenceBand, GridQModel, SiteModel
from gridq.value_stack.interface import DispatchConstraintDescriptor

__version__ = "0.0.1"

ARCHETYPE_ID = "dummy_plugin_flat"
BASIS_ID = "dummy_plugin_basis_v1"
CONSTRAINT_ID = "dummy_plugin_import_cap"
RESOURCE_ID = "dummy_plugin_flexible_load"

FLAT_ARCHETYPE = ReferenceProfile(
    profile_id=ARCHETYPE_ID,
    label="Dummy plugin archetype (test fixture, flat)",
    source=ProfileSource.ARCHETYPE_PENDING_SOURCE,
    source_ref="Test fixture: a flat shape. Not a load model.",
    weekday=(1.0,) * 48,
    saturday=(1.0,) * 48,
    sunday=(1.0,) * 48,
)


class DummyPluginBasis(GridQModel):
    """A basis from another package. Deliberately not a `StudyBasis` subclass."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    BASIS_ID: ClassVar[str] = BASIS_ID
    LABEL: ClassVar[str] = "DUMMY PLUGIN BASIS (test fixture) — NOT AN UNDERWRITING OUTPUT"

    basis_id: Literal["dummy_plugin_basis_v1"]
    authorised_by: str = Field(min_length=1)
    confidence_ceiling: ConfidenceBand = ConfidenceBand.LOW

    def note(self) -> str:
        return f"{self.LABEL}. Authorised by {self.authorised_by}."


def _describe_import_cap(
    site: SiteModel, assumptions: AssumptionLibrary
) -> DispatchConstraintDescriptor:
    return DispatchConstraintDescriptor(
        constraint_id=CONSTRAINT_ID,
        description="Test fixture: an import cap described by a plugin.",
        kind="connection_limit",
        parameter_source="connection.mic_kw",
    )


IMPORT_CAP = ConstraintSpec(
    constraint_id=CONSTRAINT_ID,
    kind="connection_limit",
    description="Test fixture: an import cap described by a plugin.",
    describe=_describe_import_cap,
)

FLEXIBLE_LOAD = ResourceSpec(
    resource_id=RESOURCE_ID,
    label="Dummy plugin flexible load (test fixture)",
    kind=ResourceKind.FLEXIBLE_LOAD,
    description="Test fixture: a flexible load whose availability an IC-11 surface would carry.",
    availability_contract="IC-11",
)


def register(host: ExtensionHost) -> None:
    host.register_load_archetype(FLAT_ARCHETYPE)
    host.register_study_basis(DummyPluginBasis)
    host.register_constraint(IMPORT_CAP)
    host.register_resource(FLEXIBLE_LOAD)
