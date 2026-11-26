"""Supply boundary for a site (M1).

The System Brief's canonical schema lists "supply boundary & supplier consent" as distinct
required elements. Supplier-consent state lives on `ConsentState`; this records the **supply
boundary** — the metering/contractual perimeter that defines what sits *behind the meter* (and so
which load and tariff the battery operates within). All fields default to unknown so existing
sites validate unchanged; the boundary is recorded, never invented.
"""

from __future__ import annotations

from gridq.schemas.provenance import GridQModel


class SupplyBoundary(GridQModel):
    """The host's supply boundary — the behind-the-meter metering/contractual perimeter."""

    #: The host's import supply MPAN (the supply meter point that defines the boundary).
    import_mpan: str | None = None
    #: Whether the battery connects behind the host's supply meter (the behind-the-meter premise).
    #: ``None`` until confirmed — never assumed.
    behind_the_meter: bool | None = None
    #: Optional id/description of the metering arrangement at the boundary.
    metering_point: str | None = None
    notes: str | None = None
