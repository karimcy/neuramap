"""Dispatch profile — a representative dispatch day persisted for the Wave 5 dispatch chart.

The MILP solves a full half-hourly battery dispatch over the metered horizon
(`gridq.optimisation.schedule.HalfHourlyDispatchSchedule`), but that schedule is the dispatch
engine's *internal* artifact and is dropped at the frozen seam (D13) — only its content-hash
``dispatch_schedule_ref`` survives onto the `OptimisationResult`. `DispatchProfile` is the additive,
serializable projection of **one representative day** of that solved schedule — the half-hourly
charge / discharge / state-of-energy / load / price series the Wave 5 dispatch chart plots — built
from a `HalfHourlyDispatchSchedule` and persisted onto the `QuoteRecord` so the dispatch is legible
from saved run data, not recomputed ad-hoc.

No dispatch logic lives here — only the shared shape. It carries **no invented numbers**: every
value is copied straight off a real solved half-hour for one **real** day, chosen deterministically
(never an average or a synthesized shape); an unavailable metric stays ``None`` (no time-of-use
energy price without a time-mappable tariff), never coerced.
"""

from __future__ import annotations

from pydantic import Field

from gridq.schemas.provenance import GridQModel


class DispatchProfilePoint(GridQModel):
    """One half-hour of the representative dispatch day — real solved values, never invented.

    ``charge_kw`` / ``discharge_kw`` are the AC-side battery power in that half-hour (kW, both
    ``≥ 0``); ``soe_kwh`` is the state of energy (kWh); ``site_load_kw`` the metered site load;
    ``duos_rate_gbp_per_kwh`` the DUoS rate the dispatch faced; ``red_band`` whether the half-hour
    sits in a DUoS red band; ``energy_price_gbp_per_kwh`` the time-of-use energy price the MILP
    arbitraged (``None`` when the tariff had no time-mappable spread — not coerced to 0).
    """

    period: int = Field(ge=0)  # 0-based half-hour index within the representative day
    charge_kw: float = Field(ge=0)
    discharge_kw: float = Field(ge=0)
    soe_kwh: float = Field(ge=0)
    site_load_kw: float
    duos_rate_gbp_per_kwh: float
    red_band: bool
    energy_price_gbp_per_kwh: float | None = None


class DispatchProfile(GridQModel):
    """A representative dispatch day sliced from the solved schedule — the chart's data source.

    ``points`` is one day of half-hourly dispatch in period order; the day is selected
    **deterministically** from the metered horizon (the highest-total-discharge day, earliest index
    on ties — the most legible day of the dispatch strategy), recorded in ``selection_basis`` and
    ``representative_day_index`` so the reader knows which real day this is. Every value is a
    real solved half-hour — **not** an average over days. ``interval_hours`` is the half-hourly Δt
    (0.5). ``power_kw`` / ``energy_kwh`` / ``duration_hours`` echo the dispatched spec.
    """

    power_kw: float = Field(ge=0)
    energy_kwh: float = Field(ge=0)
    duration_hours: float = Field(ge=0)
    interval_hours: float = Field(gt=0)
    representative_day_index: int = Field(ge=0)
    selection_basis: str
    points: list[DispatchProfilePoint] = Field(default_factory=list)
