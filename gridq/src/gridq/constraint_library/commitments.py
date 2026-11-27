"""Commitments: typed availability commitments compiled into window constraints (W7-1/W7-2, C026).

Constraints ``commitment_availability_window`` (ONE aggregate row: net injection plus the summed
committed kW fits inside the deliverable envelope, so no kW is double-sold) and
``commitment_held_state_of_energy`` (``soe_t ≥ soe_floor + held/η_dis`` through the windows, B-01 +
D-169). The kind registry below says how every descriptor kind on the dispatch seam is treated.

Descriptors arrive structurally (`CommitmentDescriptor`): the library reads ``constraint_id``,
``kind`` and the typed ``parameters`` payload (an IC-12 `AvailabilityCommitment`) and nothing else,
so it never imports the value-stack code that builds them. Moved verbatim from
``gridq.optimisation.milp_solver`` (WP-SIZE-02).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import time as _time
from typing import Any, Protocol

import numpy as np
import pandas as pd

from gridq.schemas import AvailabilityCommitment

# The kind→treatment registry (W7-2, C026): every DispatchConstraintDescriptor kind arriving on
# the ``problem.constraints`` seam is one of —
# - STRUCTURAL: enforced by the model's own bound/constraint construction (the charge upper bound
#   encodes min(MIC, ASC) − load; the warranty throughput cap is the dense annual row);
# - COMPILED: built into window constraints here when the descriptor carries a typed
#   `AvailabilityCommitment` payload. A compilable kind with **no payload** is *declared, not
#   contracted* — today's universal state until the W7-4 producer exists — and changes nothing
#   (existing solves stay byte-identical; commitments only ever activate additively);
# - DEFERRED: not a dispatch-time constraint (deliverability is the sizing lane's closed-form
#   end-of-tenor check, W7-3).
# An unknown kind warns loudly: the seam is the single audit path, nothing is silently dropped.
# `load_service` (issue 017 / D42) is compiled from `SiteModel.provisioning_requirements`
# directly — the site is the contract carrier — not via the descriptor seam.
# `export_limit` (issue 018 / D43) is STRUCTURAL: the MEC bound on the overlay `export` variable
# is built into the model's own bound construction, exactly like the MIC/ASC charge bound — not
# compiled off this seam (behaviour-neutral: DEFERRED and STRUCTURAL both `continue` below).
STRUCTURAL_KINDS = frozenset({"connection_limit", "throughput_cap", "export_limit"})
COMPILED_KINDS = frozenset({"availability_window", "held_state_of_energy"})
DEFERRED_KINDS = frozenset({"deliverability"})


class CommitmentDescriptor(Protocol):
    """What the compiler reads off a dispatch-seam descriptor (structural, no import)."""

    @property
    def constraint_id(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def parameters(self) -> AvailabilityCommitment | None: ...


def commitment_mask(commitment: AvailabilityCommitment, timestamps: pd.DatetimeIndex) -> np.ndarray:
    """The boolean half-hour mask of a commitment's windows over the metered horizon.

    Deterministic calendar mapping — month/day-of-week filters plus the same-day
    ``[start_time, end_time)`` span, unioned across the commitment's windows. Times compare
    against the metered timestamps exactly; nothing is interpolated or invented.

    Contract windows are GB local-clock content, exactly like the DUoS bands: a timezone-aware
    (e.g. UTC-labelled) metered index is converted to Europe/London before its clock fields are
    read, mirroring ``tariff_calendar._hours_weekday`` (F-003) — otherwise a 17:00 contracted
    window would sit at 18:00 BST all summer on a legal-obligation surface. Naive input is
    read as the local clock it documents.
    """
    idx = timestamps
    if idx.tz is not None:
        idx = idx.tz_convert("Europe/London")
    n = len(idx)
    months = idx.month.to_numpy()
    days = idx.dayofweek.to_numpy()
    times = np.asarray(idx.time)
    total = np.zeros(n, dtype=bool)

    for w in commitment.windows:
        m = np.ones(n, dtype=bool)
        if w.months is not None:
            m &= np.isin(months, w.months)
        if w.days_of_week is not None:
            m &= np.isin(days, w.days_of_week)
        m &= times >= w.start_time
        # end_time == 00:00 means the exclusive end of the day (24:00, W7-5): no upper bound.
        if w.end_time != _time(0, 0):
            m &= times < w.end_time
        total |= m
    return total


def deliverable_envelope_kw(power: float, aux_kw: float, load: np.ndarray) -> np.ndarray:
    """The per-instant deliverable net-injection envelope a reservation is held against (W7-1).

    D57 (Olly, 2026-08-18): the SITE-LOAD CAP IS REMOVED. Export consent, the export MPAN and
    G99/G100 are assumed approved, so a response is deliverable as net injection past the meter and
    the envelope is the battery's own capability net of its parasitic draw. The reservation is still
    subtracted from net injection through the award's windows, so no kW is double-sold, and an
    over-committed instant is still warned and returns INFEASIBLE rather than being relaxed.
    """
    return np.clip(power - aux_kw, 0.0, None) * np.ones_like(load)


def compile_commitments(
    descriptors: Iterable[CommitmentDescriptor],
    model: Any,  # linopy.Model (untyped upstream)
    charge: Any,
    discharge: Any,
    soe: Any,
    timestamps: pd.DatetimeIndex,
    coords: pd.RangeIndex,
    power: float,
    deliverable_energy: float,
    envelope: np.ndarray,
    soe_floor_kwh: float,
    eta_dis: float,
    pre_reserved_kw: np.ndarray | None = None,
    export: Any = None,
) -> tuple[list[tuple[AvailabilityCommitment, np.ndarray, str]], list[str], np.ndarray]:
    """The kind→builder table (W7-2): compile seam descriptors into model constraints.

    For each descriptor whose kind is compilable AND that carries a typed
    `AvailabilityCommitment` payload (C026):

    - ``availability_window`` → reserved **net-injection headroom** through the windows,
      enforced as ONE aggregate constraint against the per-instant deliverable ``envelope``:
      ``discharge_t − charge_t ≤ envelope_t − Σ committed_t`` — overlapping commitments
      reserve the SUM of their kW (never double-sold), curtailing a standing charge counts
      toward the response, and an undeliverable/jointly-infeasible window is warned, never
      silently relaxed;
    - ``held_state_of_energy`` → ``soe_t ≥ soe_floor + held_energy_kwh ÷ η_dis`` through the
      windows. **B-01 + D-169**: ``held_energy_kwh`` is contracted energy DELIVERABLE at the AC
      terminals, so the stored energy the constraint must hold is the reserve below the usable
      floor plus the AC requirement grossed up for discharge losses. A commitment whose
      ``held_energy_kwh`` is ``None`` holds nothing (no explicit hold was contracted — never
      invented). Overlapping holds bind at the max floor (per-commitment constraints).

    Returns ``(compiled triples, warnings, committed kW per instant)`` for post-solve
    accounting. An infeasible commitment is compiled anyway and warned — the solve honestly
    returns INFEASIBLE rather than silently relaxing a contract.
    """
    import xarray as xr

    compiled: list[tuple[AvailabilityCommitment, np.ndarray, str]] = []
    warnings: list[str] = []
    # Load-service obligations pre-reserve capability (issue 017 / D42): grid-side
    # commitments see only the residual envelope — reservations SUM, the site's own
    # obligation is never double-sold to a tender.
    committed = (
        pre_reserved_kw.astype(float).copy()
        if pre_reserved_kw is not None
        else np.zeros(len(envelope), dtype=float)
    )
    # name -> the commitment compiled under it: a re-listed identical (kind, commitment)
    # skips silently; a DIFFERENT commitment whose sanitised id collides gets a uniquified
    # constraint name + a warning — a contracted obligation is never silently dropped.
    seen: dict[str, AvailabilityCommitment] = {}
    # LIT-E-01/LIT-E-02 (2026-08-13): every compilable descriptor that arrives WITHOUT a typed
    # payload, in seam order. Collected rather than skipped silently — see the warning below.
    declared_only: list[str] = []
    for d in list(descriptors):
        if d.kind in STRUCTURAL_KINDS or d.kind in DEFERRED_KINDS:
            continue  # enforced by the model's own construction / not a dispatch-time constraint
        if d.kind not in COMPILED_KINDS:
            warnings.append(
                f"dispatch constraint kind '{d.kind}' (id '{d.constraint_id}') is not "
                "recognised by the solver's kind registry — NOT compiled; extend the "
                "registry before relying on it."
            )
            continue
        c = d.parameters
        if c is None:
            # Declared, not contracted — nothing to compile (see registry note). NOT silent:
            # D45's discipline is that an ABSENT input must never read as a satisfied
            # constraint, and a payload-less descriptor is exactly that shape. Recorded here
            # and warned once below; the model itself is untouched (byte-identical).
            declared_only.append(f"{d.kind}:{d.constraint_id}")
            continue
        name = f"{d.kind}_{re.sub(r'[^0-9A-Za-z_]', '_', c.commitment_id)}"
        if name in seen:
            if seen[name] == c:
                continue  # identical (kind, commitment) already compiled
            warnings.append(
                f"distinct commitments collide on sanitised constraint name '{name}' "
                f"('{seen[name].commitment_id}' vs '{c.commitment_id}') — both compiled "
                "under uniquified names; give commitments distinct ids."
            )
            name = f"{name}__{len(seen)}"
        mask = commitment_mask(c, timestamps)
        if not mask.any():
            warnings.append(
                f"availability commitment '{c.commitment_id}' has windows that never "
                "intersect the metered horizon — vacuous over this dataset (no constraint "
                "added; verify the contract calendar against the metered period)."
            )
            continue
        seen[name] = c
        mask_da = xr.DataArray(mask, coords=[coords])
        if d.kind == "availability_window":
            # A commitment above the largest in-window envelope is warned: it cannot be
            # delivered as net injection from this dataset's physics.
            max_in_window = float(np.max(envelope[mask])) if mask.any() else 0.0
            if c.committed_power_kw > max_in_window + 1e-9:
                warnings.append(
                    f"commitment '{c.commitment_id}' commits {c.committed_power_kw} kW, "
                    f"above the largest in-window deliverable response "
                    f"{round(max_in_window, 3)} kW (min of rated-net-of-parasitic power "
                    "and the metered site load on the no-export floor) — infeasible over "
                    "at least part of the window (never silently relaxed)."
                )
            committed[mask] += float(c.committed_power_kw)
        else:  # held_state_of_energy
            if c.held_energy_kwh is None:
                continue  # no explicit hold contracted — never invented
            if c.held_energy_kwh > deliverable_energy + 1e-9:
                warnings.append(
                    f"commitment '{c.commitment_id}' holds {c.held_energy_kwh} kWh, above the "
                    f"{round(deliverable_energy, 3)} kWh this pack can DELIVER at the AC "
                    "terminals (usable SoC window net of discharge losses, D-169/B-01) — "
                    "infeasible by construction (never silently relaxed)."
                )
            # B-01 + D-169: hold the STORED energy the AC obligation actually costs.
            model.add_constraints(
                soe >= soe_floor_kwh + c.held_energy_kwh / eta_dis, name=name, mask=mask_da
            )
        compiled.append((c, mask, d.kind))
    # LIT-E-01/LIT-E-02 (2026-08-13) + C031 item 2. Nothing in `src/` constructs an
    # `AvailabilityCommitment` yet: every `gb_streams` descriptor carries a `parameter_source`
    # string and no payload, so on a production run the loop above compiles NOTHING. That
    # invisibility is the defect: an availability commitment with no compiled reservation means the
    # same kW and kWh back the dispatch AND the obligation at once, inside the priced floor. One
    # warning, listing every affected descriptor in seam order (deterministic). Behaviour is
    # byte-identical — the model is untouched and no £ moves.
    if declared_only:
        warnings.append(
            "NO AVAILABILITY RESERVATION COMPILED (LIT-E-01/LIT-E-02, C031 item 2) for "
            f"{len(declared_only)} declared dispatch commitment descriptor(s): "
            f"{', '.join(declared_only)}. Each is DECLARED (it names a parameter_source) but "
            "carries no typed AvailabilityCommitment payload, so NO kW of power and NO kWh of "
            "stored energy is held back for it in this solve. Any availability/capacity "
            "revenue priced off these descriptors is sold against capability the dispatch is "
            "simultaneously free to spend, which OVERSTATES the priced floor and the DSCR that "
            "covenant is measured on. Deliverability is NOT certified by this run. Supply the "
            "commitment payload (committed_power_kw, optional held_energy_kwh, windows, tenor, "
            "provenance) on the descriptor and the reservation compiles and binds."
        )
    # ONE aggregate availability constraint (adoption review W7-2): at every instant the
    # SUM of committed kW across overlapping windows must fit inside the deliverable
    # envelope — two 150 kW awards on one battery reserve 300 kW, never max(150, 150).
    agg_mask = committed > 0
    if agg_mask.any():
        over = agg_mask & (committed > envelope + 1e-9)
        if over.any():
            warnings.append(
                f"overlapping commitments jointly reserve up to "
                f"{round(float(np.max(committed[over])), 3)} kW where the deliverable "
                f"envelope is at most {round(float(np.min(envelope[over])), 3)} kW "
                f"({int(over.sum())} instant(s)) — jointly infeasible (never silently "
                "relaxed)."
            )
        # Net injection reserved against the envelope. Export (issue 018) is a real
        # net-injection flow, so it joins discharge − charge here; ``net_injection`` is exactly
        # ``discharge − charge`` when no export variable exists (byte-identical).
        net_injection = (discharge + export - charge) if export is not None else discharge - charge
        model.add_constraints(
            net_injection <= xr.DataArray(envelope - committed, coords=[coords]),
            name="availability_reserve_aggregate",
            mask=xr.DataArray(agg_mask, coords=[coords]),
        )
    return compiled, warnings, committed
