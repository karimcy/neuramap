"""Continuous sizing domain (M4 / adaptive search) — the 2-D power×energy space the wider
sizing search ranges over, replacing the fixed 3×3 enumeration (`SearchSpace`).

The decision space is two free continuous variables — battery **power** (kW) and **usable
energy** (kWh) — so the C-rate (power/energy) floats instead of being locked to the
`{1, 2, 4}`-hour duration grid. Duration is the *derived* quantity (``energy / power``) and is
bounded to a physically sane band so the search never proposes a degenerate spec (a 0.1C or a
20C system). Every emitted candidate is a `SizingCandidate` — the existing per-spec unit the
dispatch+finance evaluator already consumes — so the wider search drops in behind the **same
exact full-8760-hour LP inner evaluator** with no change to the dispatch.

Data-integrity rules (SKILLS.md / parallel_development_plan §5):
- **No invented site data.** Power/energy bounds are supplied by the caller (scaled to the
  site's peak / connection / budget — stage 2); this module never fabricates a kW or kWh.
- **Deterministic.** ``grid()`` is a sorted cartesian product; ``latin_hypercube()`` is drawn
  from a seeded generator, so identical ``(domain, n, seed)`` → identical candidates.
- **No degenerate specs.** Power, energy and the derived duration are strictly positive and
  within the duration band; out-of-band points are excluded (grid), and LHS samples power only
  within the band-feasible window so every sample is in-band by construction (no clamping). A
  box that never intersects the band is rejected at construction, not sampled around.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from gridq.schemas import SiteModel
from gridq.sizing.search_space import SizingCandidate

# Labelled STRUCTURAL default (injects no £/%, same precedent as
# SearchSpace.DEFAULT_DURATION_HOURS): the physically sane duration band a behind-the-meter
# system is searched within when the caller gives no tighter bound. C&I BTM batteries are
# ~0.5–8 h; outside this the spec is degenerate, not a real candidate.
DEFAULT_DURATION_BAND_H: tuple[float, float] = (0.5, 8.0)

# Labelled STRUCTURAL default (injects no £/%, same precedent as MilpEngine.POWER_FRACTIONS):
# the continuous power search ranges over this fraction band of the site's binding scale
# (observed peak import, else the connection limit). Deliberately wider than the legacy 3×3
# grid's {0.25, 0.5, 1.0} fractions — the point of issue 016 — while still site-scaled,
# never an invented absolute kW.
DEFAULT_POWER_FRACTION_RANGE: tuple[float, float] = (0.05, 1.0)

_TOL = 1e-9


@dataclass(frozen=True)
class SizingDomain:
    """A continuous 2-D (power kW × energy kWh) sizing box with a derived-duration band.

    ``duration_h_min/max`` bound the derived duration (``energy / power``) so the free-energy
    search stays within physically sane C-rates. All six numbers must be strictly positive and
    each ``min`` strictly below its ``max``.
    """

    power_kw_min: float
    power_kw_max: float
    energy_kwh_min: float
    energy_kwh_max: float
    duration_h_min: float = DEFAULT_DURATION_BAND_H[0]
    duration_h_max: float = DEFAULT_DURATION_BAND_H[1]

    def __post_init__(self) -> None:
        for label, lo, hi in (
            ("power_kw", self.power_kw_min, self.power_kw_max),
            ("energy_kwh", self.energy_kwh_min, self.energy_kwh_max),
            ("duration_h", self.duration_h_min, self.duration_h_max),
        ):
            if lo <= 0 or hi <= 0:
                raise ValueError(f"{label} bounds must be strictly positive, got ({lo}, {hi})")
            if lo >= hi:
                raise ValueError(f"{label} min must be strictly below max, got ({lo}, {hi})")
        p_lo, p_hi = self._band_feasible_power_window()
        if p_lo > p_hi + _TOL:
            raise ValueError(
                "power×energy box never intersects the duration band: no power in "
                f"[{self.power_kw_min}, {self.power_kw_max}] kW admits an in-box energy at a "
                f"duration in [{self.duration_h_min}, {self.duration_h_max}] h "
                f"(band-feasible power window ({p_lo}, {p_hi}) is empty)"
            )

    # --- construction ------------------------------------------------------

    @classmethod
    def from_site(
        cls,
        site: SiteModel,
        *,
        power_fraction_range: tuple[float, float] = DEFAULT_POWER_FRACTION_RANGE,
        duration_band_h: tuple[float, float] = DEFAULT_DURATION_BAND_H,
    ) -> SizingDomain:
        """Site-scaled continuous bounds (issue 016 stage 2) — replaces the 3×3 heuristic.

        The power box is ``power_fraction_range`` × the site's **binding scale** — the observed
        peak import (``load_data.max_import_kw``) when present, else the connection limit
        (``connection.mic_kw``) — mirroring ``MilpEngine._default_search_space``'s basis exactly,
        so the continuous domain never invents a kW the grid path would not have. The energy box
        is the band-spanning envelope (``power_min·d_min`` … ``power_max·d_max``), so the domain
        is exactly the band-feasible region over that power range.

        Raises ``ValueError`` (a data-ask, not a fabricated default) when the site carries no
        sizing basis — callers degrade to insufficient-data exactly as the engine does today.
        """
        frac_lo, frac_hi = power_fraction_range
        if frac_lo <= 0 or frac_hi <= 0 or frac_lo >= frac_hi:
            raise ValueError(
                f"power_fraction_range must satisfy 0 < lo < hi, got {power_fraction_range}"
            )
        peak = site.load_data.max_import_kw
        mic = site.connection.mic_kw
        base = peak if (peak and peak > 0) else (mic if (mic and mic > 0) else None)
        if base is None:
            raise ValueError(
                "no sizing basis: the site has no observed peak (load_data.max_import_kw) or "
                "connection limit (connection.mic_kw) to scale the sizing domain from — supply "
                "metered half-hourly data or the connection MIC (data ask)."
            )
        d_lo, d_hi = duration_band_h
        p_lo = round(frac_lo * base, 6)
        p_hi = round(frac_hi * base, 6)
        return cls(
            power_kw_min=p_lo,
            power_kw_max=p_hi,
            energy_kwh_min=round(p_lo * d_lo, 6),
            energy_kwh_max=round(p_hi * d_hi, 6),
            duration_h_min=d_lo,
            duration_h_max=d_hi,
        )

    # --- membership --------------------------------------------------------

    def _band_feasible_power_window(self) -> tuple[float, float]:
        """The power range whose band-implied energy window intersects the energy box.

        A power ``p`` admits an in-band, in-box energy iff ``[p·d_min, p·d_max]`` overlaps
        ``[energy_min, energy_max]`` — i.e. ``p ∈ [energy_min/d_max, energy_max/d_min]``
        intersected with the power box.
        """
        return (
            max(self.power_kw_min, self.energy_kwh_min / self.duration_h_max),
            min(self.power_kw_max, self.energy_kwh_max / self.duration_h_min),
        )

    def _duration_ok(self, power_kw: float, energy_kwh: float) -> bool:
        duration = energy_kwh / power_kw
        return self.duration_h_min - _TOL <= duration <= self.duration_h_max + _TOL

    def contains(self, power_kw: float, energy_kwh: float) -> bool:
        """True iff (power, energy) is inside the box **and** the derived-duration band."""
        if not (self.power_kw_min - _TOL <= power_kw <= self.power_kw_max + _TOL):
            return False
        if not (self.energy_kwh_min - _TOL <= energy_kwh <= self.energy_kwh_max + _TOL):
            return False
        return self._duration_ok(power_kw, energy_kwh)

    def _candidate(self, power_kw: float, energy_kwh: float) -> SizingCandidate:
        # Reuse the existing per-spec unit: duration = energy / power, so the candidate's derived
        # ``energy_kwh`` (= power × duration) round-trips to the requested energy.
        return SizingCandidate(power_kw=power_kw, duration_hours=energy_kwh / power_kw)

    # --- samplers ----------------------------------------------------------

    def grid(self, n_power: int, n_energy: int) -> list[SizingCandidate]:
        """A dense, deterministic power×energy grid, duration-band filtered.

        ``n_power`` × ``n_energy`` linearly-spaced points across the box, in sorted
        ``(power, energy)`` order; points whose derived duration falls outside the band are
        excluded (box corners can imply an out-of-band C-rate). This is the exact-LP **reference
        surface** the adaptive search is benchmarked against.
        """
        if n_power < 1 or n_energy < 1:
            raise ValueError(
                f"grid resolution must be ≥ 1 on each axis, got ({n_power}, {n_energy})"
            )
        powers = np.linspace(self.power_kw_min, self.power_kw_max, n_power)
        energies = np.linspace(self.energy_kwh_min, self.energy_kwh_max, n_energy)
        out: list[SizingCandidate] = []
        for p in powers:
            for e in energies:
                pf, ef = float(p), float(e)
                if self._duration_ok(pf, ef):
                    out.append(self._candidate(round(pf, 6), round(ef, 6)))
        return out

    def latin_hypercube(self, n: int, *, seed: int = 0) -> list[SizingCandidate]:
        """``n`` deterministic Latin-hypercube samples, every one inside the duration band.

        Power is LHS-sampled over the **band-feasible power window** (not the raw power box), so
        each sampled power admits an in-band, in-box energy by construction — no sample is ever
        clamped out of the band (`grid()` and `latin_hypercube()` share one band semantics).
        Energy is then sampled within that power's band-feasible energy window. Identical
        ``(domain, n, seed)`` → identical candidates.
        """
        if n < 1:
            raise ValueError(f"sample count must be ≥ 1, got {n}")
        rng = np.random.default_rng(seed)
        p_lo, p_hi = self._band_feasible_power_window()
        # Latin-hypercube strata on power: one jittered sample per stratum, then permuted.
        strata = (np.arange(n) + rng.random(n)) / n
        powers = p_lo + strata[rng.permutation(n)] * (p_hi - p_lo)
        u_energy = rng.random(n)
        out: list[SizingCandidate] = []
        for p_raw, u in zip(powers, u_energy, strict=True):
            # Round power FIRST and derive the energy window from the rounded value — rounding
            # power after sampling energy can nudge the pair just past the band edge.
            p = round(float(p_raw), 6)
            # Band-feasible energy window for this power, intersected with the energy box —
            # non-empty for every p in the window (float-edge guarded).
            lo = max(self.energy_kwh_min, p * self.duration_h_min)
            hi = max(min(self.energy_kwh_max, p * self.duration_h_max), lo)
            e = min(max(round(lo + float(u) * (hi - lo), 6), lo), hi)
            out.append(self._candidate(p, e))
        return out
