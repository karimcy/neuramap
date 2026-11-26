"""Chassis extension registries (IC-12 2.0.0, WP-CORE-03).

Four extension points let a workstream add behaviour to the engine from its own package, without
editing a CORE-owned path:

=================  ===================================================  ====================
registry           what is registered                                   built-ins live in
=================  ===================================================  ====================
`load_archetypes`  a `ReferenceProfile` (a normalised half-hourly shape)  `ingestion.hh_synthesis`
`study_bases`      a basis class meeting `schemas.study.NamedBasis`     `schemas.study`
`resources`        a `ResourceSpec` (a dispatchable resource)           this module
`constraints`      a `ConstraintSpec` (a dispatch-constraint describer)  none yet (WP-SIZE-02)
=================  ===================================================  ====================

**How a plugin registers.** A plugin is a module with a ``register(host)`` function. It is found
in one of two ways, and both end in the same call:

1. *Entry point* (the normal route). The plugin's distribution declares, in its own
   ``pyproject.toml``::

       [project.entry-points."gridq.extensions"]
       my_workstream = "my_package.gridq_plugin:register"

   GridQ discovers it on the first registry read that needs it. The registration's owner and
   version are the distribution's name and version, read from its metadata.
2. *Explicit* (tests, notebooks, a workstream package inside this repository that is not a
   distribution of its own): ``load_plugin("my_package.gridq_plugin")``. The owner is the module
   name and the version is the module's ``__version__``, which it must state.

``register(host)`` receives an `ExtensionHost` already bound to that owner and version, so a
plugin cannot state either for itself, and each ``register_*`` call takes the id from the item.

**What a plugin cannot do.** It cannot override an id (see `registry.Registry`), register as
``gridq``, or half-register: if ``register`` raises, everything it added is rolled back and
`ExtensionLoadError` is raised. A failing plugin is never skipped silently.

**Determinism.** Registering changes no run. An item acts only when a run names its id, and a
named id is an input. A study basis enters ``input_hash`` whole; a plugin's basis also pins its
owner and version there (`gridq.quotes.assembly`).
"""

from __future__ import annotations

import importlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from typing import TYPE_CHECKING, Any, Final

import gridq
from gridq.extensions import _validators
from gridq.extensions.registry import (
    CORE_OWNER,
    ID_PATTERN,
    DuplicateRegistration,
    Registration,
    Registry,
    RegistryError,
    UnknownExtension,
)
from gridq.extensions.specs import ConstraintSpec, ResourceKind, ResourceSpec

if TYPE_CHECKING:
    from gridq.ingestion.hh_synthesis import ReferenceProfile
    from gridq.schemas.study import NamedBasis

__all__ = [
    "BATTERY_STORAGE",
    "CORE_OWNER",
    "ENTRY_POINT_GROUP",
    "ID_PATTERN",
    "ConstraintSpec",
    "DuplicateRegistration",
    "ExtensionHost",
    "ExtensionLoadError",
    "LoadedPlugin",
    "Registration",
    "Registry",
    "RegistryError",
    "ResourceKind",
    "ResourceSpec",
    "UnknownExtension",
    "constraints",
    "discover",
    "load_archetypes",
    "load_plugin",
    "loaded_plugins",
    "resources",
    "study_bases",
]

#: The entry-point group a plugin distribution declares its ``register`` function under.
ENTRY_POINT_GROUP: Final = "gridq.extensions"


class ExtensionLoadError(RuntimeError):
    """A plugin could not be loaded, or its ``register`` raised. Never swallowed."""


@dataclass(frozen=True)
class LoadedPlugin:
    """One plugin whose ``register`` has run."""

    hook: str
    owner: str
    version: str
    source: str


_lock = threading.RLock()
_loaded: dict[str, LoadedPlugin] = {}
_discovered = False
_discovering = False


def discover() -> None:
    """Load every plugin declared under `ENTRY_POINT_GROUP`, once per process.

    Called by the registries on the first read that needs it; calling it directly is allowed and
    harmless. Entry points are loaded in (name, value) order. A plugin that fails raises
    `ExtensionLoadError`, and the next read tries again and raises again.
    """
    global _discovered, _discovering
    with _lock:
        if _discovered or _discovering:
            return
        _discovering = True
        try:
            found = sorted(
                metadata.entry_points(group=ENTRY_POINT_GROUP), key=lambda e: (e.name, e.value)
            )
            for ep in found:
                source = f"entry point {ep.name!r} = {ep.value!r}"
                if ep.dist is None:
                    raise ExtensionLoadError(f"{source}: no distribution metadata")
                try:
                    hook = ep.load()
                except Exception as exc:
                    raise ExtensionLoadError(f"{source}: could not be imported: {exc}") from exc
                _run(hook, owner=ep.dist.name, version=ep.dist.version, source=source)
            _discovered = True
        finally:
            _discovering = False


load_archetypes: Registry[ReferenceProfile] = Registry(
    "load_archetypes",
    validate=_validators.check_load_archetype,
    home="gridq.ingestion.hh_synthesis",
    discover=discover,
)
study_bases: Registry[type[NamedBasis]] = Registry(
    "study_bases",
    validate=_validators.check_study_basis,
    home="gridq.schemas.study",
    discover=discover,
)
resources: Registry[ResourceSpec] = Registry(
    "resources", validate=_validators.check_resource, discover=discover
)
constraints: Registry[ConstraintSpec] = Registry(
    "constraints", validate=_validators.check_constraint, discover=discover
)

_ALL: Final[tuple[Registry[Any], ...]] = (load_archetypes, study_bases, resources, constraints)

#: The one resource the engine schedules today, declared so a second resource registers beside it.
BATTERY_STORAGE: Final = resources.register_builtin(
    "battery_storage",
    ResourceSpec(
        resource_id="battery_storage",
        label="Battery energy storage system",
        kind=ResourceKind.STORAGE,
        description=(
            "The behind-the-meter battery the dispatch MILP schedules and the sizing loop sizes."
        ),
    ),
    version=gridq.__version__,
)


@dataclass(frozen=True)
class ExtensionHost:
    """What a plugin's ``register(host)`` is handed: the four registries, bound to its identity."""

    owner: str
    version: str

    def register_load_archetype(self, profile: ReferenceProfile) -> None:
        """Add a load archetype. Its id is ``profile.profile_id``."""
        load_archetypes.register(
            getattr(profile, "profile_id", ""), profile, owner=self.owner, version=self.version
        )

    def register_study_basis(self, basis_class: type[NamedBasis]) -> None:
        """Add a study basis. Its id is the class's ``BASIS_ID``."""
        study_bases.register(
            getattr(basis_class, "BASIS_ID", ""),
            basis_class,
            owner=self.owner,
            version=self.version,
        )

    def register_resource(self, spec: ResourceSpec) -> None:
        """Add a dispatchable resource. Its id is ``spec.resource_id``."""
        resources.register(
            getattr(spec, "resource_id", ""), spec, owner=self.owner, version=self.version
        )

    def register_constraint(self, spec: ConstraintSpec) -> None:
        """Add a dispatch constraint. Its id is ``spec.constraint_id``."""
        constraints.register(
            getattr(spec, "constraint_id", ""), spec, owner=self.owner, version=self.version
        )


def _run(hook: Any, *, owner: str, version: str, source: str) -> LoadedPlugin:
    """Call one plugin's ``register`` with a bound host; roll back and raise if it fails."""
    if not callable(hook):
        raise ExtensionLoadError(f"{source}: the plugin hook is not callable")
    name = f"{getattr(hook, '__module__', '?')}:{getattr(hook, '__qualname__', repr(hook))}"
    with _lock:
        held = _loaded.get(name)
        if held is not None:
            return held
        if not owner.strip() or not version.strip():
            raise ExtensionLoadError(f"{source}: a plugin must state an owner and a version")
        # Built-ins first: a hook's own first registration would otherwise import them after the
        # snapshot, and a rollback would then take them out with the plugin's items.
        for registry in _ALL:
            registry._ensure_home()
        before = [r._snapshot() for r in _ALL]
        try:
            hook(ExtensionHost(owner=owner, version=version))
        except Exception as exc:
            for registry, snapshot in zip(_ALL, before, strict=True):
                registry._restore(snapshot)
            raise ExtensionLoadError(
                f"{source}: register() failed and was rolled back: {exc}"
            ) from exc
        plugin = LoadedPlugin(hook=name, owner=owner, version=version, source=source)
        _loaded[name] = plugin
        return plugin


def load_plugin(module_name: str) -> LoadedPlugin:
    """Load a plugin module explicitly and run its ``register(host)``.

    The owner is ``module_name``; the version is the module's ``__version__``, which it must
    state. Loading the same plugin twice is a no-op that returns the first result.
    """
    source = f"plugin module {module_name!r}"
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise ExtensionLoadError(f"{source}: could not be imported: {exc}") from exc
    hook: Callable[[ExtensionHost], None] | None = getattr(module, "register", None)
    if hook is None:
        raise ExtensionLoadError(f"{source}: it defines no register(host) function")
    version = getattr(module, "__version__", "")
    if not isinstance(version, str) or not version.strip():
        raise ExtensionLoadError(f"{source}: it must state __version__")
    return _run(hook, owner=module_name, version=version, source=source)


def loaded_plugins() -> tuple[LoadedPlugin, ...]:
    """Every plugin loaded so far, ordered by hook name. Does not trigger discovery."""
    with _lock:
        return tuple(_loaded[k] for k in sorted(_loaded))
