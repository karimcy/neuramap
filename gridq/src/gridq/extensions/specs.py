"""What a plugin registers at the two extension points that have no chassis model of their own.

Load archetypes register a `gridq.ingestion.hh_synthesis.ReferenceProfile` and study bases
register a basis class (`gridq.schemas.study.NamedBasis`), both of which already exist. Resources
and constraints had no declaration type, so they get one here.

A spec is a declaration plus code. It holds **no assumption value**: a number a constraint or a
resource needs comes from the `SiteModel` or the assumption release at solve time, exactly as
`DispatchConstraintDescriptor` already requires.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gridq.assumptions import AssumptionLibrary
    from gridq.schemas import SiteModel
    from gridq.value_stack.interface import DispatchConstraintDescriptor

__all__ = ["ConstraintSpec", "ResourceKind", "ResourceSpec"]


class ResourceKind(StrEnum):
    """How a resource can act on the site's net load."""

    #: Charges and discharges, with a state of energy (the BESS).
    STORAGE = "storage"
    #: A load that can be shifted or shed inside a declared envelope (cooling thermal flex).
    FLEXIBLE_LOAD = "flexible_load"
    #: A load that may be turned down only when a stated condition holds (a compute envelope).
    CONDITIONAL_LOAD = "conditional_load"
    #: On-site generation (standby sets, PV, co-located AD or EfW).
    GENERATION = "generation"


@dataclass(frozen=True)
class ResourceSpec:
    """A dispatchable resource the engine may be asked to schedule.

    ``availability_contract`` names the frozen contract that carries the resource's availability
    (``"IC-11"`` for thermal flex), or is ``None`` when the `SiteModel` and the assumption release
    carry everything the resource needs.
    """

    resource_id: str
    label: str
    kind: ResourceKind
    description: str
    availability_contract: str | None = None


@dataclass(frozen=True)
class ConstraintSpec:
    """A dispatch constraint a workstream adds from its own package.

    ``describe`` returns the `DispatchConstraintDescriptor` for one site, or ``None`` when the
    constraint does not apply to it. The descriptor must carry this spec's ``constraint_id``. It
    points at the input that parameterises it and holds no fabricated number.

    A registered constraint reaches a dispatch problem only when the caller names its id
    (`gridq.dispatch.dispatch_constraints_for`, ``extension_constraints=``). Registering one never
    changes a run that does not name it.
    """

    constraint_id: str
    kind: str
    description: str
    describe: Callable[[SiteModel, AssumptionLibrary], DispatchConstraintDescriptor | None]
