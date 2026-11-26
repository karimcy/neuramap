"""The sizing search space (M4) — the P·duration grid the outer loop enumerates.

A `SearchSpace` is a deterministic, validated grid of candidate systems. Per the spec the v1
optimiser is **enumeration first** (smarter search only if the solve-time benchmark demands it),
so this is a plain, sorted, de-duplicated cartesian product of power × duration, with energy
derived (``energy_kwh = power_kw × duration_hours``).

Data-integrity rules (SKILLS.md / parallel_development_plan §5):
- **No invented site data.** The *power* candidates must be supplied by the caller — the scale
  of a site's battery is site-specific (bounded by load, connection headroom, footprint) and is
  never fabricated here. Only the *duration* grid carries a labelled structural default, because
  durations are a small structural set the assumption library is already keyed on.
- **No negative or zero sizes.** Power and duration must be strictly positive (a 0-kW or 0-h
  battery is not a candidate); negatives are rejected outright.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# Labelled STRUCTURAL default (not an underwriting £-assumption — it injects no £/% into any
# output, same precedent as finance's DEFAULT_HORIZON_YEARS, decision D9). The durations match
# the keys the assumption library uses for duration-dependent values — warranty cycle caps
# (``warranty_cycle_limits.max_cycles_per_year_by_duration_h``) and Capacity-Market de-rating
# (``capacity_market.de_rating_factor_by_duration_h``), both keyed '1'/'2'/'4'. A caller may pass
# any durations, but off-grid durations have no library key until one is added.
DEFAULT_DURATION_HOURS: tuple[float, ...] = (1.0, 2.0, 4.0)


@dataclass(frozen=True)
class SizingCandidate:
    """One candidate system: a fixed power, duration and the energy they imply.

    ``energy_kwh`` is *derived* (``power_kw × duration_hours``) — never an independent input — so
    the three numbers can never disagree. Power and duration must be strictly positive.
    """

    power_kw: float
    duration_hours: float

    def __post_init__(self) -> None:
        if self.power_kw <= 0:
            raise ValueError(f"power_kw must be strictly positive, got {self.power_kw}")
        if self.duration_hours <= 0:
            raise ValueError(f"duration_hours must be strictly positive, got {self.duration_hours}")

    @property
    def energy_kwh(self) -> float:
        """Derived usable energy capacity (kWh) = power (kW) × duration (h)."""
        return round(self.power_kw * self.duration_hours, 6)


def _validated_sorted_unique(values: Iterable[float], *, label: str) -> list[float]:
    """Reject non-positive values, de-duplicate, and sort ascending (deterministic)."""
    out: list[float] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise TypeError(f"{label} candidates must be numbers, got {v!r}")
        fv = float(v)
        if fv <= 0:
            raise ValueError(f"{label} candidates must be strictly positive, got {fv}")
        out.append(fv)
    return sorted(set(out))


class SearchSpace:
    """A validated, deterministic grid of `SizingCandidate`s (power × duration).

    Both axes are validated (strictly positive), de-duplicated and sorted ascending on
    construction. ``candidates()`` is the cartesian product, ordered deterministically by the
    ``(power_kw, duration_hours)`` tuple (not by derived energy — that would be ambiguous for
    ties such as 100 kW × 4 h vs 200 kW × 2 h, both 400 kWh). An empty axis yields no candidates
    (the caller decides what an empty search means — the sizer treats it as "no candidate").
    """

    def __init__(
        self,
        power_kw_candidates: Iterable[float],
        duration_hour_candidates: Iterable[float] = DEFAULT_DURATION_HOURS,
    ) -> None:
        self._power_kw = _validated_sorted_unique(power_kw_candidates, label="power_kw")
        self._duration_hours = _validated_sorted_unique(
            duration_hour_candidates, label="duration_hours"
        )

    @property
    def power_kw_candidates(self) -> list[float]:
        """The validated, sorted, unique power candidates (kW)."""
        return list(self._power_kw)

    @property
    def duration_hour_candidates(self) -> list[float]:
        """The validated, sorted, unique duration candidates (hours)."""
        return list(self._duration_hours)

    def candidates(self) -> list[SizingCandidate]:
        """The full cartesian product, in deterministic ``(power, duration)`` order."""
        return [
            SizingCandidate(power_kw=p, duration_hours=d)
            for p in self._power_kw
            for d in self._duration_hours
        ]

    def __len__(self) -> int:
        return len(self._power_kw) * len(self._duration_hours)

    def __repr__(self) -> str:
        return (
            f"SearchSpace(power_kw={self._power_kw}, duration_hours={self._duration_hours}, "
            f"candidates={len(self)})"
        )
