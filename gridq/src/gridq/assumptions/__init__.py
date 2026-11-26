"""Assumption library (X.1).

One versioned source for every underwriting number — no private constants anywhere else.
`load_assumptions(version)` returns a validated `AssumptionLibrary`; each entry is a fully
documented `Assumption`. See docs/assumption_library.md.
"""

from __future__ import annotations

from gridq.assumptions.loader import (
    ASSUMPTIONS_DIR,
    AssumptionError,
    available_versions,
    latest_version,
    load_assumptions,
)
from gridq.assumptions.models import (
    OPTIONAL_SECTION_NAMES,
    SECTION_NAMES,
    Assumption,
    AssumptionLibrary,
    AssumptionMetadata,
    AssumptionStatus,
)
from gridq.assumptions.reprice import (
    AssumptionMove,
    RepriceCheck,
    assess_quote_reprice,
    assess_reprice,
    reprice_tolerance_pct,
)

__all__ = [
    "load_assumptions",
    "available_versions",
    "latest_version",
    "ASSUMPTIONS_DIR",
    "AssumptionError",
    "Assumption",
    "AssumptionLibrary",
    "AssumptionMetadata",
    "AssumptionStatus",
    "OPTIONAL_SECTION_NAMES",
    "SECTION_NAMES",
    "AssumptionMove",
    "RepriceCheck",
    "assess_reprice",
    "assess_quote_reprice",
    "reprice_tolerance_pct",
]
