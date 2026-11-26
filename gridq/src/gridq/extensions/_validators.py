"""The typed check each extension point applies before it accepts an item.

Kept apart from `registry` so that module stays free of GridQ imports. Every chassis import here
is inside a function: these run at registration time, never at import time.
"""

from __future__ import annotations

from typing import Any

from gridq.extensions.registry import RegistryError
from gridq.extensions.specs import ConstraintSpec, ResourceKind, ResourceSpec


def check_load_archetype(key: str, item: Any) -> None:
    from gridq.ingestion.hh_synthesis import ReferenceProfile

    if not isinstance(item, ReferenceProfile):
        raise RegistryError(
            f"load archetype {key!r} must be a gridq.ingestion.hh_synthesis.ReferenceProfile"
        )
    if item.profile_id != key:
        raise RegistryError(
            f"load archetype {key!r} carries profile_id {item.profile_id!r}; the two must agree"
        )
    if not item.label.strip() or not item.source_ref.strip():
        raise RegistryError(f"load archetype {key!r} must state a label and a source_ref")


def check_study_basis(key: str, item: Any) -> None:
    from gridq.schemas.study import basis_class_problems

    problems = basis_class_problems(item, expected_id=key)
    if not problems:
        from gridq.extensions import study_bases

        taken = {r.item.LABEL: r.key for r in study_bases._snapshot().values() if r.key != key}
        if item.LABEL in taken:
            problems.append(f"its LABEL is the banner of {taken[item.LABEL]!r} already")
    if problems:
        raise RegistryError(f"study basis {key!r} is refused: " + "; ".join(problems))


def check_resource(key: str, item: Any) -> None:
    if not isinstance(item, ResourceSpec):
        raise RegistryError(f"resource {key!r} must be a gridq.extensions.ResourceSpec")
    if item.resource_id != key:
        raise RegistryError(
            f"resource {key!r} carries resource_id {item.resource_id!r}; the two must agree"
        )
    if not isinstance(item.kind, ResourceKind):
        raise RegistryError(f"resource {key!r}: kind must be a ResourceKind")
    if not item.label.strip() or not item.description.strip():
        raise RegistryError(f"resource {key!r} must state a label and a description")


def check_constraint(key: str, item: Any) -> None:
    if not isinstance(item, ConstraintSpec):
        raise RegistryError(f"constraint {key!r} must be a gridq.extensions.ConstraintSpec")
    if item.constraint_id != key:
        raise RegistryError(
            f"constraint {key!r} carries constraint_id {item.constraint_id!r}; the two must agree"
        )
    if not item.kind.strip() or not item.description.strip():
        raise RegistryError(f"constraint {key!r} must state a kind and a description")
    if not callable(item.describe):
        raise RegistryError(f"constraint {key!r}: describe must be callable")
