"""What a declared IC-9 turn-down could deliver, on request only (WP-SITE-07, D126/D127).

The ensemble is never changed by a declaration (`gridq.datacentre.workload_flex`). This module
answers the separate question a consumer asks of a bound declaration: *if a call of
``duration_min`` minutes started at each half-hour, how much could it take off?* It is the load
model's reading of the contract, and it reads the contract only through its readers:

- the state in force at each naive-local half-hour comes from `declared_at_local` (PEP 495
  ``fold=0``, the contract's rule);
- the kW comes from `stack_at` (merit order, one envelope per exclusion group, the concurrent cap
  and the retained IT floor), at each draw's IT load floored to a whole kW (the contract's
  integers; flooring is the safe side). Summing curves instead would double-count alternatives.

The caller states the duration: every envelope is a duration curve, and no single duration is the
right one for every consumer (D127).

Per draw and half-hour it returns:

- ``it_deliverable_kw``: the IT turn-down, and each envelope's layer of it;
- ``meter_instant_kw``: what the meter sees as soon as IT falls: IT plus the UPS loss and
  distribution loss that fall with it, cooling and auxiliaries held (the thermal mass carries
  the cooling load for the first minutes);
- ``meter_settled_kw``: facility power at the declared load minus facility power at the reduced
  load, both through the plant model `evaluate` (cooling settled to the lower heat).

The reduction is allocated across phases in proportion to their load (``pro_rata_phase_load/v1``).
Each quantity has its expected series and pointwise P05 to P95 bands. Every number is
``firmness = "conditional"`` (B8-03): an offer by a third party's workloads, never firm, never in
the priced floor, and never a gate on any revenue stream (D57).

`turndown_overlay_minutes` gives the IT turn-down at one minute, reading each draw's one-minute IT
load; the meter conversion is half-hourly only in this version (stated in its notes).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

import numpy as np

from gridq.contracts.compute_turndown import (
    MAX_HOLD_MIN,
    ComputeTurndownDeclaration,
    NotInForce,
    OfferedAnswer,
    declared_at_local,
    stack_at,
)
from gridq.datacentre.model import evaluate
from gridq.datacentre.synthesiser import (
    _ROUND,
    BLOCK_MINUTES,
    LEVEL_NAMES,
    LEVELS,
    Synthesis,
    _rounded_bytes,
)
from gridq.datacentre.workload_flex import WorkloadFlexBinding
from gridq.provenance.hashing import stable_json_dumps

__all__ = ["ALLOCATION", "FIRMNESS", "MinuteTurndown", "TurndownOverlay", "turndown_overlay",
           "turndown_overlay_minutes"]  # fmt: skip

FIRMNESS: Final = "conditional"
ALLOCATION: Final = "pro_rata_phase_load/v1"
OVERLAY_VERSION: Final = "dc_turndown_overlay 1.0.0"


@dataclass(frozen=True)
class TurndownOverlay:
    """What a call of ``duration_min`` starting at each half-hour could take off, every draw."""

    duration_min: int
    timestamps: tuple[datetime, ...]
    declaration_hash: str
    binding_hash: str
    it_deliverable_kw: np.ndarray  # (N, T)
    layers_kw: dict[str, np.ndarray]  # envelope id -> (N, T)
    meter_instant_kw: np.ndarray  # (N, T)
    meter_settled_kw: np.ndarray  # (N, T)
    expected_kw: dict[str, np.ndarray]  # quantity -> (T,)
    bands_kw: dict[str, dict[str, np.ndarray]]  # quantity -> level -> (T,)
    firmness: Literal["conditional"]
    allocation: str
    notes: tuple[str, ...]
    overlay_hash: str


@dataclass(frozen=True)
class MinuteTurndown:
    """The IT turn-down at one minute over ``[start, end)``, every draw."""

    duration_min: int
    start: datetime
    end: datetime
    it_deliverable_kw: np.ndarray  # (N, M)
    expected_kw: np.ndarray
    bands_kw: dict[str, np.ndarray]
    firmness: Literal["conditional"]
    notes: tuple[str, ...]
    overlay_hash: str


def _binding(synthesis: Synthesis) -> WorkloadFlexBinding:
    if not isinstance(synthesis.workload_flex, WorkloadFlexBinding):
        raise ValueError(
            "no IC-9 turn-down is declared for this ensemble: IT load is untouched and there is "
            "nothing to overlay (pass workload_flex= to synthesise)"
        )
    return synthesis.workload_flex


def _check_duration(duration_min: int) -> None:
    if isinstance(duration_min, bool) or not isinstance(duration_min, int):
        raise ValueError("duration_min must be an integer number of minutes")
    if not (1 <= duration_min <= MAX_HOLD_MIN):
        raise ValueError(f"duration_min must be 1 to {MAX_HOLD_MIN}")


def _whole_kw(it_kw: np.ndarray) -> np.ndarray:
    """IT load floored to a whole kW (the contract's integers), after the package's rounding."""
    return np.floor(np.round(it_kw, _ROUND)).astype(np.int64)


class _Stacker:
    """`stack_at` per (state, whole-kW load), cached: the answer depends on nothing else."""

    def __init__(self, declaration: ComputeTurndownDeclaration, duration_min: int) -> None:
        self.declaration = declaration
        self.duration_min = duration_min
        self.ids = list(declaration.stacking.merit_order)
        self._cache: dict[tuple[str, int], tuple[int, ...]] = {}

    def layers(self, answer: OfferedAnswer, load_kw: int) -> tuple[int, ...]:
        """kW per envelope in merit order (0 where it takes no layer)."""
        key = (answer.state.model_dump_json(), load_kw)
        hit = self._cache.get(key)
        if hit is None:
            stack = stack_at(self.declaration, answer.instant, self.duration_min, load_kw)
            by_id = {x.envelope_id: x.kw for x in stack.layers}
            hit = tuple(by_id.get(i, 0) for i in self.ids)
            self._cache[key] = hit
        return hit


def _declared(declaration: ComputeTurndownDeclaration, t: datetime) -> OfferedAnswer | NotInForce:
    """The declaration at the naive-local half-hour holding ``t`` (minute m reads m // 30)."""
    return declared_at_local(declaration, t.date(), (t.hour * 60 + t.minute) // BLOCK_MINUTES)


def _stats(values: np.ndarray) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    expected = values.mean(axis=0)
    bands = {LEVEL_NAMES[p]: np.quantile(values, p, axis=0, method="inverted_cdf") for p in LEVELS}
    return expected, bands


def turndown_overlay(synthesis: Synthesis, *, duration_min: int) -> TurndownOverlay:
    """Per draw and half-hour, the turn-down a call of ``duration_min`` minutes starting there
    could deliver under the bound declaration (module docstring). Raises when none is bound."""
    binding = _binding(synthesis)
    _check_duration(duration_min)
    declaration = binding.declaration
    engine = synthesis._engine
    hh = engine.half_hourly(0, synthesis.inputs.days)
    it = hh.components.it_kw
    load = _whole_kw(it)
    stacker = _Stacker(declaration, duration_min)
    n, t_count = it.shape
    layers = np.zeros((len(stacker.ids), n, t_count))
    for j, answer in enumerate(_declared(declaration, t) for t in synthesis.timestamps):
        if isinstance(answer, NotInForce):
            continue
        for i in range(n):
            layers[:, i, j] = stacker.layers(answer, int(load[i, j]))
    it_deliverable = layers.sum(axis=0)

    # Pro-rata across phases; a phase with no load gives nothing.
    with np.errstate(divide="ignore", invalid="ignore"):
        keep = np.where(it > 0, 1.0 - it_deliverable / np.where(it > 0, it, 1.0), 1.0)
    reduced = evaluate(
        engine.plant,
        engine.params,
        phase_load=hh.phase_load * keep[:, None, :],
        phase_cap=hh.phase_cap,
        phase_live=hh.phase_live,
        any_live=hh.any_live,
        ambient_c=hh.ambient,
        half_hour_of_day=hh.half_hour_of_day,
    )
    base = hh.components
    instant = (
        (base.it_kw - reduced.it_kw)
        + (base.ups_loss_kw - reduced.ups_loss_kw)
        + (base.distribution_kw - reduced.distribution_kw)
    )
    settled = base.facility_kw - reduced.facility_kw

    quantities = {
        "it_deliverable": it_deliverable,
        "meter_instant": instant,
        "meter_settled": settled,
    }
    expected: dict[str, np.ndarray] = {}
    bands: dict[str, dict[str, np.ndarray]] = {}
    for name, values in quantities.items():
        expected[name], bands[name] = _stats(values)

    digest = hashlib.sha256()
    for values in quantities.values():
        digest.update(_rounded_bytes(values))
    digest.update(
        stable_json_dumps(
            {
                "overlay": OVERLAY_VERSION,
                "binding_hash": binding.binding_hash,
                "duration_min": duration_min,
                "firmness": FIRMNESS,
                "allocation": ALLOCATION,
            }
        ).encode("utf-8")
    )
    notes = (
        *binding.notes,
        f"Deliverable if a call of {duration_min} min starts at each half-hour, read with the "
        "IC-9 readers (declared_at_local, stack_at) at each draw's IT load floored to a whole kW.",
        "meter_instant holds cooling and auxiliaries; meter_settled re-evaluates the plant at the "
        "reduced IT load (cooling settled).",
    )
    return TurndownOverlay(
        duration_min=duration_min,
        timestamps=synthesis.timestamps,
        declaration_hash=binding.declaration_hash,
        binding_hash=binding.binding_hash,
        it_deliverable_kw=it_deliverable,
        layers_kw={eid: layers[k] for k, eid in enumerate(stacker.ids)},
        meter_instant_kw=instant,
        meter_settled_kw=settled,
        expected_kw=expected,
        bands_kw=bands,
        firmness=FIRMNESS,
        allocation=ALLOCATION,
        notes=notes,
        overlay_hash=f"sha256:{digest.hexdigest()}",
    )


def turndown_overlay_minutes(
    synthesis: Synthesis, start: datetime, end: datetime, *, duration_min: int
) -> MinuteTurndown:
    """Per draw and minute of ``[start, end)``, the IT turn-down a call of ``duration_min``
    starting at that minute could deliver, at that minute's IT load. Each minute reads the
    declaration at its naive-local half-hour (minute ``m`` of the day reads half-hour ``m // 30``,
    the contract's rule). Raises when no declaration is bound."""
    binding = _binding(synthesis)
    _check_duration(duration_min)
    declaration = binding.declaration
    window = synthesis.one_minute(start, end, jensen_gap_flag_kw=None)
    load = _whole_kw(window.it_members_kw)
    stacker = _Stacker(declaration, duration_min)
    n, m_count = load.shape
    minutes = tuple(start + timedelta(minutes=k) for k in range(m_count))
    by_block: dict[datetime, OfferedAnswer | NotInForce] = {}
    out = np.zeros((n, m_count))
    for k, t in enumerate(minutes):
        head = t.replace(minute=t.minute - t.minute % BLOCK_MINUTES)
        if head not in by_block:
            by_block[head] = _declared(declaration, head)
        answer = by_block[head]
        if isinstance(answer, NotInForce):
            continue
        for i in range(n):
            out[i, k] = math.fsum(stacker.layers(answer, int(load[i, k])))
    expected, bands = _stats(out)
    digest = hashlib.sha256()
    digest.update(_rounded_bytes(out))
    digest.update(
        stable_json_dumps(
            {
                "overlay": OVERLAY_VERSION,
                "binding_hash": binding.binding_hash,
                "window_hash": window.window_hash,
                "duration_min": duration_min,
            }
        ).encode("utf-8")
    )
    return MinuteTurndown(
        duration_min=duration_min,
        start=start,
        end=end,
        it_deliverable_kw=out,
        expected_kw=expected,
        bands_kw=bands,
        firmness=FIRMNESS,
        notes=(
            *binding.notes,
            "One-minute IT turn-down only: the meter conversion is half-hourly in this version "
            "(turndown_overlay).",
        ),
        overlay_hash=f"sha256:{digest.hexdigest()}",
    )
