"""`gridq.constraint_library`: the dispatch constraint set, one versioned package (WP-SIZE-02).

The constraints a battery dispatch must honour live here and nowhere else: the import and export
limits and the kVA polygons (`connection`), the resilience floor of declared load-service
obligations (`resilience`), the thermal turn-down limit from an IC-11 surface (`thermal`), the
usable SoC window (`soc`), the warranty throughput cap (`warranty`) and contracted availability
commitments (`commitments`). The sizer's dispatch (`MilpDispatchSolver`, called per candidate) and
the rolling-horizon MPC (`MpcDispatchSolver`, which solves each window through the same build) both
compile their constraints from these modules (NEW-SIZE-LIB: B2-02 needs the same library as A5).

**Boundary (D176).** The library imports frozen contracts only (`gridq.contracts`, and
the IC-12 chassis in `gridq.schemas`), the standard library and numpy, pandas and xarray; never
workstream code. The caller resolves every number (release keys, site fields, derived series)
and hands it in; the library holds the algebra, the row names and the disclosures.
``tests/test_constraint_library.py`` enforces the boundary by AST scan.

**Identity (D177).** ``LIBRARY_VERSION`` plus the source hash; `library_ref` is the stamp
on every `OptimisationResult` (IC-12 3.2.0) and `library_pin` the IC-3 envelope pin.
"""

from __future__ import annotations

from gridq.constraint_library import (
    catalogue,
    commitments,
    connection,
    resilience,
    soc,
    thermal,
    warranty,
)
from gridq.constraint_library.catalogue import CATALOGUE, ConstraintSpec, Family, constraint_ids
from gridq.constraint_library.identity import (
    LIBRARY_ID,
    LIBRARY_VERSION,
    check_envelope,
    library_hash,
    library_pin,
    library_ref,
)

__all__ = [
    "CATALOGUE",
    "LIBRARY_ID",
    "LIBRARY_VERSION",
    "ConstraintSpec",
    "Family",
    "catalogue",
    "check_envelope",
    "commitments",
    "connection",
    "constraint_ids",
    "library_hash",
    "library_pin",
    "library_ref",
    "resilience",
    "soc",
    "thermal",
    "warranty",
]
