"""The released design: one frozen, hashed object (D154).

`DESIGN` gathers the zones, the base conduits, the BMS variants, the control vocabulary, the
invariants, the public sources and the SL 2 review. Building it runs every invariant in every BMS
mode and resolves every element the review names, so a design that breaks a rule, or a review that
points at nothing, cannot be released. Its canonical JSON is committed beside this module and its
hash is pinned as `DESIGN_HASH`; a site instance (`site`) binds to that hash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

from pydantic import model_validator

from gridq.contracts._base import ContractModel, NonEmptyStr, canonical_json, content_hash
from gridq.contracts.edge_cloud import CONTRACT_VERSION as IC5_VERSION
from gridq.edge_design import invariants, reference
from gridq.edge_design.model import (
    CONTROLS,
    BmsConduitMode,
    BmsVariant,
    Conduit,
    ControlKind,
    ControlSpec,
    Zone,
)
from gridq.edge_design.public_sources import PUBLIC_SOURCES, PublicSource
from gridq.edge_design.review import REVIEW, Sl2Review

__all__ = [
    "DESIGN",
    "DESIGN_HASH",
    "DESIGN_VERSION",
    "RELEASE_JSON",
    "ControlEntry",
    "EdgeZoneConduitDesign",
    "InvariantEntry",
    "build_design",
]

DESIGN_VERSION: Final = "1.0.0"
RELEASE_JSON: Final = (
    Path(__file__).resolve().parent / "release" / f"edge_zone_conduit_design.v{DESIGN_VERSION}.json"
)

#: The pinned hash of `DESIGN`. A change to the design is a new version, never a re-pin. (Pinned
#: once more at integration, before any consumer bound to it: the branch's sha256:76ccb848...
#: carried the branch placeholder for decision EDGE02-3 in one invariant statement, now D156;
#: nothing else in the design differs.)
DESIGN_HASH: Final = "sha256:9737bb5425ecf05df93c48bdee89ecb2cf541eeb12019a9302ebd8c5c7ddfb50"


class ControlEntry(ContractModel):
    kind: ControlKind
    spec: ControlSpec


class InvariantEntry(ContractModel):
    name: NonEmptyStr
    statement: NonEmptyStr


class EdgeZoneConduitDesign(ContractModel):
    release: Literal["edge_zone_conduit_design"]
    version: Literal["1.0.0"]
    #: The IC-5 edge-cloud contract version this design was checked against.
    ic5_contract_version: Literal["1.0.0"]
    default_bms_mode: BmsConduitMode
    zones: tuple[Zone, ...]
    base_conduits: tuple[Conduit, ...]
    bms_variants: tuple[BmsVariant, ...]
    controls: tuple[ControlEntry, ...]
    invariants: tuple[InvariantEntry, ...]
    sources: tuple[PublicSource, ...]
    review: Sl2Review

    @model_validator(mode="after")
    def _releasable(self) -> EdgeZoneConduitDesign:
        violations = invariants.check(self.zones, self.base_conduits, self.bms_variants)
        if violations:
            lines = "; ".join(f"{v.invariant} [{v.mode}]: {v.detail}" for v in violations[:6])
            raise ValueError(f"{len(violations)} invariant violation(s): {lines}")
        if [c.kind for c in self.controls] != list(ControlKind):
            raise ValueError("the control table lists every ControlKind once, in enum order")
        if [i.name for i in self.invariants] != list(invariants.INVARIANTS):
            raise ValueError("the invariant table lists every invariant once, in order")
        missing = sorted(e for e in self.element_refs() if not self.resolves(e))
        if missing:
            raise ValueError(f"the review names elements the design lacks: {missing}")
        return self

    def all_conduits(self) -> tuple[Conduit, ...]:
        seen: dict[str, Conduit] = {c.conduit_id: c for c in self.base_conduits}
        for v in self.bms_variants:
            for c in v.conduits:
                seen.setdefault(c.conduit_id, c)
        return tuple(seen.values())

    def element_refs(self) -> frozenset[str]:
        return frozenset(e for s in self.review.srs for e in s.design_elements)

    def resolves(self, element: str) -> bool:
        from gridq.edge_design.site import DESIGN_ASKS

        kind, _, name = element.partition(":")
        if kind == "zone":
            return any(z.zone_id == name for z in self.zones)
        if kind == "conduit":
            return any(c.conduit_id == name for c in self.all_conduits())
        if kind == "control":
            return name in {k.value for k in ControlKind}
        if kind == "invariant":
            return name in invariants.INVARIANTS
        if kind == "ask":
            return name in {a.ask_id for a in DESIGN_ASKS}
        return False


def build_design() -> EdgeZoneConduitDesign:
    if IC5_VERSION != "1.0.0":
        raise RuntimeError(f"checked against IC-5 1.0.0, found {IC5_VERSION}: review the design")
    return EdgeZoneConduitDesign(
        release="edge_zone_conduit_design",
        version="1.0.0",
        ic5_contract_version="1.0.0",
        default_bms_mode=reference.DEFAULT_BMS_MODE,
        zones=reference.ZONES,
        base_conduits=reference.BASE_CONDUITS,
        bms_variants=reference.BMS_VARIANTS,
        controls=tuple(ControlEntry(kind=k, spec=CONTROLS[k]) for k in ControlKind),
        invariants=tuple(
            InvariantEntry(name=name, statement=" ".join(statement.split()))
            for name, (statement, _) in invariants.INVARIANTS.items()
        ),
        sources=tuple(PUBLIC_SOURCES.values()),
        review=REVIEW,
    )


DESIGN: Final = build_design()


def release_text(design: EdgeZoneConduitDesign = DESIGN) -> str:
    """The committed release file: the canonical JSON plus a final newline."""
    return canonical_json(design) + "\n"


def design_hash(design: EdgeZoneConduitDesign = DESIGN) -> str:
    return content_hash(design)
