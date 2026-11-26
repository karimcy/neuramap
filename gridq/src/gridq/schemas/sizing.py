"""Sizing evaluation grid — the persisted record of every candidate the optimiser evaluated.

The sizing optimiser (M4) evaluates a power×duration grid and selects the best feasible size, but
the frozen `OptimisationResult` carries only the *chosen* candidate's spec. `SizingGrid` is the
additive, serializable projection of the full evaluation — every candidate with its levered
(equity) return, P90 DSCR and covenant feasibility — that the Wave 5 **3D sizing surface** plots
(power × duration × levered return, the P90-DSCR-feasible region highlighted, the optimum marked).
It is built from a `SizingRun` (`gridq.sizing.sizer.SizingRun.sizing_grid`) and persisted onto the
`QuoteRecord` so the surface is plottable from saved run data, not recomputed ad-hoc.

No optimisation logic lives here — only the shared shape. It carries **no invented numbers**: every
field is copied straight off a `CandidateEvaluation` (the revenue/cost fields one hop deeper, off
the evaluation's own `FinancialModelResult.cashflows` and `OptimisationResult.value_streams`), and
an unavailable metric stays ``None`` (no levered return without injected debt; no P90 DSCR without a
debt structure; no floor revenue without a cashflow model) — never coerced.
"""

from __future__ import annotations

from pydantic import Field

from gridq.schemas.enums import SolverStatus
from gridq.schemas.provenance import GridQModel

#: The version of the SIZING-GRID contract specifically, overriding `GridQModel`'s inherited
#: package-wide default on the three models below. Bumped **deliberately** (CLAUDE.md §7 —
#: "change deliberately, versioned"), because this projection gained fields that change what a
#: reader may conclude from it rather than merely how it is spelled:
#:
#: * ``0.1.0`` — the shape up to 2026-08-17: power/duration/energy, the two return metrics, the
#:   DSCR + covenant flag, the solver/stage/proxy provenance, and **no revenue field of any kind**.
#:   Any persisted grid without a ``schema_version`` of ``0.2.0`` is this shape, and a reader must
#:   not treat its silence about revenue as a measurement of zero.
#: * ``0.2.0`` — adds the per-candidate priced floor (`SizingGridPoint.annual_revenue_gbp`), its
#:   recorded cost stack, and the per-stream breakdown (`SizingGridStreamRevenue`). This is what
#:   lets a run that selected NOTHING still state what its best candidate measured, instead of
#:   leaving a reader to infer "this site earns nothing" from an absent field.
#:
#: **The global `SCHEMA_VERSION` is deliberately NOT bumped, and no ``run_id`` moves.** The grid is
#: excluded from the canonical hash input by design (`quotes.assembly.assemble_quote_record`
#: docstring; `_canonical_run_input` never reads it), so it feeds neither ``input_hash`` nor
#: ``quote_id`` nor ``run_id``; and the package-wide constant is pinned to
#: `OptimisationResult.schema_version` by `engine.conformance`, so moving it would re-mint every
#: identity in the repo to version an additive OUTPUT projection. D23/D25 precedent (see
#: `schemas.provisioning`): additive contract growth versions locally, not globally.
SIZING_GRID_SCHEMA_VERSION = "0.2.0"


class SizingGridStreamRevenue(GridQModel):
    """One value stream's year-1 revenue for one evaluated candidate, with its floor membership.

    A copy of the four fields of the candidate's own `ValueStreamResult` that a reader needs to
    tell one ``£0.00`` from another, and nothing else (the per-stream warning prose — a 250-word
    paragraph repeated across every stream of a no-spec run — stays on the top-level record where
    it is stated once).

    **Why `in_priced_floor` is stored rather than left to be re-derived.** A recorded zero has
    three different meanings and they must not collapse (D45):

    * ``in_priced_floor=True`` with ``annual_revenue_gbp == 0.0`` — the stream IS in the priced
      floor, the optimiser priced it, and the trade came out at nothing. A measured result.
    * ``in_priced_floor=False`` — the stream was held OUT of the floor by its firmness/gate
      (``gate_status`` says which), so its £ is upside and its zero is not a floor measurement at
      all. A held-out stream may equally carry a **non-zero** ``annual_revenue_gbp``: conditional
      capacity-market upside does exactly that, and summing it into the floor would be a false
      claim in the opposite direction.
    * The whole list empty, or `SizingGridPoint.annual_revenue_gbp` ``None`` — nothing was
      measured for this candidate. Never written as ``0.0``.

    It is stamped from `schemas.market.is_priced_floor`, the same predicate
    `finance.cashflows.revenue_by_stream` filters on, so the floor breakdown here and the floor
    total in `SizingGridPoint.annual_revenue_gbp` cannot disagree about which streams compose it.
    """

    schema_version: str = Field(default=SIZING_GRID_SCHEMA_VERSION)
    #: The `MarketStream` value (``str``, not the enum, so a persisted grid stays readable when the
    #: enum grows a member a reader's build does not have).
    stream: str
    #: Year-1 revenue for this stream on this candidate, verbatim from
    #: ``ValueStreamResult.annual_revenue_gbp``. Read it with ``in_priced_floor``, always.
    annual_revenue_gbp: float = Field(ge=0)
    #: The recorded `Firmness` value — the discriminator ``in_priced_floor`` is stamped from.
    firmness: str
    #: The recorded availability-gate state (``open``/``conditional``/``closed``, or ``None`` when
    #: the producer stated none) — WHY a held-out stream was held out.
    gate_status: str | None = None
    #: Whether this stream's £ is inside the priced floor this candidate's cashflows were built on.
    in_priced_floor: bool


class SizingGridPoint(GridQModel):
    """One evaluated (power, duration) candidate and the scalars the surface/objective read.

    Mirrors `gridq.sizing.sizer.CandidateEvaluation`, keeping only the plottable, serializable
    scalars (the heavy per-candidate `OptimisationResult`/`FinancialModelResult` are dropped).
    ``levered_return_pct`` is the equity IRR (P50 basis; ``None`` without injected debt — the
    surface's z-axis), ``project_irr_pct`` the unlevered fallback. ``passes_covenant`` is the
    P90-DSCR feasibility flag — ``True``/``False`` when the DSCR is available, ``None`` when it is
    not (no debt structure), never coerced to ``False``; ``min_p90_dscr`` is likewise ``None`` when
    unavailable.

    **The revenue fields (``0.2.0``) exist because their absence made the record say something
    false.** Up to 2026-08-17 a point carried every ratio the objective ranks on and not one £, so
    a run that selected nothing persisted an evaluated grid that could not state what any candidate
    had *earned*. The pack then read as "this site earns nothing" on sites whose own recorded
    verdict said the dispatch had discharged tens of MWh: the true sentence is "the best candidate
    earns £X/yr and still does not cover its costs", and only the second is a result rather than an
    implication. Both are negatives; only the second is evidenced. (It also makes an existing claim
    true: `optimisation.valuation` already tells readers "the per-candidate figures are in the
    sizing grid".)

    Read the £ fields by these rules:

    * ``annual_revenue_gbp`` is the **priced floor** — firm + contracted streams only, copied from
      ``CashflowModel.total_annual_revenue_gbp``, the identical figure the finance model built this
      candidate's cashflows on. It is NOT the sum of ``revenue_streams``, which also carries the
      held-out entries.
    * ``None`` on any £ field means **no cashflow model was built for this candidate** (see
      ``priceable``) — genuinely unmeasured, never a zero. A ``0.0`` means measured and zero.
    * The costs are the two recorded lines EBITDA nets off, kept separate exactly as
      `CashflowModel` keeps them, so "does not cover its costs" can be checked rather than
      asserted. Neither is escalated and neither includes capex (year-1 basis, as elsewhere).
    """

    power_kw: float = Field(ge=0)
    duration_hours: float = Field(ge=0)
    energy_kwh: float = Field(ge=0)
    levered_return_pct: float | None = None
    project_irr_pct: float | None = None
    min_p90_dscr: float | None = None
    passes_covenant: bool | None = None
    priceable: bool
    bankable: bool
    solver_status: SolverStatus
    end_of_life_deliverable: bool | None = None
    # Two-stage search provenance (D32; additive — absent on pre-v2 records, defaults apply).
    # ``stage`` is which search stage produced the point (``coarse``/``refine``/``confirm``;
    # ``grid`` for the legacy enumeration). ``proxy`` marks a typical-period scoring solve whose
    # numbers guided the search but were never reported as the chosen size (the confirm stage
    # re-solves on the full series).
    stage: str = "grid"
    proxy: bool = False
    # --- per-candidate value (0.2.0; additive — a 0.1.0 record defaults to None/[]) ------------
    #: Year-1 PRICED-FLOOR revenue for this candidate (£), verbatim from
    #: ``CashflowModel.total_annual_revenue_gbp``. ``None`` ⇔ no cashflow model was built (an
    #: unpriceable candidate), which is an absent figure and must never be read as £0.
    annual_revenue_gbp: float | None = None
    #: Year-1 total of the available operating cost lines (£; excludes capex), verbatim from
    #: ``CashflowModel.total_annual_operating_cost_gbp``. ``None`` on the same terms as above.
    annual_operating_cost_gbp: float | None = None
    #: Year-1 dispatch (charging-energy) cost (£), verbatim from
    #: ``CashflowModel.annual_dispatch_cost_gbp``. Reported separately because EBITDA nets it
    #: separately, so revenue-vs-cost is checkable from the record. ``None`` on the same terms.
    annual_dispatch_cost_gbp: float | None = None
    #: Every value stream this candidate's own dispatch reported, floor and held-out alike, each
    #: with its firmness/gate and its floor membership. Empty ⇔ the candidate's dispatch reported no
    #: streams at all (read ``solver_status``) — which is silence, not a zero.
    revenue_streams: list[SizingGridStreamRevenue] = Field(default_factory=list)
    schema_version: str = Field(default=SIZING_GRID_SCHEMA_VERSION)


class SizingGrid(GridQModel):
    """The full evaluated sizing grid + the chosen optimum — the 3D surface's data source.

    ``points`` is every evaluated candidate in deterministic ``(power, duration)`` order (inherited
    from `SearchSpace.candidates()`); ``selected`` is the chosen optimum — the covenant-feasible
    best, the unlevered best, the indicatively-chosen fallback, or ``None`` when none exists, which
    is honest, not an error. ``selection_basis`` names which of the four it is (they are enumerated
    on the field itself below), and must be read alongside it:
    the grid used to report ``selected=None`` and "no bankable candidate" on runs whose
    `OptimisationResult` said ``selection_status=SELECTED`` and named a kW, with both persisted on
    the same `QuoteRecord` (N16). ``covenant`` is the P90-DSCR covenant the feasibility flag is
    measured against (``None`` when unknown). ``bankable`` mirrors `SizingRun.bankable` — never
    ``True`` on the placeholder.
    """

    search_space_size: int = Field(ge=0)
    covenant: float | None = None
    bankable: bool
    selection_reason: str
    points: list[SizingGridPoint] = Field(default_factory=list)
    selected: SizingGridPoint | None = None
    #: How ``selected`` was chosen (N16; additive — historical records default to the
    #: pre-N16 meaning of the field). ``covenant``: a P90 DSCR was measured for it and MET the
    #: covenant — the objective's optimum, and the only basis that is one. ``unlevered``: no debt
    #: structure was resolved on the sizing path, so no covenant could be applied to any candidate
    #: and the size was ranked on the unlevered project IRR (`validation_requirements.md` §3) — it
    #: has NEITHER passed nor failed a covenant, and the confidence band is capped for it.
    #: ``indicative_fallback``: the covenant gate ran and no candidate met it, so this is the point
    #: the engine's labelled fallback picked — a recommendation, not a feasible optimum, and
    #: `selection_reason` names the metric that picked it. ``none``: nothing was chosen. ``None``
    #: on a pre-N16 record: not recorded, which is not the same as ``"none"`` and is never read
    #: as either of the other three.
    selection_basis: str | None = None
    # Which search produced this cloud (D32; additive). ``enumerate-v1`` is the legacy 3x3 grid;
    # ``m4-two-stage-v2`` is the coarse-sweep + seeded-refinement continuous search.
    search_version: str = "enumerate-v1"
    #: The SIZING-GRID contract version (`SIZING_GRID_SCHEMA_VERSION`) — ``0.2.0`` since the
    #: per-candidate revenue fields landed. A grid stamped ``0.1.0`` carries no revenue at all and
    #: its silence is not a zero. Deliberately grid-local: this projection is excluded from the
    #: canonical hash input, so no ``run_id`` depends on it.
    schema_version: str = Field(default=SIZING_GRID_SCHEMA_VERSION)
