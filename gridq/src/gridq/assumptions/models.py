"""Models for the versioned assumption library.

Every underwriting number lives here as a fully-documented `Assumption`. The library is
a `metadata` header plus the 23 named sections, each a mapping of assumption name ->
`Assumption`. Reuses `GridQModel` (extra='forbid' + schema_version) and the shared
`Confidence` enum.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import Field

from gridq.schemas.enums import Confidence
from gridq.schemas.provenance import GridQModel


class AssumptionStatus(StrEnum):
    """Review status of an assumption."""

    CONFIRMED = "confirmed"
    PLACEHOLDER = "placeholder"
    NEEDS_REVIEW = "needs_review"


class Assumption(GridQModel):
    """One underwriting assumption, fully documented.

    All eight descriptive fields are required so no number is undocumented.
    ``value`` may be a scalar or a structured value (e.g. a per-duration mapping).
    """

    value: Any
    unit: str
    source: str
    status: AssumptionStatus
    owner_or_reviewer: str
    confidence: Confidence
    last_reviewed: date
    notes: str


class AssumptionMetadata(GridQModel):
    """Release header for an assumption library file."""

    market: str
    version: str
    released: date
    currency: str = "GBP"
    notes: str | None = None


# The 23 sections every GB library release must carry (order matches docs).
SECTION_NAMES: tuple[str, ...] = (
    "battery_capex",
    "battery_opex",
    "degradation",
    "round_trip_efficiency",
    "parasitic_load",
    "warranty_cycle_limits",
    "financing",
    "dscr_covenants",
    "p50_p90_haircuts",
    "tariff_arbitrage",
    "duos",
    "mic_asc_headroom",
    "capacity_market",
    "local_flex_piclo",
    "wholesale_p415",
    "balancing_mechanism",
    "ancillary_services",
    "business_rates",
    "insurance",
    "metering_idno",
    "dno_costs",
    "quote_validity",
    "scenario_weights",
)

# The schema-evolution mechanism (D30, north_star Wave 8): the 23 sections above stay REQUIRED
# forever (removing or renaming one is a breaking event); NEW sections join this tuple as
# OPTIONAL fields defaulting to empty — so frozen releases (gb_v0_1, gb_v1_0) load byte-identically
# without them, and a new release simply carries the sections it introduces. Convention: an
# optional section present in a release must be fully populated per the same 8-field Assumption
# discipline; an empty dict means "this release predates the section", never "zero".
OPTIONAL_SECTION_NAMES: tuple[str, ...] = (
    "availability",  # availability-payment prices, availability/outage factors (firming — W8)
    "penalties",  # non-delivery / clawback regimes (CM penalty rate + caps, flex LDs — W8)
    "tnuos",  # the TNUoS demand residual + banding (the un-firmable bill line — W8/W9)
    "bill_products",  # capped/fixed-bill risk keys: volume/weather variance (W9 — the risk a
    # bill guarantee transfers onto the seller; its downside is NOT the P90 revenue haircut)
)


class AssumptionLibrary(GridQModel):
    """A full versioned assumption set: metadata + the 23 required sections (+ optional growth).

    Each section is a mapping of assumption name -> `Assumption`. Sections are explicit
    fields so a missing or mistyped section is a validation error. Sections in
    ``OPTIONAL_SECTION_NAMES`` default to empty (D30): frozen releases without them load
    unchanged, and a mistyped section name is still rejected (``extra='forbid'``).
    """

    metadata: AssumptionMetadata
    battery_capex: dict[str, Assumption]
    battery_opex: dict[str, Assumption]
    degradation: dict[str, Assumption]
    round_trip_efficiency: dict[str, Assumption]
    parasitic_load: dict[str, Assumption]
    warranty_cycle_limits: dict[str, Assumption]
    financing: dict[str, Assumption]
    dscr_covenants: dict[str, Assumption]
    p50_p90_haircuts: dict[str, Assumption]
    tariff_arbitrage: dict[str, Assumption]
    duos: dict[str, Assumption]
    mic_asc_headroom: dict[str, Assumption]
    capacity_market: dict[str, Assumption]
    local_flex_piclo: dict[str, Assumption]
    wholesale_p415: dict[str, Assumption]
    balancing_mechanism: dict[str, Assumption]
    ancillary_services: dict[str, Assumption]
    business_rates: dict[str, Assumption]
    insurance: dict[str, Assumption]
    metering_idno: dict[str, Assumption]
    dno_costs: dict[str, Assumption]
    quote_validity: dict[str, Assumption]
    scenario_weights: dict[str, Assumption]
    # Optional growth sections (D30) — empty = "this release predates the section", never "zero".
    availability: dict[str, Assumption] = Field(default_factory=dict)
    penalties: dict[str, Assumption] = Field(default_factory=dict)
    tnuos: dict[str, Assumption] = Field(default_factory=dict)
    bill_products: dict[str, Assumption] = Field(default_factory=dict)

    def get(self, section: str, name: str) -> Assumption:
        """Fetch one assumption by section + name (raises KeyError if absent)."""
        sect: dict[str, Assumption] = getattr(self, section)
        return sect[name]
