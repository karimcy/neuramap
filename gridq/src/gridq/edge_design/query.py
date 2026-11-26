"""Questions later edge packages ask of the released design (D154).

WP-EDGE-03 asks what the edge may write at a stage in a mode (`write_bindings`), WP-EDGE-04 which
conduit to break for each fail-safe trigger (`conduits_for_trigger`), WP-GRC-10 which controls
stand behind an SR (`controls_for_sr`), and the edge itself whether a schedule's chiller band is
inside the site's BMS mode at all (`design_write_scope_reasons`, composed with IC-5's
`check_setpoint_schedule`).
"""

from __future__ import annotations

from dataclasses import dataclass

from gridq.contracts._base import NotApplicable, Unknown
from gridq.contracts.edge_cloud import (
    ControlStage,
    RejectReason,
    SetpointSchedule,
    TriggerCode,
)
from gridq.edge_design.model import (
    CONTROLS,
    BmsConduitMode,
    BmsVariant,
    Conduit,
    ControlKind,
    PointClass,
    WriteScope,
    Zone,
    stage_at_least,
)
from gridq.edge_design.release import DESIGN
from gridq.edge_design.site import max_stage_for

__all__ = [
    "WriteBinding",
    "conduits",
    "conduits_between",
    "conduits_for_trigger",
    "controls_for_sr",
    "design_write_scope_reasons",
    "effective_stage",
    "variant",
    "write_bindings",
    "write_conduits",
    "zone",
]

#: The zones from which a write is Neura's: the edge itself and the adapter it drives.
_NEURA_SIDE = frozenset({"edge_control", "thermaflex_adapter"})


def variant(mode: BmsConduitMode = DESIGN.default_bms_mode) -> BmsVariant:
    return next(v for v in DESIGN.bms_variants if v.mode is mode)


def conduits(mode: BmsConduitMode = DESIGN.default_bms_mode) -> tuple[Conduit, ...]:
    """The conduits in force in a BMS mode: the base conduits plus that mode's."""
    return DESIGN.base_conduits + variant(mode).conduits


def zone(zone_id: str) -> Zone:
    return next(z for z in DESIGN.zones if z.zone_id == zone_id)


def conduits_between(
    a: str, b: str, mode: BmsConduitMode = DESIGN.default_bms_mode
) -> tuple[Conduit, ...]:
    return tuple(c for c in conduits(mode) if set(c.zones) == {a, b})


def write_conduits(mode: BmsConduitMode = DESIGN.default_bms_mode) -> tuple[Conduit, ...]:
    return tuple(c for c in conduits(mode) if isinstance(c.authority, WriteScope))


def conduits_for_trigger(
    code: TriggerCode, mode: BmsConduitMode = DESIGN.default_bms_mode
) -> tuple[Conduit, ...]:
    return tuple(c for c in conduits(mode) if c.loss_trigger == code)


def controls_for_sr(sr_id: str) -> tuple[ControlKind, ...]:
    return tuple(k for k in ControlKind if sr_id in CONTROLS[k].sr_ids)


def effective_stage(granted: ControlStage, mode: BmsConduitMode | Unknown) -> ControlStage:
    """The lower of the stage IC-5 granted and the highest the site's BMS mode permits."""
    ceiling = max_stage_for(mode)
    return granted if stage_at_least(ceiling, granted) else ceiling


@dataclass(frozen=True, order=True)
class WriteBinding:
    conduit_id: str
    target_zone: str
    point_class: PointClass


def write_bindings(
    stage: ControlStage, mode: BmsConduitMode = DESIGN.default_bms_mode
) -> tuple[WriteBinding, ...]:
    """What Neura's side may write at a granted stage in a BMS mode: the point classes of every
    write conduit whose lowest stage the effective stage reaches. WP-EDGE-03's allow-list must
    stay inside this set. The DNO's ANM writes into the edge and are not Neura's."""
    eff = effective_stage(stage, mode)
    out: list[WriteBinding] = []
    for c in conduits(mode):
        a = c.authority
        if not isinstance(a, WriteScope) or c.side(a.writer) not in _NEURA_SIDE:
            continue
        if not stage_at_least(eff, a.min_stage):
            continue
        target = c.side("b" if a.writer == "a" else "a")
        out.extend(WriteBinding(c.conduit_id, target, p) for p in a.point_classes)
    return tuple(sorted(out))


def design_write_scope_reasons(
    schedule: SetpointSchedule, mode: BmsConduitMode | Unknown
) -> tuple[RejectReason, ...]:
    """The design's own refusal, to add to IC-5's verdict: a closed-loop schedule that commands a
    chiller band at a site whose BMS mode has no chiller write path is outside the write scope,
    whatever stage IC-5 granted."""
    closed = schedule.execution == "closed_loop"
    chiller = any(not isinstance(p.chiller_band, NotApplicable) for p in schedule.periods)
    if closed and chiller and not stage_at_least(max_stage_for(mode), ControlStage.L3_CHILLER_BAND):
        return (RejectReason.OUTSIDE_WRITE_SCOPE,)
    return ()
