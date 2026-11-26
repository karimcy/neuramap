"""Thermal flex in the adequacy simulator, read only through IC-11 (D170).

For each restriction event, `plan_flex` decides the one call a dispatcher could make on the
surface's own terms, and the kernel then applies it minute by minute:

- no ``response`` terms (``Unknown``) means no flex: the notice and ramp are not known;
- an IC-2 ``notice_s`` of ``Unknown`` is treated as zero notice (the safe side);
- the call starts once the surface's minimum notice has run, ``s_f = start + max(0, notice_min x
  60 - notice_s)``, on the next whole minute; if that is not before the event end, nothing;
- the start cell decides the curve (on the GB clock of the scenario instant); a ``zero_flex``,
  ``unavailable`` or ``unknown`` start cell gives nothing, and the hold is cut at the first
  ``unavailable`` or ``unknown`` cell it would cross and at the surface's ``valid_until``;
- a hold no longer than the curve's longest point takes `deliverable_kw` for it; a longer event
  takes the longest point for that point's duration (firm, never extrapolated);
- the kW ramps linearly over ``ramp_min``, sampled at each step start (minute 0 delivers 0);
- on release the rebound is added as load: ``peak_kw`` for ``ceil(energy_kwh x 60 / peak_kw)``
  minutes (the peak-first block, which front-loads the stated energy), and no new call starts
  until ``recovery_min`` (or the block, if longer) has run;
- in the kernel the delivered kW is clamped per draw to the cooling share ``facility - IT``.

Validity is tested at the *study* instant (the scenario instant shifted by the load anchor), since
the surface describes the plant over the load model's period.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil
from typing import Final, Literal

import numpy as np

from gridq.adequacy._common import NonNegInt
from gridq.contracts._base import ContractModel, NonEmptyStr, NotApplicable, not_applicable
from gridq.contracts.curtailment import RestrictionEvent
from gridq.contracts.thermal_flex import (
    AvailabilityStatus,
    Flex,
    ResponseTerms,
    ThermalFlexAvailability,
    deliverable_kw,
    query_at,
)

__all__ = ["FlexCall", "flex_arrays", "plan_flex"]

NoCallReason = Literal[
    "response_terms_unknown",
    "notice_too_short",
    "outside_validity",
    "in_recovery",
    "start_cell_zero_flex",
    "start_cell_unavailable",
    "start_cell_unknown",
    "hold_truncated_to_zero",
]

_STATUS_REASON: Final[dict[AvailabilityStatus, NoCallReason]] = {
    AvailabilityStatus.ZERO_FLEX: "start_cell_zero_flex",
    AvailabilityStatus.UNAVAILABLE: "start_cell_unavailable",
    AvailabilityStatus.UNKNOWN: "start_cell_unknown",
}


class FlexCall(ContractModel):
    """The thermal call planned for one event. Minutes count from the scenario's period start."""

    event_index: NonNegInt
    outcome: Literal["called", "none"]
    reason: NoCallReason | NotApplicable
    notice_unknown_treated_as_zero: bool
    start_minute: NonNegInt
    hold_min: NonNegInt
    reduction_kw: NonNegInt
    ramp_min: NonNegInt
    rebound_peak_kw: NonNegInt
    rebound_block_min: NonNegInt
    recovery_end_minute: NonNegInt
    season_id: NonEmptyStr | NotApplicable
    band_id: NonEmptyStr | NotApplicable


def _none(i: int, reason: NoCallReason, unknown_notice: bool, start_minute: int = 0) -> FlexCall:
    na = not_applicable("no call")
    return FlexCall(
        event_index=i,
        outcome="none",
        reason=reason,
        notice_unknown_treated_as_zero=unknown_notice,
        start_minute=start_minute,
        hold_min=0,
        reduction_kw=0,
        ramp_min=0,
        rebound_peak_kw=0,
        rebound_block_min=0,
        recovery_end_minute=0,
        season_id=na,
        band_id=na,
    )


def plan_flex(
    surface: ThermalFlexAvailability,
    event: RestrictionEvent,
    *,
    event_index: int,
    period_start: datetime,
    shift: timedelta,
    recovery_until_minute: int,
) -> FlexCall:
    """The call for one event, given that a previous call's recovery runs until
    ``recovery_until_minute``. Pure."""
    unknown_notice = not isinstance(event.notice_s, float)
    if not isinstance(surface.response, ResponseTerms):
        return _none(event_index, "response_terms_unknown", unknown_notice)
    notice = event.notice_s if isinstance(event.notice_s, float) else 0.0
    start_s = (event.start - period_start).total_seconds()
    end_s = (event.end - period_start).total_seconds()
    s_f = start_s + max(0.0, surface.response.min_notice_min * 60 - notice)
    k_f = ceil(s_f / 60)
    k_e = ceil(end_s / 60)
    if k_f >= k_e:
        return _none(event_index, "notice_too_short", unknown_notice)
    at = period_start + timedelta(minutes=k_f)
    study = at + shift
    if not (surface.valid_from <= study < surface.valid_until):
        return _none(event_index, "outside_validity", unknown_notice, k_f)
    if k_f < recovery_until_minute:
        return _none(event_index, "in_recovery", unknown_notice, k_f)
    answer = query_at(surface, at, 1)
    if answer.status is not AvailabilityStatus.FLEX:
        return _none(event_index, _STATUS_REASON[answer.status], unknown_notice, k_f)
    cell = next(
        c for c in surface.cells if (c.season_id, c.band_id) == (answer.season_id, answer.band_id)
    )
    flex = cell.availability
    assert isinstance(flex, Flex)
    d = min(k_e - k_f, int((surface.valid_until - study).total_seconds() // 60))
    for j in range(1, d):
        status = query_at(surface, at + timedelta(minutes=j), 1).status
        if status in (AvailabilityStatus.UNAVAILABLE, AvailabilityStatus.UNKNOWN):
            d = j
            break
    if d < 1:
        return _none(event_index, "hold_truncated_to_zero", unknown_notice, k_f)
    longest = flex.curve[-1]
    if d <= longest.duration_min:
        kw, rebound = deliverable_kw(flex, d)
        assert rebound is not None
        hold = d
    else:
        kw, rebound, hold = longest.reduction_kw, longest.rebound, longest.duration_min
    block = ceil(rebound.energy_kwh * 60 / rebound.peak_kw) if rebound.energy_kwh else 0
    release = k_f + hold
    return FlexCall(
        event_index=event_index,
        outcome="called",
        reason=not_applicable("a call is made"),
        notice_unknown_treated_as_zero=unknown_notice,
        start_minute=k_f,
        hold_min=hold,
        reduction_kw=kw,
        ramp_min=surface.response.ramp_min,
        rebound_peak_kw=rebound.peak_kw if block else 0,
        rebound_block_min=block,
        recovery_end_minute=release + max(rebound.recovery_min, block),
        season_id=answer.season_id,
        band_id=answer.band_id,
    )


def flex_arrays(calls: tuple[FlexCall, ...], n_minutes: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-minute planned reduction and rebound, integer watts (before the per-draw clamp)."""
    flex = np.zeros(n_minutes, dtype=np.int64)
    rebound = np.zeros(n_minutes, dtype=np.int64)
    for c in calls:
        if c.outcome != "called":
            continue
        j = np.arange(c.hold_min, dtype=np.int64)
        w = c.reduction_kw * 1000
        ramp = np.minimum(w, (w * j) // c.ramp_min) if c.ramp_min else np.full_like(j, w)
        lo, hi = c.start_minute, min(n_minutes, c.start_minute + c.hold_min)
        if hi > lo:
            flex[lo:hi] = ramp[: hi - lo]
        r0 = c.start_minute + c.hold_min
        r1 = min(n_minutes, r0 + c.rebound_block_min)
        if r1 > r0:
            rebound[r0:r1] += c.rebound_peak_kw * 1000
    return flex, rebound
