"""The sizing objective (M4) — maximise levered return s.t. P90 DSCR ≥ covenant.

The bankable objective from the System Brief: **search power/energy/duration to maximise the
levered (equity) return, subject to the hard constraint that the P90 DSCR meets the covenant**,
with the asset sized day-one for end-of-life capability (no augmentation, so the binding year
may be late-life). The covenant is *embedded* in the objective (a feasibility gate), not bolted
on afterwards — a candidate the covenant would reject can never be selected.

Honest status:
- The **primary metric is the levered (equity) return** — the finance model's equity IRR on the
  P50 base case (``FinancialCase.equity_irr``), computed whenever real `DebtTerms` are injected.
  Without injected debt there is no levered split, so the equity IRR is ``None`` and the objective
  ranks on the *unlevered* project IRR as a labelled fallback (and never asserts a bankable best
  on the fallback). It never invents a levered return.
- The **feasibility gate is real**: it needs the P90 DSCR (which needs injected `DebtTerms` — the
  library carries no debt structure in v1) and the covenant from the assumption library, and it
  rejects any spec *known* to be non-deliverable to its final contract year. When the inputs are
  present the gate genuinely fires and rejects sub-covenant / non-deliverable candidates.

End-of-life capability is sized day-one (no augmentation, so the binding year may be late-life).
Degradation already flows through finance (revenue fade → lower late-life P90 CFADS → lower P90
DSCR), so it is *not* re-modelled here; the gate only rejects a spec with an explicit
non-deliverable verdict, and treats an *unassessed* verdict (``None``) as a data-ask (per decision
D12 — the late-life-capability check lands with the real engine / a generic GateContext), never a
fabricated pass.

This module holds only the *policy* (feasibility + ranking). The `gridq.sizing.sizer` orchestrates
enumeration and selection against it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gridq.sizing.search_space import SizingCandidate


@runtime_checkable
class Evaluation(Protocol):
    """The metrics the objective reads off one evaluated candidate (see `CandidateEvaluation`).

    Declared as read-only properties so a frozen-dataclass `CandidateEvaluation` (and a plain test
    double) both satisfy it structurally.
    """

    @property
    def candidate(self) -> SizingCandidate: ...
    @property
    def min_p90_dscr(self) -> float | None: ...
    @property
    def levered_return_pct(self) -> float | None: ...
    @property
    def project_irr_pct(self) -> float | None: ...
    @property
    def end_of_life_deliverable(self) -> bool | None: ...


@dataclass(frozen=True)
class SizingObjective:
    """Bankable sizing objective: maximise levered return s.t. P90 DSCR ≥ covenant.

    ``min_p90_dscr_covenant`` is the covenant ratio (read from the assumption library by the
    caller, e.g. ``dscr_covenants.min_p90_dscr``); ``None`` means the covenant is unknown, so no
    candidate can be proven feasible.
    """

    min_p90_dscr_covenant: float | None

    def feasible(self, evaluation: Evaluation) -> bool:
        """True iff the P90 DSCR meets the covenant **and** the spec is not known non-deliverable.

        A missing covenant or a missing/unavailable DSCR ⇒ not feasible (never assume it passes).
        An explicit ``end_of_life_deliverable is False`` rejects the spec; an *unassessed* verdict
        (``None``, the placeholder/v1 case) does not block — it is a data-ask, not a fabricated
        pass (decision D12).
        """
        if self.min_p90_dscr_covenant is None:
            return False
        if evaluation.min_p90_dscr is None:
            return False
        if evaluation.end_of_life_deliverable is False:
            return False
        return evaluation.min_p90_dscr >= self.min_p90_dscr_covenant

    def primary_metric_available(self, evaluation: Evaluation) -> bool:
        """Whether the *primary* objective metric (the levered/equity return) is computed here.

        ``True`` once the finance model returns an equity IRR (real `DebtTerms` injected); ``False``
        when the run falls back to the unlevered project IRR — used to flag that a ranking on the
        fallback is not yet a bankable optimisation.
        """
        return evaluation.levered_return_pct is not None

    def rank_key(self, evaluation: Evaluation) -> tuple[int, float, int, float, float, float]:
        """A deterministic sort key where the maximum is the best candidate.

        NOTE: the smaller-system components are TIE-BREAKS among candidates that carry a return
        metric — never a selection basis on their own. A pool where no candidate carries any
        return metric must not be ranked into a "recommendation" at all: that is the
        `candidate_viable` gate (a red-team fix, 2026-07-07 — the pre-fix behaviour selected the
        smallest grid corner purely via this tie-break and presented it as the optimum).

        Prefers, in order: a candidate with a levered return, then the higher levered return;
        then a candidate with a (fallback) project IRR, then the higher IRR; then the smaller
        system (smaller power, then smaller duration) as a deterministic tie-break. Unavailable
        metrics sort last via their availability flag, so ``None`` is never compared as a number.
        """
        levered = evaluation.levered_return_pct
        irr = evaluation.project_irr_pct
        return (
            1 if levered is not None else 0,
            levered if levered is not None else 0.0,
            1 if irr is not None else 0,
            irr if irr is not None else 0.0,
            -evaluation.candidate.power_kw,
            -evaluation.candidate.duration_hours,
        )


def candidate_viable(evaluation: Evaluation) -> bool:
    """Whether a candidate is economically viable enough to be *recommendable* at all.

    Viable ⇔ at least one computed return metric is strictly positive: a levered return > 0 or a
    project IRR > 0. A candidate whose cashflows never return capital (IRR ``None`` — no
    sign-change, or a computed negative IRR) is evidence for the sizing surface, never a
    recommendation: presenting the least-bad loss-maker as "the optimum" is the exact failure
    the red-team E2E found (a 4 kW grid-corner "recommendation" on an unviable site). Covenant
    state is deliberately not consulted here — this is the weaker, debt-free floor test used by
    the engine's indicative fallback; the covenant path (`SizingObjective.feasible`) is stricter.
    """
    levered = evaluation.levered_return_pct
    irr = evaluation.project_irr_pct
    return (levered is not None and levered > 0.0) or (irr is not None and irr > 0.0)
