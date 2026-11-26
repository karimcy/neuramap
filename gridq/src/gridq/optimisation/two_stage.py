"""The two-stage deterministic sizing search (M4 v2, D32) — coarse sweep + seeded refinement.

OPT-IN (`MilpEngine(use_two_stage_search=True)`) — the engine default stays the legacy 3x3
enumeration per the C025 gate (north-star D29): on per-MW-only capex any wide-duration search
pins the recommendation to the band edge, so a continuous recommendation would be a placeholder
artifact. A continuous search over the (power, duration) box:

- **Stage A — coarse sweep:** a wide deterministic grid, power spanning a **two-anchor** domain
  (5% of the site's LOAD-relevant scale up to 150% of its binding capacity — see
  `load_relevant_capacity_kw`) x durations 0.5-8 h, scored against a **typical-period proxy** (one
  medoid day per month x weekday/weekend stratum, weighted — `gridq.sizing.typical_periods`) so
  each candidate dispatch costs a fraction of a full-year solve.
- **Stage B — seeded refinement:** deterministic differential evolution
  (`gridq.sizing.refine`, ~200 evaluation budget) over the same box, seeded with the coarse
  bests, covenant-gated throughout via the injected `SizingObjective`.
- **Stage C — confirmation:** the top-ranked candidates are re-solved on the **full metered
  series**; only confirmed (non-proxy) evaluations can be selected or reported. Proxy numbers
  guide the search and are persisted in the cloud clearly labelled ``proxy`` — they never become
  the chosen size's figures.

Every evaluated candidate (all three stages) is retained on the returned `SizingRun`, so the
persisted `SizingGrid` is a true continuous sizing surface, not a 9-point grid. Deterministic by
construction: fixed stage grids, seeded refinement, memoised evaluations, stable ordering —
identical inputs give the identical selection and the identical cloud.

The coarse fractions/durations and bounds below are **labelled structural constants** (the same
precedent as the legacy ``POWER_FRACTIONS``/``DEFAULT_DURATION_HOURS`` — they inject no £/% into
any output; power candidates stay fractions of the site's own binding capacity, never an invented
absolute kW).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

from gridq.assumptions import AssumptionLibrary
from gridq.dispatch import dispatch_constraints_for
from gridq.finance import FinancialModelResult, run_financial_model
from gridq.finance.debt import DebtTerms
from gridq.ingestion import HalfHourlyDataError
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.proxy_solver import PreloadedDispatchSolver
from gridq.schemas import MarketStream, SiteModel
from gridq.sizing.objective import SizingObjective, candidate_viable
from gridq.sizing.refine import RankKey, refine_sizing
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import (
    CandidateEvaluation,
    ProgressSink,
    SizingRun,
    _bankability_warning,
    _emit_progress,
    _evaluate_spec,
    _select,
)
from gridq.sizing.typical_periods import select_typical_days
from gridq.value_stack.registry import StreamRegistry, default_registry

SEARCH_VERSION = "m4-two-stage-v2"

# Structural search-space constants (labelled; inject no £/%). The fractions below are applied to
# TWO anchors, because one number cannot serve both edges of the power axis (see
# `load_relevant_capacity_kw`): the UPPER edge is 150% of the site's binding capacity (the
# connection, where one is evidenced), the LOWER edge 5% of the site's own load-relevant scale.
# Duration spans 0.5-8 h. The coarse sweep, the refinement bounds, the polish clamp and the N6
# cover all read that one domain.
COARSE_POWER_FRACTIONS: tuple[float, ...] = (
    0.05,
    0.10,
    0.20,
    0.35,
    0.50,
    0.70,
    0.90,
    1.10,
    1.30,
    1.50,
)
COARSE_DURATION_HOURS: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)
POWER_FRACTION_BOUNDS: tuple[float, float] = (0.05, 1.50)
DURATION_BOUNDS_HOURS: tuple[float, float] = (0.5, 8.0)
# How many top-ranked candidates are re-solved on the full series before selection (widened
# from 5 per the D34 addendum follow-up: the adaptive refiner beat the old first-feasible-wins
# confirm by +0.59pp on the corrected surface). ALL confirmed candidates compete and the BEST
# feasible one wins — never merely the first to pass. Structural constant; injects no £/%.
CONFIRM_TOP_N = 10
# Candidates within this relative spacing of an already-chosen confirm candidate (on BOTH
# axes) are skipped when building the confirm list: the top of the proxy ranking is typically
# a cluster of near-identical neighbours, and confirming ten clones of one point buys nothing.
# Diversifying the confirm set is what lets the wider budget actually cover the optimum region.
CONFIRM_MIN_RELATIVE_SPACING = 0.02
# Stage-D polish: a bounded deterministic compass search on the FULL series around the best
# confirmed candidate (±step on each axis, halving per round). The typical-day proxy ranks the
# optimum's neighbourhood slightly wrong (D34 addendum: the adaptive refiner measured +0.59pp
# over the pre-polish confirm), so a handful of exact solves around the confirmed best recovers
# the true peak. Structural constants; the probe count is hard-capped.
POLISH_ROUNDS = 2
POLISH_STEP_RELATIVE = 0.05
POLISH_MAX_PROBES = 10
# N6 last-resort cover: the size-diverse set re-solved on the FULL metered series before any
# FINAL "no viable configuration" verdict is allowed to stand. Spans the search box on both axes
# (corners included) rather than the proxy ranking, whose top 10 degenerate to the ten SMALLEST
# candidates when nothing carries a positive metric — the exact situation a no-viable verdict is
# rendered in. Structural constants (labelled; inject no £/%); the solve count is hard-capped.
NO_VIABLE_COVER_POWER_FRACTIONS: tuple[float, ...] = (0.05, 0.35, 0.70, 1.50)
NO_VIABLE_COVER_DURATION_HOURS: tuple[float, ...] = (0.5, 2.0, 8.0)
# The cap is DERIVED, not chosen: the cover's power set is the fractions applied to BOTH anchors of
# the power domain (`_two_anchor_powers`), so a COMPLETE cross product is at most 2 x powers x
# durations. Expressing the cap that way is the point — a cover silently truncated below its own
# span would be the very defect N6 exists to prevent (it would drop the top of the power range,
# which is exactly what the cover is there to reach). Structural; injects no £/%.
NO_VIABLE_COVER_MAX_SOLVES = (
    2 * len(NO_VIABLE_COVER_POWER_FRACTIONS) * len(NO_VIABLE_COVER_DURATION_HOURS)
)


def connection_power_limit_kw(site: SiteModel) -> float | None:
    """The site's DECLARED physical import limit (kW), or ``None`` when none is declared.

    N7. The binding limit is ``min(MIC, ASC)`` — the same convention
    `sizing.provisioning.search_floors` and the solver's charge cap use, so the search box and
    the dispatch that fills it agree about what the connection supports. The observed peak is
    deliberately NOT a fallback here: the peak is a fact about the load, and a provisioning
    obligation exceeding it is normal, not a reason to declare reinforcement.
    """
    connection = getattr(site, "connection", None)
    limits = [
        float(v)
        for v in (getattr(connection, "mic_kw", None), getattr(connection, "asc_kw", None))
        if v is not None and v > 0
    ]
    return min(limits) if limits else None


def load_relevant_capacity_kw(
    site: SiteModel, binding_capacity_kw: float, *, series_peak_kw: float | None = None
) -> float:
    """The scale the SMALL end of the power axis is anchored on: the smallest evidenced capacity.

    The binding capacity anchors the TOP of the search box on the connection — a battery is sized
    to the wire, and anchoring the box on the load hid evidenced headroom (`engine._sizing_basis`).
    But one number cannot serve both edges. Behind-the-meter discharge is capped at
    ``min(power, load)`` (`milp_solver`'s ``discharge_ub``), so on a low-load/large-connection site
    the economically relevant power band is roughly ``[0, peak]`` — and re-anchoring on the
    connection raised ``0.05 x basis`` clean out of it. Measured: Knowl Hill's smallest searchable
    system went from 1.13 kW to 16.25 kW against a 22.6 kW peak, leaving ONE coarse power point in
    the entire band where a viable specification would first appear.

    So the lower edge is anchored on the smallest capacity the site evidences: the binding capacity
    itself, the declared limit ``min(MIC, ASC)``, the declared ``max_import_kw`` and — when the
    caller has the series in hand — its measured peak. Taking the MINIMUM over all of them is what
    makes this safe: the floor can only ever FALL, never rise, so the candidate set can only widen
    and no size any anchor evidences is ever excluded. The measured series peak is included because
    it is the exact quantity ``discharge_ub`` caps against; where a declared peak also exists the
    two agree, and the smaller of the two wins either way.

    Returns the binding capacity unchanged when it is the only positive scale (so a site with no
    declared limit and no peak searches exactly as before).
    """
    scales = [float(binding_capacity_kw)]
    limit_kw = connection_power_limit_kw(site)
    if limit_kw is not None:
        scales.append(float(limit_kw))
    declared_peak = getattr(site.load_data, "max_import_kw", None)
    if declared_peak is not None and float(declared_peak) > 0:
        scales.append(float(declared_peak))
    if series_peak_kw is not None and float(series_peak_kw) > 0:
        scales.append(float(series_peak_kw))
    positive = [s for s in scales if s > 0]
    return min(positive) if positive else float(binding_capacity_kw)


def power_search_floor_kw(relevant_capacity_kw: float) -> float:
    """The smallest searchable power (kW) — the LOWER edge of the two-anchor power domain.

    One expression, read by the coarse sweep, the refinement bounds, the polish clamp and the N6
    cover, so a candidate cannot be reachable by one stage and unreachable by the next.
    """
    return round(POWER_FRACTION_BOUNDS[0] * float(relevant_capacity_kw), 3)


def _two_anchor_powers(
    fractions: tuple[float, ...], binding_capacity_kw: float, relevant_capacity_kw: float
) -> list[float]:
    """``fractions`` applied to BOTH anchors of the power domain, deduplicated and sorted.

    The binding-capacity ladder is unchanged — it sets the top of the box. The load-relevant ladder
    contributes only the points BELOW the binding ladder's own floor (``0.05 x binding``): exactly
    the sub-domain the binding ladder cannot reach, and nothing that would merely crowd a region it
    already covers. When the two anchors coincide (any site whose load peak is at or above its
    connection, and every site with no declared limit) the second ladder is empty and the result is
    the same set of powers as before, computed by the same expression.
    """
    powers = {round(f * float(binding_capacity_kw), 3) for f in fractions}
    floor = POWER_FRACTION_BOUNDS[0] * float(binding_capacity_kw)
    if float(relevant_capacity_kw) < float(binding_capacity_kw):
        powers |= {
            round(f * float(relevant_capacity_kw), 3)
            for f in fractions
            if f * float(relevant_capacity_kw) < floor - 1e-9
        }
    return sorted(p for p in powers if p > 0)


def coarse_power_grid_kw(binding_capacity_kw: float, relevant_capacity_kw: float) -> list[float]:
    """The Stage-A coarse power ladder over the two-anchor domain (ascending, deduplicated)."""
    return _two_anchor_powers(COARSE_POWER_FRACTIONS, binding_capacity_kw, relevant_capacity_kw)


def provisioning_reinforcement_reason(
    power_floor_kw: float, energy_floor_kwh: float, limit_kw: float | None
) -> str | None:
    """The FINAL connection-reinforcement verdict, or ``None`` when the floor is buildable.

    N7 (2026-08-10 review): reinforcement used to be declared whenever the provisioning floor
    escaped the COARSE SWEEP — a structural search constant (1.5x the site's own peak, 8 h), not
    a limit. A 400 kW-peak site with a 700 kW floor and a 1,000 kW MIC was told "no battery this
    connection supports can meet the obligation" about a battery its connection plainly supports.

    Only the POWER floor can produce this verdict: duration is bought with cells, not with
    connection capacity, so an energy floor at or below the limit is always buildable (long, and
    the caller says so). With no declared limit the verdict is unavailable — an undeclared MIC is
    a data ask, never grounds for a final refusal.
    """
    if limit_kw is None:
        return None
    if power_floor_kw <= limit_kw + 1e-6:
        return None
    return (
        "no viable configuration: the provisioning floor "
        f"({power_floor_kw:.0f} kW / {energy_floor_kwh:.0f} kWh at end-of-tenor capability) "
        f"requires more import capacity than this connection has — its declared limit is "
        f"{limit_kw:.0f} kW (min of MIC/ASC). The obligation cannot be met by any battery this "
        "connection supports; the honest recommendation is connection reinforcement "
        "(issue 017 / D39)."
    )


def provisioning_cover_pairs(
    power_floor_kw: float,
    energy_floor_kwh: float,
    limit_kw: float | None,
    *,
    steps: tuple[float, ...] = (1.0, 1.25, 1.6, 2.0),
) -> list[tuple[float, float]]:
    """(power, duration) candidates that MEET a provisioning floor lying outside the coarse box.

    N7. The search box is scaled from the site's own load; an obligation is not, so a floor can
    sit above every coarse candidate while staying inside the connection. Rather than refuse, the
    box is extended to cover the floor: the smallest power that can carry it (never below the
    power floor, never above the declared limit), then a few larger ones so the search has a
    gradient to work with, each at exactly the duration the ENERGY floor needs. Deterministic and
    admissible by construction — every pair satisfies both floors.
    """
    if power_floor_kw <= 0 and energy_floor_kwh <= 0:
        return []
    # The smallest power that can meet the energy floor within the standard duration cap, floored
    # by the power obligation itself. Below this no duration helps.
    base = max(power_floor_kw, energy_floor_kwh / DURATION_BOUNDS_HOURS[1])
    if limit_kw is not None:
        base = min(base, limit_kw)
    if base <= 0:
        return []
    pairs: list[tuple[float, float]] = []
    for step in steps:
        power = round(base * step, 3)
        if limit_kw is not None and power > limit_kw + 1e-6:
            power = round(limit_kw, 3)
        # Round the duration UP (2 dp) so power x duration clears the energy floor exactly.
        needed = energy_floor_kwh / power if power > 0 else 0.0
        duration = max(DURATION_BOUNDS_HOURS[0], math.ceil(needed * 100.0) / 100.0)
        pair = (power, round(duration, 2))
        if pair not in pairs:
            pairs.append(pair)
    return pairs


def no_viable_cover_pairs(
    already_confirmed: set[tuple[float, float]],
    binding_capacity_kw: float,
    admits: Callable[[float, float], bool],
    *,
    relevant_capacity_kw: float | None = None,
) -> list[tuple[float, float]]:
    """The size-diverse full-series cover to solve before a FINAL no-viable verdict (N6).

    Spans the search box's power and duration ranges (corners included), drops anything the
    provisioning floor forbids or that was already confirmed, and is hard-capped at
    ``NO_VIABLE_COVER_MAX_SOLVES``. ``admits(power_kw, energy_kwh)`` is the floor test.

    ``relevant_capacity_kw`` is the power domain's LOWER anchor (`load_relevant_capacity_kw`); the
    cover spans the SAME two-anchor domain as the grid it covers, because a cover reaching a
    different range from the search it is meant to vouch for is a defect in itself — the verdict it
    licenses is "this asset does not work at ANY size", and the low, cheap sizes are precisely where
    a positive metric would first appear. ``None`` (the default) means the two anchors coincide and
    the cover is the binding-capacity ladder exactly as before.
    """
    powers = _two_anchor_powers(
        NO_VIABLE_COVER_POWER_FRACTIONS,
        binding_capacity_kw,
        binding_capacity_kw if relevant_capacity_kw is None else relevant_capacity_kw,
    )
    pairs: list[tuple[float, float]] = []
    for power in powers:
        for duration in NO_VIABLE_COVER_DURATION_HOURS:
            if len(pairs) >= NO_VIABLE_COVER_MAX_SOLVES:
                return pairs
            pair = (power, round(duration, 2))
            if pair in already_confirmed or pair in pairs:
                continue
            if not admits(power, power * duration):
                continue
            pairs.append(pair)
    return pairs


def two_stage_sizing(
    site: SiteModel,
    assumptions: AssumptionLibrary,
    *,
    base_solver: MilpDispatchSolver,
    objective: SizingObjective,
    binding_capacity_kw: float,
    requested_streams: list[MarketStream] | None = None,
    debt_terms: DebtTerms | None = None,
    horizon_years: int | None = None,
    scenario: object | None = None,
    registry: StreamRegistry | None = None,
    finance_runner: Callable[..., FinancialModelResult] = run_financial_model,
    seed: int = 0,
    progress: ProgressSink | None = None,
) -> SizingRun:
    """Run the coarse-sweep + seeded-refinement + full-series-confirmation sizing search.

    ``binding_capacity_kw`` is the site's own scale — the declared connection limit where one is
    evidenced, the observed peak otherwise (`engine._sizing_basis` derives it). It anchors the TOP
    of the power axis only; the floor is anchored on `load_relevant_capacity_kw`, derived here from
    the same site and series. Falls back to the legacy behaviour only in shape, never in honesty:
    with no usable half-hourly series every candidate degrades to ``INSUFFICIENT_DATA`` exactly as
    before (see `MilpEngine`, which short-circuits that case before calling this).

    ``progress`` is an optional presentation-only side-channel sink (the web run screen), fired
    once per ACTUAL dispatch solve at each of the four real solve sites: coarse
    (``("coarse", i, len(coarse_pairs))``), refine (``("refine", n, None)`` — no known total),
    confirm (``("confirm", i, len(to_confirm))``) and polish
    (``("polish", probes_used, POLISH_MAX_PROBES)``). Floor-skipped and memo-hit candidates cost
    nothing and are not emitted. Guarded so it can never perturb the deterministic solve; default
    ``None`` is byte-identical to omitting it (see `_emit_progress`).
    """
    reg = registry if registry is not None else default_registry()
    streams = requested_streams if requested_streams is not None else reg.stream_ids()
    constraints = dispatch_constraints_for(streams, site, assumptions, registry=reg)
    warnings: list[str] = []

    # One read of the metered series (one confidential-access log entry) for the whole search.
    load, timestamps, interval_min, load_warn = base_solver._load_series(
        site.load_data.half_hourly_file_ref or ""
    )
    warnings.extend(load_warn)
    if load.size == 0:
        raise HalfHourlyDataError("the half-hourly series has no usable (non-null) load readings")

    # Issue 017 / D42 (P-C): declared obligations put a hard feasibility floor under the
    # search — derived once from the full series, enforced at candidate generation (the
    # per-candidate dispatch enforces the obligation exactly either way; the floor avoids
    # wasted solves and carries the verdict). Recharge infeasibility is final at ANY size.
    from gridq.sizing.provisioning import ProvisioningInfeasibleError, search_floors

    prov_floors = search_floors(site, load, timestamps, interval_min / 60.0, assumptions)
    warnings.extend(prov_floors.warnings)
    if prov_floors.infeasible_reason is not None:
        raise ProvisioningInfeasibleError(prov_floors.infeasible_reason)

    typical = select_typical_days(timestamps, load, interval_min=interval_min)
    if typical is not None:
        warnings.append(typical.note)
        search_solver: PreloadedDispatchSolver = PreloadedDispatchSolver(
            base_solver,
            load=load[typical.positions],
            timestamps=timestamps[typical.positions],
            interval_min=interval_min,
            interval_weights=typical.interval_weights,
            extra_warnings=(typical.note,),
        )
    else:
        warnings.append(
            "sizing-search proxy unavailable (series too thin to stratify); the search scored "
            "every candidate on the full metered series."
        )
        search_solver = PreloadedDispatchSolver(
            base_solver, load=load, timestamps=timestamps, interval_min=interval_min
        )
    confirm_solver = PreloadedDispatchSolver(
        base_solver, load=load, timestamps=timestamps, interval_min=interval_min
    )

    evaluations: dict[tuple[float, float], CandidateEvaluation] = {}

    def evaluate(power: float, duration: float, *, stage: str, proxy: bool) -> CandidateEvaluation:
        key = (power, duration)
        if key in evaluations:
            return evaluations[key]
        solver = search_solver if proxy or stage != "confirm" else confirm_solver
        evaluation = replace(
            _evaluate_spec(
                site,
                assumptions,
                SizingCandidate(power_kw=power, duration_hours=duration),
                dispatch_solver=solver,
                streams=list(streams),
                constraints=constraints,
                debt_terms=debt_terms,
                horizon_years=horizon_years,
                scenario=scenario,
                finance_runner=finance_runner,
                model_version=solver.MODEL_VERSION,
                registry=reg,
            ),
            stage=stage,
            proxy=proxy,
        )
        evaluations[key] = evaluation
        return evaluation

    def rank_key(evaluation: CandidateEvaluation) -> RankKey:
        return (
            1.0 if objective.feasible(evaluation) else 0.0,
            *(float(x) for x in objective.rank_key(evaluation)),
        )

    proxy_mode = typical is not None

    # --- Stage A: coarse sweep ------------------------------------------------
    # The power axis has TWO anchors (`load_relevant_capacity_kw`): its top tracks the CONNECTION
    # the battery must charge through, its floor the site's own load-relevant scale. One number
    # cannot serve both edges when the two differ by an order of magnitude, and on this portfolio of
    # low-load/large-connection sites they do.
    relevant_capacity_kw = load_relevant_capacity_kw(
        site, binding_capacity_kw, series_peak_kw=float(load.max())
    )
    box_power_lo = power_search_floor_kw(relevant_capacity_kw)
    powers = coarse_power_grid_kw(binding_capacity_kw, relevant_capacity_kw)
    if relevant_capacity_kw < binding_capacity_kw:
        warnings.append(
            "sizing power axis spans TWO anchors: its upper edge is "
            f"{POWER_FRACTION_BOUNDS[1]:.2f}x the binding capacity "
            f"({binding_capacity_kw:,.1f} kW — the connection the battery charges through), its "
            f"lower edge {POWER_FRACTION_BOUNDS[0]:.2f}x the site's load-relevant capacity "
            f"({relevant_capacity_kw:,.1f} kW), i.e. {box_power_lo:,.3f} kW. Behind-the-meter "
            "discharge is capped at min(power, load), so the smallest searchable system must stay "
            "scaled to the load even though the largest is scaled to the wire; the coarse ladder "
            f"therefore carries {len(powers)} power points, "
            f"{len(COARSE_POWER_FRACTIONS)} of them from the connection anchor."
        )
    coarse_pairs = [
        (p, round(d, 2))
        for p in powers
        for d in COARSE_DURATION_HOURS
        if prov_floors.admits(p, p * d)
    ]
    # The refinement/polish box, widened below if the provisioning floor forces the search
    # outside the site-scaled domain (N7). The upper edge is deliberately NOT clipped at the
    # declared connection limit: energy is power x duration and duration caps at
    # DURATION_BOUNDS_HOURS[1], so the 1.5x power headroom is the only route to the box's
    # high-ENERGY corner, and a candidate that cannot use its rating is bounded by the solver's own
    # charge/discharge caps and simply carries its capex into the ranking, where it loses.
    box_power_hi = round(POWER_FRACTION_BOUNDS[1] * binding_capacity_kw, 3)
    box_duration_hi = DURATION_BOUNDS_HOURS[1]
    if prov_floors.active and not coarse_pairs:
        # N7: the floor escaping the COARSE GRID is a fact about the search box, not about the
        # connection. Reinforcement is declared only against a declared physical limit; otherwise
        # the box is extended to cover the floor and the search continues.
        limit_kw = connection_power_limit_kw(site)
        refusal = provisioning_reinforcement_reason(
            prov_floors.power_floor_kw, prov_floors.energy_floor_kwh, limit_kw
        )
        if refusal is not None:
            raise ProvisioningInfeasibleError(refusal)
        coarse_pairs = provisioning_cover_pairs(
            prov_floors.power_floor_kw, prov_floors.energy_floor_kwh, limit_kw
        )
        if not coarse_pairs:
            raise ProvisioningInfeasibleError(
                "no viable configuration: the provisioning floor "
                f"({prov_floors.power_floor_kw:.0f} kW / {prov_floors.energy_floor_kwh:.0f} kWh "
                "at end-of-tenor capability) admits no battery of any size."
            )
        box_power_hi = max(box_power_hi, max(p for p, _ in coarse_pairs))
        box_duration_hi = max(box_duration_hi, max(d for _, d in coarse_pairs))
        warnings.append(
            "sizing box EXTENDED to cover the provisioning floor "
            f"({prov_floors.power_floor_kw:.0f} kW / {prov_floors.energy_floor_kwh:.0f} kWh): the "
            "obligation exceeds every candidate in the site-scaled coarse grid "
            f"(≤{POWER_FRACTION_BOUNDS[1]:.2f}x the site's binding capacity, "
            f"≤{DURATION_BOUNDS_HOURS[1]:.1f} h) but "
            + (
                f"stays within the connection's declared limit ({limit_kw:.0f} kW), so a battery "
                "that meets it is buildable and is searched (N7)."
                if limit_kw is not None
                else "the connection declares no import limit (connection.mic_kw / "
                "connection.asc_kw), so no reinforcement verdict can be rendered — the floor is "
                "searched and the limit is a data ask (N7)."
            )
        )
    coarse_total = len(coarse_pairs)
    for i, (power, duration) in enumerate(coarse_pairs, start=1):
        _emit_progress(progress, "coarse", i, coarse_total)
        evaluate(power, duration, stage="coarse", proxy=proxy_mode)

    coarse_ranked = sorted(
        evaluations.values(),
        key=lambda e: (rank_key(e), -e.candidate.power_kw, -e.candidate.duration_hours),
        reverse=True,
    )
    seed_candidates = [
        (e.candidate.power_kw, e.candidate.duration_hours) for e in coarse_ranked[:3]
    ]

    # --- Stage B: seeded convergent refinement ---------------------------------
    refine_solves = 0

    def refine_evaluate(power: float, duration: float) -> RankKey:
        nonlocal refine_solves
        if prov_floors.active and not prov_floors.admits(power, power * duration):
            # Below the obligation floor (the energy floor is a hyperbola the refinement box
            # cannot express): worst rank, never solved, never stored on the surface, not emitted.
            return (-1.0,)
        # Emit only for a genuine new dispatch solve — a memo hit costs nothing, so the narration
        # counts real evaluations (no known total; DE early-converges within a ~200-eval budget).
        is_new = (power, duration) not in evaluations
        ranked = rank_key(evaluate(power, duration, stage="refine", proxy=proxy_mode))
        if is_new:
            refine_solves += 1
            _emit_progress(progress, "refine", refine_solves, None)
        return ranked

    trace = refine_sizing(
        refine_evaluate,
        power_bounds=(
            # the two-anchor floor (`power_search_floor_kw`), raised by the provisioning power
            # floor where one is declared (energy-floor leaks are caught per-candidate by the
            # solver's compiled obligation)
            round(max(box_power_lo, prov_floors.power_floor_kw), 3),
            box_power_hi,
        ),
        duration_bounds=(DURATION_BOUNDS_HOURS[0], box_duration_hi),
        seed=seed,
        seed_candidates=seed_candidates,
    )
    warnings.append(
        f"sizing refinement: {len(trace.evaluated)} distinct candidates over "
        f"{trace.generations_run} generation(s), "
        f"{'converged' if trace.converged else 'stopped at the generation cap'} (seed {seed})."
    )

    # --- Stage C: full-series confirmation of a DIVERSE top-ranked set --------
    # Walk the ranking and take a candidate only if it is not a near-clone (within
    # CONFIRM_MIN_RELATIVE_SPACING on both axes) of one already taken, until CONFIRM_TOP_N are
    # chosen. Every chosen candidate is confirmed on the full series and the BEST feasible
    # confirmed candidate wins — the old first-feasible-wins early stop traded optimality for
    # speed and measurably lost to the adaptive refiner (D34 addendum).
    ranked = sorted(
        evaluations.values(),
        key=lambda e: (rank_key(e), -e.candidate.power_kw, -e.candidate.duration_hours),
        reverse=True,
    )
    to_confirm: list[CandidateEvaluation] = []
    for evaluation in ranked:
        if len(to_confirm) >= CONFIRM_TOP_N:
            break
        power = evaluation.candidate.power_kw
        duration = evaluation.candidate.duration_hours
        clone = any(
            abs(power - c.candidate.power_kw) <= CONFIRM_MIN_RELATIVE_SPACING * c.candidate.power_kw
            and abs(duration - c.candidate.duration_hours)
            <= CONFIRM_MIN_RELATIVE_SPACING * c.candidate.duration_hours
            for c in to_confirm
        )
        if not clone:
            to_confirm.append(evaluation)
    confirmed: list[CandidateEvaluation] = []
    confirm_total = len(to_confirm)
    for i, evaluation in enumerate(to_confirm, start=1):
        _emit_progress(progress, "confirm", i, confirm_total)
        power = evaluation.candidate.power_kw
        duration = evaluation.candidate.duration_hours
        if not proxy_mode:
            # The search already scored on the full series; re-labelling is enough (no re-solve).
            confirmed.append(replace(evaluation, stage="confirm", proxy=False))
            continue
        confirm_eval = replace(
            _evaluate_spec(
                site,
                assumptions,
                SizingCandidate(power_kw=power, duration_hours=duration),
                dispatch_solver=confirm_solver,
                streams=list(streams),
                constraints=constraints,
                debt_terms=debt_terms,
                horizon_years=horizon_years,
                scenario=scenario,
                finance_runner=finance_runner,
                model_version=confirm_solver.MODEL_VERSION,
                registry=reg,
            ),
            stage="confirm",
            proxy=False,
        )
        confirmed.append(confirm_eval)

    # --- Stage D: bounded full-series polish around the best confirmed candidate --------
    # Compass probes (±step per axis, step halving each round) solved on the FULL series and
    # ranked exactly as selection ranks (feasibility first, then the objective). Deterministic:
    # fixed step schedule, fixed probe order, hard probe cap. Only runs in proxy mode — in
    # full-series mode the refinement stage already searched exactly.
    if proxy_mode and confirmed:

        def _polish_rank(e: CandidateEvaluation) -> tuple[bool, RankKey]:
            return (objective.feasible(e), rank_key(e))

        # Same two-anchor floor the coarse ladder and the refinement box use. With the floor
        # anchored on the connection instead, every probe around a small best candidate was clamped
        # straight back up onto that floor, so polish could not move at all in exactly the region
        # this domain exists to reach.
        power_lo = box_power_lo
        power_hi = box_power_hi
        seen_specs = {
            (round(e.candidate.power_kw, 2), round(e.candidate.duration_hours, 2))
            for e in confirmed
        }
        best = max(confirmed, key=_polish_rank)
        step = POLISH_STEP_RELATIVE
        probes_used = 0
        for _ in range(POLISH_ROUNDS):
            improved = False
            base_p = best.candidate.power_kw
            base_d = best.candidate.duration_hours
            for dp, dd in ((step, 0.0), (-step, 0.0), (0.0, step), (0.0, -step)):
                if probes_used >= POLISH_MAX_PROBES:
                    break
                power = round(min(max(base_p * (1 + dp), power_lo), power_hi), 2)
                duration = round(
                    min(max(base_d * (1 + dd), DURATION_BOUNDS_HOURS[0]), box_duration_hi),
                    2,
                )
                key = (round(power, 2), round(duration, 2))
                if key in seen_specs:
                    continue
                seen_specs.add(key)
                probes_used += 1
                _emit_progress(progress, "polish", probes_used, POLISH_MAX_PROBES)
                probe = replace(
                    _evaluate_spec(
                        site,
                        assumptions,
                        SizingCandidate(power_kw=power, duration_hours=duration),
                        dispatch_solver=confirm_solver,
                        streams=list(streams),
                        constraints=constraints,
                        debt_terms=debt_terms,
                        horizon_years=horizon_years,
                        scenario=scenario,
                        finance_runner=finance_runner,
                        model_version=confirm_solver.MODEL_VERSION,
                        registry=reg,
                    ),
                    stage="confirm",
                    proxy=False,
                )
                confirmed.append(probe)
                if _polish_rank(probe) > _polish_rank(best):
                    best = probe
                    improved = True
            if not improved:
                step /= 2.0
        warnings.append(
            f"sizing polish: {probes_used} full-series compass probe(s) around the best "
            "confirmed candidate (deterministic step schedule)."
        )

    # --- N6: a size-diverse full-series cover before any FINAL no-viable verdict --------
    # If NO confirmed candidate carries a positive return metric, the caller renders
    # `NO_VIABLE_CONFIGURATION` — a FINAL "this asset does not work at any size" verdict. On the
    # pre-N6 path that verdict could rest entirely on CONFIRM_TOP_N points chosen by the
    # typical-day PROXY ranking, and when nothing carries a metric that ranking degenerates to
    # the (-power, -duration) tie-break: the ten SMALLEST candidates. Before refusing a site, the
    # search now solves a cover spanning the box on the FULL metered series. Costs nothing on any
    # run that found a viable candidate (the overwhelming majority) — it fires only in the moment
    # the answer is about to be "no".
    if not any(candidate_viable(e) for e in confirmed):
        cover = no_viable_cover_pairs(
            {
                (round(e.candidate.power_kw, 3), round(e.candidate.duration_hours, 2))
                for e in confirmed
            },
            binding_capacity_kw,
            lambda p, kwh: prov_floors.admits(p, kwh),
            relevant_capacity_kw=relevant_capacity_kw,
        )
        for i, (power, duration) in enumerate(cover, start=1):
            already = evaluations.get((power, duration))
            if already is not None and not already.proxy:
                # Full-series mode: the sweep already solved this exact spec on the real series —
                # re-labelling is enough, exactly as the confirm stage does. No second solve.
                confirmed.append(replace(already, stage="confirm", proxy=False))
                continue
            _emit_progress(progress, "confirm", i, len(cover))
            confirmed.append(
                replace(
                    _evaluate_spec(
                        site,
                        assumptions,
                        SizingCandidate(power_kw=power, duration_hours=duration),
                        dispatch_solver=confirm_solver,
                        streams=list(streams),
                        constraints=constraints,
                        debt_terms=debt_terms,
                        horizon_years=horizon_years,
                        scenario=scenario,
                        finance_runner=finance_runner,
                        model_version=confirm_solver.MODEL_VERSION,
                        registry=reg,
                    ),
                    stage="confirm",
                    proxy=False,
                )
            )
        still_unviable = not any(candidate_viable(e) for e in confirmed)
        anchors = f"{binding_capacity_kw:,.1f} kW binding capacity"
        if relevant_capacity_kw < binding_capacity_kw:
            anchors += f" and {relevant_capacity_kw:,.1f} kW load-relevant capacity"
        warnings.append(
            f"no-viable cover (N6): no confirmed candidate carried a positive return metric, so "
            f"{len(cover)} additional size-diverse candidate(s) spanning the search box "
            f"({', '.join(f'{f:g}x' for f in NO_VIABLE_COVER_POWER_FRACTIONS)} of {anchors} x "
            f"{', '.join(f'{d:g}h' for d in NO_VIABLE_COVER_DURATION_HOURS)}) were solved on the "
            "FULL metered series before any no-viable verdict could stand. "
            + (
                "The cover found no viable size either — the verdict rests on a cover of the "
                "whole box, not on the proxy ranking."
                if still_unviable
                else "The cover FOUND a viable size the proxy ranking had missed."
            )
        )

    selected, bankable, reason, basis = _select(confirmed, objective, debt_terms=debt_terms)
    rejected_by_covenant = [e.candidate for e in confirmed if e.passes_covenant is False]

    cloud: list[CandidateEvaluation] = list(evaluations.values())
    if proxy_mode:
        cloud.extend(confirmed)
    else:
        # Full-series mode: the confirm labels replace the search labels for those candidates.
        keys = {(e.candidate.power_kw, e.candidate.duration_hours) for e in confirmed}
        cloud = [
            e for e in cloud if (e.candidate.power_kw, e.candidate.duration_hours) not in keys
        ] + confirmed

    warnings.append(_bankability_warning(bankable, basis))

    return SizingRun(
        search_space_size=len(cloud),
        candidates_evaluated=cloud,
        selected=selected,
        selection_reason=reason,
        rejected_by_covenant=rejected_by_covenant,
        bankable=bankable,
        assumption_version=assumptions.metadata.version,
        warnings=warnings,
        search_version=SEARCH_VERSION,
        selection_basis=basis,
    )
