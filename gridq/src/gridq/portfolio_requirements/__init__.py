"""Portfolio requirements solver — *what portfolio would it take* to earn a target levered IRR.

This module answers the inverse of the usual question. The engine normally asks "what is the IRR
of this configuration"; this asks **"what configuration would be required to reach 20% levered
IRR, using realistic numbers"** — and refuses to answer with a number when the answer would sit
outside defensible bounds.

Design authored by the Fable advisor (2026-08-05) and implemented here unchanged in substance.
Two layers over one deterministic cashflow kernel:

* **Kernel** (:func:`evaluate`) — per-MW cashflow with **DSCR-sized debt**. Debt is never asserted
  as a gearing percentage; it is sized as ``min(cap x capex, PV(CFADS_P90 / DSCR_target))``. This
  is the integrity feature: it makes value-destructive leverage structurally impossible, and it is
  the specific pathology found in ``Sim Work/Model_v5.xlsx`` (60% gearing asserted against a
  0.996x DSCR).
* **Layer 1** (:func:`frontier`) — grid map over capex/MW x net revenue/MW, with iso-levered-IRR
  contours and a shaded *defensible region* built from the tagged assumption bounds.
* **Layer 2** (:func:`required_revenue_for_target`) — per-archetype root-find on the single free
  variable (required net revenue/MW) such that levered IRR hits the target, then decomposes that
  revenue into the GB stack and tags every line.

Determinism: pure arithmetic, bisection root-find, no stochastic search, no LLM anywhere. Every
input carries a ``(value, tag, source)`` triple via :class:`Assumption`, so a result can always be
traced to the assumption set that produced it.

The honest verdict this module encodes (Fable advisor, confirmed by the corrected Model_v5 matrix):
**20% levered is not reachable at defensible Central inputs for a GB BTM BESS portfolio at any
scale.** IRR is scale-invariant above the fixed-cost floor, and small scale makes it worse through
the capex/opex floors. The realistic own-and-operate ceiling is ~10-14% levered. 20% is the
property of a specific top-decile host archetype — which is what the spec card describes.
"""

from __future__ import annotations

from gridq.portfolio_requirements.assumptions import (
    ARCHETYPES,
    REVENUE_STACK,
    Archetype,
    Assumption,
    AssumptionSet,
    FinancingTier,
    Tag,
    central_set,
)
from gridq.portfolio_requirements.kernel import (
    CashflowResult,
    evaluate,
    irr,
)
from gridq.portfolio_requirements.solver import (
    FrontierPoint,
    RequirementResult,
    SpecCard,
    frontier,
    required_revenue_for_target,
    solve_spec_card,
    verdict,
)

__all__ = [
    "ARCHETYPES",
    "REVENUE_STACK",
    "Archetype",
    "Assumption",
    "AssumptionSet",
    "CashflowResult",
    "FinancingTier",
    "FrontierPoint",
    "RequirementResult",
    "SpecCard",
    "Tag",
    "central_set",
    "evaluate",
    "frontier",
    "irr",
    "required_revenue_for_target",
    "solve_spec_card",
    "verdict",
]
