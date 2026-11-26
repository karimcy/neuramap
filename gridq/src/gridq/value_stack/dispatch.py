"""What a value function reads off a dispatch schedule — a market-agnostic *read model*.

The concrete half-hourly dispatch schedule is M3's to define and stays opaque (``Any``)
behind the ``OptimisationEngine`` seam; the value-stream interface deliberately does not
bind it (``interface.DispatchSchedule = Any``). A value function therefore reads a small,
stable set of **aggregate dispatched quantities** off whatever the dispatch engine
produces — exactly as the finance lane duck-types ``Scenario.price_multiplier`` rather than
importing the scenario type.

- :class:`DispatchScheduleLike` names those quantities as a structural Protocol.
- :class:`DispatchSummary` is a concrete, validated carrier (used by tests, and the shape a
  real dispatch result can expose).
- :func:`read_dispatch` normalises *anything* duck-typed into a :class:`DispatchSummary`.

This is a **read model**, not the frozen ``OptimisationResult`` contract: nothing here is
promoted onto the platform↔engine seam. Landing these aggregates on ``OptimisationResult`` (e.g.
``annual_discharge_kwh``) is M3's job, done jointly with its producer (decisions D9/D13).
No GB content lives here — only the units a value function needs.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import Field

from gridq.schemas.provenance import GridQModel


class DispatchReadError(ValueError):
    """Raised when a supplied dispatch schedule cannot be read as priced quantities."""


@runtime_checkable
class DispatchScheduleLike(Protocol):
    """The aggregate quantities a value function prices a dispatch schedule over.

    Any object exposing these attributes (a :class:`DispatchSummary`, or a real M3
    dispatch result) can be valued. ``power_mw`` and ``duration_h`` describe the sized
    asset; the optional energy fields carry windowed delivery a stream prices against.
    """

    @property
    def power_mw(self) -> float: ...
    @property
    def duration_h(self) -> float: ...
    @property
    def annual_discharge_mwh(self) -> float | None: ...
    @property
    def red_band_discharge_mwh(self) -> float | None: ...
    @property
    def tender_window_discharge_mwh(self) -> float | None: ...
    @property
    def deliverable_capacity_mw(self) -> float | None: ...
    @property
    def load_service_energy_mwh(self) -> float | None: ...


class DispatchSummary(GridQModel):
    """Concrete, validated carrier of the priced quantities of a dispatch schedule.

    The minimal set the GB value functions price against. ``power_mw`` / ``duration_h``
    are the sized asset; the optional energy fields let a stream value windowed delivery
    (red-band avoidance, an awarded tender's utilisation) and let Capacity Market price the
    engine-verified end-of-life de-rated capability when the engine supplies it.
    """

    power_mw: float = Field(ge=0, description="Sized battery power rating (MW).")
    duration_h: float = Field(gt=0, description="Storage duration at rated power (hours).")
    annual_discharge_mwh: float | None = Field(
        default=None, ge=0, description="Total annual delivered (discharge) energy (MWh)."
    )
    red_band_discharge_mwh: float | None = Field(
        default=None, ge=0, description="Discharge delivered across DUoS red-band windows (MWh)."
    )
    tender_window_discharge_mwh: float | None = Field(
        default=None,
        ge=0,
        description="Discharge delivered within an awarded local-flex tender's windows (MWh).",
    )
    deliverable_capacity_mw: float | None = Field(
        default=None,
        ge=0,
        description="Engine-verified de-rated capability held to the final contract year at "
        "degraded (end-of-life) capability (MW) — the CM deliverability profile when known.",
    )
    load_service_energy_mwh: float | None = Field(
        default=None,
        ge=0,
        description="Annualised energy the battery was FORCED to serve against declared "
        "load-service obligations (MWh, issue 017 / D42) — measured delivery, never priced "
        "as import displacement.",
    )


_REQUIRED = ("power_mw", "duration_h")
_OPTIONAL = (
    "annual_discharge_mwh",
    "red_band_discharge_mwh",
    "tender_window_discharge_mwh",
    "deliverable_capacity_mw",
    "load_service_energy_mwh",
)


def read_dispatch(schedule: object) -> DispatchSummary:
    """Normalise a duck-typed dispatch schedule into a :class:`DispatchSummary`.

    Accepts a :class:`DispatchSummary` as-is, or any object exposing at least ``power_mw``
    and ``duration_h`` (plus the optional energy fields). Raises :class:`DispatchReadError`
    if the required quantities are absent or invalid — the caller then prices nothing and
    warns, never invents a value.
    """
    if isinstance(schedule, DispatchSummary):
        return schedule
    try:
        values = {name: getattr(schedule, name) for name in _REQUIRED}
    except AttributeError as exc:
        raise DispatchReadError(
            f"dispatch schedule is missing a required quantity ({exc}); "
            "value functions need at least power_mw and duration_h."
        ) from exc
    for name in _OPTIONAL:
        values[name] = getattr(schedule, name, None)
    try:
        return DispatchSummary(**values)
    except Exception as exc:  # pydantic ValidationError or bad types
        raise DispatchReadError(f"dispatch schedule quantities are invalid: {exc}") from exc
