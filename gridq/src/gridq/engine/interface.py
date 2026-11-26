"""The shared optimisation-engine interface.

Both Oli's platform and Olly's (future) MILP code against this. Anything that builds a
request from a site + assumptions and solves it to a schema-valid `OptimisationResult`
is an engine — the baseline now, the real engine later, behind the *same* interface.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from gridq.assumptions import AssumptionLibrary
from gridq.schemas import OptimisationRequest, OptimisationResult, SiteModel


@runtime_checkable
class OptimisationEngine(Protocol):
    """An optimisation engine: build a request from a site, then solve it.

    Implementations must return a schema-valid `OptimisationResult`. They must not raise
    on insufficient site data — they report it via `solver_status`/`warnings` instead.
    """

    def build_optimisation_request(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario_set: str | None = None,
    ) -> OptimisationRequest:
        """Assemble the request envelope the engine will solve."""
        ...

    def solve(self, request: OptimisationRequest) -> OptimisationResult:
        """Solve a request into an `OptimisationResult`."""
        ...
