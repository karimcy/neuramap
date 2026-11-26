"""The generic, closed-for-override registry behind every chassis extension point.

One class, `Registry`, holds the items of one extension point (load archetypes, study bases,
resources, constraints). The rules are the same for all four, and each is there for determinism:

- **An id is registered once.** A second registration under an id that exists is refused, so a
  plugin can add behaviour but can never replace a built-in, or another plugin. What an id means
  cannot depend on which packages happen to be installed.
- **Every registration states an owner and a version.** Built-ins are owned by `CORE_OWNER`; a
  plugin cannot claim that owner. Where a run consumes a plugin's item, the owner and version go
  into the run's identity beside the id (`gridq.quotes.assembly`).
- **Reads are ordered by id,** never by registration order, so import order and entry-point order
  cannot reach an output.
- **Nothing is read from the registry at import time.** Built-ins are registered by the module
  that defines them (the registry's ``home``), which the registry imports on first use. Plugins
  are discovered on the first read that needs them (see `gridq.extensions.discover`).

This module imports nothing from GridQ. The typed checks for each extension point live in
`gridq.extensions._validators`, which imports the chassis lazily.
"""

from __future__ import annotations

import importlib
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Generic, TypeVar

__all__ = [
    "CORE_OWNER",
    "ID_PATTERN",
    "DuplicateRegistration",
    "Registration",
    "Registry",
    "RegistryError",
    "UnknownExtension",
]

T = TypeVar("T")

#: The owner of every built-in. A plugin may not register under it.
CORE_OWNER: Final = "gridq"

#: An extension id: lower-case ASCII, digits and underscores, starting with a letter. The same
#: shape as the ids the chassis already persists (``all_streams_enabled_study_v1``).
ID_PATTERN: Final = r"^[a-z][a-z0-9_]*$"
_ID = re.compile(ID_PATTERN)


class RegistryError(ValueError):
    """A registration was refused."""


class DuplicateRegistration(RegistryError):
    """The id is already registered. An id is never overridden."""


class UnknownExtension(KeyError):
    """No item is registered under the id, after plugin discovery has run."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


@dataclass(frozen=True)
class Registration(Generic[T]):
    """One registered item, with who registered it and at what version."""

    key: str
    item: T
    owner: str
    version: str

    @property
    def builtin(self) -> bool:
        return self.owner == CORE_OWNER


def _no_discovery() -> None:
    return None


class Registry(Generic[T]):
    """The items of one extension point, keyed by id."""

    def __init__(
        self,
        name: str,
        *,
        validate: Callable[[str, T], None],
        home: str | None = None,
        discover: Callable[[], None] = _no_discovery,
    ) -> None:
        self.name = name
        self._validate = validate
        self._home = home
        self._home_loaded = home is None
        self._discover = discover
        self._items: dict[str, Registration[T]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ writing

    def _ensure_home(self) -> None:
        """Import the module that registers this point's built-ins, once."""
        if not self._home_loaded:
            self._home_loaded = True
            assert self._home is not None
            importlib.import_module(self._home)

    def _add(self, key: str, item: T, owner: str, version: str) -> T:
        if not isinstance(key, str) or not _ID.fullmatch(key):
            raise RegistryError(
                f"{self.name}: {key!r} is not a valid extension id (pattern {ID_PATTERN})"
            )
        if not owner.strip() or not version.strip():
            raise RegistryError(f"{self.name}: {key!r} must state an owner and a version")
        self._validate(key, item)
        with self._lock:
            held = self._items.get(key)
            if held is not None:
                raise DuplicateRegistration(
                    f"{self.name}: {key!r} is already registered by {held.owner} "
                    f"{held.version}; an id is never overridden"
                )
            self._items[key] = Registration(key=key, item=item, owner=owner, version=version)
        return item

    def register_builtin(self, key: str, item: T, *, version: str) -> T:
        """Register a CORE built-in. Called only by this point's ``home`` module."""
        return self._add(key, item, CORE_OWNER, version)

    def register(self, key: str, item: T, *, owner: str, version: str) -> T:
        """Register a plugin's item. Refused if the id exists or the owner is `CORE_OWNER`."""
        if owner == CORE_OWNER:
            raise RegistryError(
                f"{self.name}: {key!r} cannot be registered as {CORE_OWNER!r}; that owner is "
                "reserved for built-ins"
            )
        self._ensure_home()
        return self._add(key, item, owner, version)

    # ------------------------------------------------------------------ reading

    def registration(self, key: str) -> Registration[T]:
        """The registration under ``key``. Runs plugin discovery before giving up."""
        self._ensure_home()
        found = self._items.get(key)
        if found is None:
            self._discover()
            found = self._items.get(key)
        if found is None:
            raise UnknownExtension(
                f"{self.name}: nothing is registered as {key!r}; registered: {list(self.ids())}"
            )
        return found

    def get(self, key: str) -> T:
        return self.registration(key).item

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        try:
            self.registration(key)
        except UnknownExtension:
            return False
        return True

    def registrations(self) -> tuple[Registration[T], ...]:
        """Every registration, built-in and plugin, ordered by id."""
        self._ensure_home()
        self._discover()
        return tuple(self._items[k] for k in sorted(self._items))

    def ids(self) -> tuple[str, ...]:
        return tuple(r.key for r in self.registrations())

    def __len__(self) -> int:
        return len(self.registrations())

    # ------------------------------------------------------------------ test support

    def _snapshot(self) -> dict[str, Registration[T]]:
        """The current registrations. For test isolation only (`_restore`)."""
        return dict(self._items)

    def _restore(self, snapshot: dict[str, Registration[T]]) -> None:
        """Put back a `_snapshot`. For test isolation only; no production caller."""
        with self._lock:
            self._items = dict(snapshot)
