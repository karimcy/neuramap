"""IC-5 reference simulator endpoint (roadmap WP-EDGE-01, D121).

Something both sides of the edge–cloud link can develop against before the other side exists:

- ``relay``: a validating mailbox. The cloud posts cloud-to-edge frames, the edge reads them, and
  the other way round. Every frame is validated against IC-5 before it is accepted.
- ``edge-twin``: a reference edge (`EdgeReference`) behind the endpoint, for cloud developers. It
  admits schedules, acknowledges them, runs the lease and enters and leaves fail-safe exactly as
  the contract's pure functions say.
- ``cloud-twin``: a reference cloud (`CloudReference`), for edge developers. It sends heartbeats
  and re-issues an idle schedule inside the held IC-3 envelope on the profile's cadence.

Run it with ``python -m gridq.edge_reference --role relay --port 0 ...`` (see ``__main__``).

**A simulator, not a gateway.** It binds 127.0.0.1 only, has no authentication, answers with the
header ``X-IC5-Reference: simulator``, refuses any site that is not ``SYNTHETIC-``, and runs on a
simulated clock that moves only when told (``POST /sim/v1/clock``): no sleeps, no wall clock.

Imports the standard library and `gridq.contracts` only (a test proves it), so it installs with
the contracts and nothing else.
"""

from __future__ import annotations

from gridq.edge_reference.machines import CloudReference, EdgeReference, Simulation

__all__ = ["CloudReference", "EdgeReference", "Simulation"]
