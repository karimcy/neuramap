"""Provisioning derivation layer (issue 017 stage 2) — pure, deterministic, solver-free.

Turns a `ProvisioningRequirement` plus the metered horizon into the numbers the rest of the
product needs, with **nothing invented**:

- the unmet series — ``unmet_t = max(0, load_t + aux_t + additional − import_cap_t)`` inside
  windows (derived from the trace + the declared addition + the cap; never declared wholesale);
- per-occurrence window statistics and a **worst-case exceedance level** (default
  ``max_observed`` — a single observed year understates the tail, and that is a warned data
  ask, never an extrapolation);
- end-of-tenor floors via the W7-3 closed form, ``level / (1 − fade/100)^N``. Energy floors
  are in the solver's own SoE terms: the dispatch LP books round-trip losses entirely on the
  charge side (``soe += charge·RTE·Δt − discharge·Δt``), so the SoE floor equals the delivered
  window energy with **no discharge-side efficiency division**;
- a recharge-feasibility scan: the battery refills only through the same connection in the
  gaps (``max(0, cap − load − aux)`` grid headroom, converted to SoE at ×RTE), so a running
  prefix balance across the whole horizon must never go negative. This is a **necessary**
  condition (it ignores the battery's own power/energy box, which the sizing floors and the
  dispatch LP enforce) — its failure is a named no-viable reason before any solve.

Calendar semantics are pinned to the solver's `_commitment_mask` (GB local clock, tz-aware
input converted to Europe/London — the F-003/C031-#3 bug class) by a parity test; issue-017
stage 3 should hoist the helper to one shared home when the `load_service` kind lands.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import time as _time

import numpy as np
import pandas as pd

from gridq.assumptions import AssumptionLibrary
from gridq.schemas import AvailabilityWindow, ProvisioningRequirement
from gridq.sizing.deliverability import _maybe, _numeric

DEFAULT_EXCEEDANCE_BASIS = "max_observed"

_FADE_SECTION, _FADE_NAME = "degradation", "annual_capacity_fade_pct"
_RTE_SECTION, _RTE_NAME = "round_trip_efficiency", "ac_round_trip"


@dataclass(frozen=True)
class WindowOccurrence:
    """One contiguous in-window run over the metered horizon."""

    start: int
    stop: int  # exclusive
    label: str  # the occurrence's first local date, ISO
    peak_unmet_kw: float
    unmet_energy_kwh: float


@dataclass(frozen=True)
class ProvisioningFloors:
    """Hard sizing floors the obligation puts under the search domain."""

    power_floor_kw: float
    energy_floor_kwh: float
    exceedance_basis: str
    occurrence_count: int
    worst_peak_kw: float
    worst_energy_kwh: float
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RechargeAssessment:
    """The prefix-balance recharge scan — a necessary condition, checked before any solve."""

    feasible: bool
    worst_margin_kwh: float
    worst_at: str | None  # local timestamp (ISO) where the running balance bottomed out
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProvisioningAssessment:
    """Everything the sizer needs from one requirement, with honest gaps as warnings."""

    requirement_id: str
    assessable: bool
    floors: ProvisioningFloors | None
    recharge: RechargeAssessment | None
    warnings: list[str] = field(default_factory=list)


class ProvisioningInfeasibleError(Exception):
    """A declared obligation is infeasible at ANY battery size (final, not per-candidate).

    Raised by the search layer so the engine can render the D39 no-viable verdict with the
    provisioning reason — the honest recommendation is connection reinforcement.
    """


@dataclass(frozen=True)
class SearchFloors:
    """The hard feasibility floor the site's obligations put under the sizing search (P-C).

    Floors are derived from the COMBINED shortfall across all requirements (overlapping
    windows sum, exactly as the solver compiles them) at the worst-case exceedance level and
    the most conservative (longest) declared tenor's end-of-life fade. ``infeasible_reason``
    is set when any requirement fails the recharge scan — final at any size.
    """

    power_floor_kw: float
    energy_floor_kwh: float
    infeasible_reason: str | None
    warnings: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.power_floor_kw > 0.0 or self.energy_floor_kwh > 0.0

    def admits(self, power_kw: float, energy_kwh: float, tol: float = 1e-6) -> bool:
        """Whether a candidate spec clears the obligation floor."""
        return power_kw >= self.power_floor_kw - tol and energy_kwh >= self.energy_floor_kwh - tol


def search_floors(
    site: object,
    load_kw: np.ndarray,
    timestamps: pd.DatetimeIndex,
    interval_hours: float,
    assumptions: AssumptionLibrary,
) -> SearchFloors:
    """Aggregate every declared requirement into one sizing floor (issue 017 / D42, P-C).

    Mirrors the solver's compile exactly: per-requirement unmet series SUM (overlapping
    obligations reserve jointly, never at max), occurrences over the union mask, worst-case
    exceedance, end-of-tenor uplift at the longest declared tenor. The site's connection
    limit resolves a requirement's absent ``import_cap_kw`` the same way the solver does.
    """
    requirements = getattr(site, "provisioning_requirements", None) or []
    warnings: list[str] = []
    if not requirements:
        return SearchFloors(0.0, 0.0, None, warnings)
    connection = getattr(site, "connection", None)
    conn_candidates = [
        v
        for v in (getattr(connection, "mic_kw", None), getattr(connection, "asc_kw", None))
        if v is not None and v > 0
    ]
    conn_limit = min(conn_candidates) if conn_candidates else None

    n = len(load_kw)
    unmet_total = np.zeros(n, dtype=float)
    union = np.zeros(n, dtype=bool)
    reason: str | None = None
    max_tenor: float | None = None
    for req in requirements:
        cap = req.import_cap_kw if req.import_cap_kw is not None else conn_limit
        if cap is None or cap <= 0:
            warnings.append(
                f"provisioning requirement '{req.requirement_id}' has no usable import cap — "
                "excluded from the sizing floor (data ask; never defaulted)."
            )
            continue
        mask = window_mask(req.windows, timestamps)
        if not mask.any():
            continue
        addition: float | np.ndarray
        if req.additional_load_profile_daily_kw is not None:
            addition = daily_profile_series(req.additional_load_profile_daily_kw, timestamps)
        else:
            addition = req.additional_load_kw or 0.0
        unmet = unmet_series(load_kw, 0.0, addition, cap, mask)
        unmet_total += unmet
        union |= mask
        if req.tenor_years is not None:
            max_tenor = req.tenor_years if max_tenor is None else max(max_tenor, req.tenor_years)
        rte_a = _maybe(assumptions, _RTE_SECTION, _RTE_NAME)
        rte = _numeric(rte_a)
        if rte is not None and 0.0 < rte <= 1.0 and reason is None:
            scan = recharge_scan(load_kw, 0.0, cap, unmet, mask, timestamps, interval_hours, rte)
            if not scan.feasible:
                reason = (
                    f"no viable configuration: provisioning requirement "
                    f"'{req.requirement_id}' cannot recharge through this connection at ANY "
                    f"battery size (running balance bottoms at "
                    f"{scan.worst_margin_kwh:.1f} kWh, {scan.worst_at}) — the honest "
                    "recommendation is connection reinforcement (issue 017 / D39)."
                )

    occs = occurrences(unmet_total, union, timestamps, interval_hours)
    peak, level = exceedance_levels(occs)
    fade_a = _maybe(assumptions, _FADE_SECTION, _FADE_NAME)
    fade = _numeric(fade_a)
    if max_tenor is not None and fade_a is not None and fade is not None and 0 <= fade < 100:
        power_floor, energy_floor = end_of_tenor_floors(peak, level, fade, max_tenor)
    else:
        power_floor, energy_floor = peak, level
        if max_tenor is not None:
            warnings.append(
                "provisioning sizing floor quoted at day-one capability — "
                f"{_FADE_SECTION}.{_FADE_NAME} missing or invalid (data ask)."
            )
    return SearchFloors(float(power_floor), float(energy_floor), reason, warnings)


def window_mask(windows: Sequence[AvailabilityWindow], timestamps: pd.DatetimeIndex) -> np.ndarray:
    """Boolean mask of the windows over the metered horizon.

    Identical semantics to the solver's ``_commitment_mask`` (parity-pinned by test):
    month/day-of-week filters plus the same-day ``[start_time, end_time)`` span, unioned
    across windows; ``end_time == 00:00`` is the exclusive end of the day. Windows are GB
    local-clock content — a tz-aware index is converted to Europe/London before its clock
    fields are read; naive input is read as the local clock it documents.
    """
    idx = timestamps
    if idx.tz is not None:
        idx = idx.tz_convert("Europe/London")
    n = len(idx)
    months = idx.month.to_numpy()
    days = idx.dayofweek.to_numpy()
    times = np.asarray(idx.time)
    total = np.zeros(n, dtype=bool)
    for w in windows:
        m = np.ones(n, dtype=bool)
        if w.months is not None:
            m &= np.isin(months, w.months)
        if w.days_of_week is not None:
            m &= np.isin(days, w.days_of_week)
        m &= times >= w.start_time
        if w.end_time != _time(0, 0):
            m &= times < w.end_time
        total |= m
    return total


def daily_profile_series(profile: Sequence[float], timestamps: pd.DatetimeIndex) -> np.ndarray:
    """Map a manual 48-slot daily curve (D42) onto the horizon by LOCAL half-hour slot.

    Slot 0 = 00:00 local; a tz-aware index is converted to Europe/London first (the same
    local-clock convention as the window mask). Sub-half-hourly indices map each instant to
    its containing slot.
    """
    if len(profile) != 48:
        raise ValueError(f"daily profile must have exactly 48 slots, got {len(profile)}")
    idx = timestamps
    if idx.tz is not None:
        idx = idx.tz_convert("Europe/London")
    slots = idx.hour.to_numpy() * 2 + idx.minute.to_numpy() // 30
    return np.asarray(profile, dtype=float)[slots]


def unmet_series(
    load_kw: np.ndarray,
    aux_kw: float,
    additional_load_kw: float | np.ndarray | None,
    import_cap_kw: float,
    mask: np.ndarray,
) -> np.ndarray:
    """The derived shortfall series (kW): in-window ``max(0, load + aux + add − cap)``.

    ``additional_load_kw`` is a flat kW or a per-instant series (a mapped daily curve).
    Zero outside windows — the requirement asserts nothing there (the site draws what it
    draws; a declared addition exists only inside its windows, v1 semantics per D42). The
    shortfall is physically joint: a declared addition on top of metered load riding near
    the cap is one excess, not two separable ones.
    """
    add = additional_load_kw if additional_load_kw is not None else 0.0
    total = np.asarray(load_kw, dtype=float) + aux_kw + add
    unmet = np.maximum(0.0, total - import_cap_kw)
    return np.where(mask, unmet, 0.0)


def occurrences(
    unmet: np.ndarray,
    mask: np.ndarray,
    timestamps: pd.DatetimeIndex,
    interval_hours: float,
) -> list[WindowOccurrence]:
    """Group the mask's contiguous runs and record each run's peak kW and energy kWh."""
    idx = timestamps
    if idx.tz is not None:
        idx = idx.tz_convert("Europe/London")
    where = np.flatnonzero(mask)
    if where.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(where) > 1)
    starts = np.concatenate(([0], breaks + 1))
    stops = np.concatenate((breaks + 1, [where.size]))
    out: list[WindowOccurrence] = []
    for s, e in zip(starts, stops, strict=True):
        lo, hi = int(where[s]), int(where[e - 1]) + 1
        seg = unmet[lo:hi]
        out.append(
            WindowOccurrence(
                start=lo,
                stop=hi,
                label=idx[lo].date().isoformat(),
                peak_unmet_kw=float(seg.max()) if seg.size else 0.0,
                unmet_energy_kwh=float(seg.sum() * interval_hours),
            )
        )
    return out


def exceedance_levels(
    occs: Sequence[WindowOccurrence], basis: str = DEFAULT_EXCEEDANCE_BASIS
) -> tuple[float, float]:
    """The (peak kW, occurrence energy kWh) level the guarantee is set at.

    ``max_observed`` is the only v1 basis — the conservative choice on a single observed
    year. Anything else is a schema-level decision, not a silent default.
    """
    if basis != DEFAULT_EXCEEDANCE_BASIS:
        raise ValueError(f"unknown exceedance basis '{basis}' (v1 supports 'max_observed')")
    if not occs:
        return (0.0, 0.0)
    return (
        max(o.peak_unmet_kw for o in occs),
        max(o.unmet_energy_kwh for o in occs),
    )


def end_of_tenor_floors(
    peak_kw: float, energy_kwh: float, fade_pct: float, tenor_years: float
) -> tuple[float, float]:
    """Day-one floors that still cover the level at degraded year-N capability (W7-3 form)."""
    retention = (1.0 - fade_pct / 100.0) ** tenor_years
    if retention <= 0.0:
        raise ValueError(f"retention non-positive (fade {fade_pct}%/yr over {tenor_years}y)")
    return (peak_kw / retention, energy_kwh / retention)


def recharge_scan(
    load_kw: np.ndarray,
    aux_kw: float,
    import_cap_kw: float,
    unmet: np.ndarray,
    mask: np.ndarray,
    timestamps: pd.DatetimeIndex,
    interval_hours: float,
    rte: float,
    initial_soe_kwh: float = 0.0,
) -> RechargeAssessment:
    """Running prefix balance: gap headroom in (×RTE, SoE terms) minus window draws.

    In the gaps the battery may store at most ``max(0, cap − load − aux)·Δt·RTE`` per
    interval; inside windows it must supply the unmet energy 1:1 from SoE (the solver books
    losses on the charge side). The running balance starting from ``initial_soe_kwh``
    (default 0 — conservative: no free energy on day one) must never go negative. Necessary,
    not sufficient: the battery's own power/energy box binds tighter and is enforced by the
    sizing floors and the dispatch LP.
    """
    if not 0.0 < rte <= 1.0:
        raise ValueError(f"rte must be in (0, 1], got {rte}")
    headroom = np.maximum(0.0, import_cap_kw - np.asarray(load_kw, dtype=float) - aux_kw)
    gain = np.where(~mask, headroom * interval_hours * rte, 0.0)
    draw = np.where(mask, unmet * interval_hours, 0.0)
    balance = initial_soe_kwh + np.cumsum(gain - draw)
    worst_i = int(np.argmin(balance))
    worst = float(balance[worst_i])
    idx = timestamps
    if idx.tz is not None:
        idx = idx.tz_convert("Europe/London")
    feasible = worst >= 0.0
    notes = [
        "necessary condition only: ignores the battery's own power/energy box (enforced by "
        "the sizing floors and the dispatch LP); a failure here is final at ANY size"
    ]
    return RechargeAssessment(
        feasible=feasible,
        worst_margin_kwh=worst,
        worst_at=idx[worst_i].isoformat() if not feasible else None,
        notes=notes,
    )


def assess_provisioning(
    requirement: ProvisioningRequirement,
    load_kw: np.ndarray,
    aux_kw: float,
    timestamps: pd.DatetimeIndex,
    interval_hours: float,
    assumptions: AssumptionLibrary,
    site_max_import_kw: float | None = None,
) -> ProvisioningAssessment:
    """The orchestrator: requirement + horizon + assumptions → floors, recharge, warnings.

    Honest gaps, never defaults: an unresolvable import cap makes the requirement
    unassessable; a missing fade assumption leaves day-one floors with an end-of-tenor data
    ask; a missing RTE leaves the recharge scan unassessed with a data ask.
    """
    warnings: list[str] = []
    cap = requirement.import_cap_kw or site_max_import_kw
    if cap is None or cap <= 0:
        return ProvisioningAssessment(
            requirement_id=requirement.requirement_id,
            assessable=False,
            floors=None,
            recharge=None,
            warnings=[
                f"provisioning requirement '{requirement.requirement_id}' not assessable: no "
                "import cap — neither import_cap_kw nor the site's max_import_kw is available "
                "(data ask; never defaulted)"
            ],
        )

    mask = window_mask(requirement.windows, timestamps)
    addition: float | np.ndarray | None
    if requirement.additional_load_profile_daily_kw is not None:
        addition = daily_profile_series(requirement.additional_load_profile_daily_kw, timestamps)
    else:
        addition = requirement.additional_load_kw
    unmet = unmet_series(load_kw, aux_kw, addition, cap, mask)
    occs = occurrences(unmet, mask, timestamps, interval_hours)
    peak, energy = exceedance_levels(occs)
    if peak <= 0.0:
        warnings.append(
            f"provisioning requirement '{requirement.requirement_id}': no shortfall observed "
            f"in its windows at cap {cap:g} kW — the obligation demands nothing on this "
            "horizon (floors are zero; verify the declared addition and the cap)"
        )

    notes = [
        f"exceedance basis '{DEFAULT_EXCEEDANCE_BASIS}' over {len(occs)} window occurrence(s) "
        "on a single observed horizon — additional years of data are a ranked data ask, "
        "never extrapolated"
    ]
    fade_a = _maybe(assumptions, _FADE_SECTION, _FADE_NAME)
    fade = _numeric(fade_a)
    tenor = requirement.tenor_years
    if tenor is None:
        power_floor, energy_floor = peak, energy
        notes.append("no tenor declared — floors at day-one capability only")
    elif fade_a is None or fade is None or not 0 <= fade < 100:
        power_floor, energy_floor = peak, energy
        warnings.append(
            f"end-of-tenor floors unassessable: {_FADE_SECTION}.{_FADE_NAME} missing or "
            f"invalid (data ask) — floors quoted at day-one capability, NOT year-{tenor:g}"
        )
        notes.append(f"day-one floors only (fade unassessable, tenor {tenor:g}y declared)")
    else:
        power_floor, energy_floor = end_of_tenor_floors(peak, energy, fade, tenor)
        notes.append(
            f"end-of-tenor floors at year {tenor:g}: fade {fade:g}%/yr "
            f"(status '{fade_a.status.value}')"
        )

    floors = ProvisioningFloors(
        power_floor_kw=power_floor,
        energy_floor_kwh=energy_floor,
        exceedance_basis=DEFAULT_EXCEEDANCE_BASIS,
        occurrence_count=len(occs),
        worst_peak_kw=peak,
        worst_energy_kwh=energy,
        notes=notes,
    )

    rte_a = _maybe(assumptions, _RTE_SECTION, _RTE_NAME)
    rte = _numeric(rte_a)
    recharge: RechargeAssessment | None = None
    if rte is None or not 0.0 < rte <= 1.0:
        warnings.append(
            f"recharge feasibility unassessed: {_RTE_SECTION}.{_RTE_NAME} missing or invalid "
            "(data ask)"
        )
    else:
        recharge = recharge_scan(load_kw, aux_kw, cap, unmet, mask, timestamps, interval_hours, rte)
        if not recharge.feasible:
            warnings.append(
                f"provisioning requirement '{requirement.requirement_id}' is recharge-"
                f"infeasible on this connection at ANY battery size: running balance bottoms "
                f"at {recharge.worst_margin_kwh:.1f} kWh ({recharge.worst_at}) — the honest "
                "recommendation is connection reinforcement (issue 017 §6, D39 verdict)"
            )

    return ProvisioningAssessment(
        requirement_id=requirement.requirement_id,
        assessable=True,
        floors=floors,
        recharge=recharge,
        warnings=warnings,
    )
