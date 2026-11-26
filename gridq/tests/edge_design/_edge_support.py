"""Shared helpers for the WP-EDGE-02 zone and conduit design tests."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONTRACT_TESTS = ROOT / "tests" / "contracts"
if str(CONTRACT_TESTS) not in sys.path:
    sys.path.insert(0, str(CONTRACT_TESTS))

from _contracts_support import run_mutant as run_mutant  # noqa: E402

from gridq.edge_design import invariants, reference  # noqa: E402
from gridq.edge_design.model import BmsConduitMode, Conduit, Zone  # noqa: E402

DEFAULT = reference.DEFAULT_BMS_MODE


def variant_of(mode: BmsConduitMode):  # type: ignore[no-untyped-def]
    return next(v for v in reference.BMS_VARIANTS if v.mode is mode)


def view(
    mode: BmsConduitMode = DEFAULT,
    *,
    zones: Iterable[Zone] | None = None,
    base: Iterable[Conduit] | None = None,
    extra: Iterable[Conduit] = (),
) -> invariants.ModeView:
    """A mode's view, optionally with replaced zones or base conduits and extra conduits."""
    var = variant_of(mode)
    var = var.model_copy(update={"conduits": tuple(var.conduits) + tuple(extra)})
    return invariants.mode_view(
        tuple(reference.ZONES if zones is None else zones),
        tuple(reference.BASE_CONDUITS if base is None else base),
        var,
    )


def conduit(cid: str) -> Conduit:
    for c in (*reference.BASE_CONDUITS, *(c for v in reference.BMS_VARIANTS for c in v.conduits)):
        if c.conduit_id == cid:
            return c
    raise KeyError(cid)


def with_conduit(cid: str, **update: Any) -> tuple[Conduit, ...]:
    """The base conduits with one replaced, unvalidated (so only the invariant can object)."""
    return tuple(
        c.model_copy(update=update) if c.conduit_id == cid else c for c in reference.BASE_CONDUITS
    )


def with_zone(zone_id: str, **update: Any) -> tuple[Zone, ...]:
    return tuple(
        z.model_copy(update=update) if z.zone_id == zone_id else z for z in reference.ZONES
    )


def fired(name: str, v: invariants.ModeView) -> list[str]:
    return list(invariants.INVARIANTS[name][1](v))


def conduit_json(c: Conduit) -> dict[str, Any]:
    return json.loads(c.model_dump_json())
