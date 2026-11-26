"""End-of-tenor deliverability + the first GateContext producer (W7-3 / W7-4).

**W7-3 — the closed-form check.** "Sized day-one for end-of-life capability" (the M4
objective) is computable: for every typed availability commitment a candidate carries (the
C026 payloads on the constraint seam), verify at N = the commitment's final contract year
(``tenor_years``):

    capability(N) = power × derate(duration) × (1 − fade/100)^N  ≥  committed_power_kw
    energy(N)     = energy × (1 − fade/100)^N                    ≥  held_energy_kwh (if held)

Pure arithmetic — no extra LP solves. The de-rating factor applies only to Capacity-Market
commitments (the agreement is written in de-rated MW; conservative duration flooring per
D28/D22, via the same ``value_for_duration`` the solver uses); other streams' commitments are
plain deliverable kW. The verdict is an honest three-state: ``True`` (every tenored commitment
passes — the notes quote the fade assumption's status: a placeholder-fade verdict is
**arithmetic, not evidence**), ``False`` (a known failure always wins — the sizing objective
REJECTS the candidate), ``None`` (not assessable — a data-ask, never a fabricated pass, D12).

**W7-4 — the producer.** `produce_gate_context` turns seam commitments into an
evidence-bearing `GateContext`: gate facts are asserted ONLY from commitments carrying a
contract ``reference`` (a commitment without provenance is a modelling input, not evidence —
C026), each fact paired with a `CommitmentEvidence` naming who confirmed it, on what basis,
against which reference. Deliverability verdicts come from the W7-3 computed check and say so
(``confirmed_by='engine:…'``); a contracted flex award's evidence is the reference itself.
Nothing here touches bankability — placeholder content keeps every quote ``not_bankable``.
"""

from __future__ import annotations

from gridq.assumptions import Assumption, AssumptionLibrary
from gridq.schemas import AvailabilityCommitment, CommitmentEvidence, MarketStream
from gridq.value_stack.interface import DispatchConstraintDescriptor, GateContext

_ASSESSABLE_KINDS = frozenset({"availability_window", "held_state_of_energy"})
_TOL = 1e-9

_ENGINE_CONFIRMER = "engine:end-of-tenor-capability-check (W7-3)"

#: **D57 (Olly, 2026-08-18) — AGGREGATION IS ASSUMED APPROVED, so the aggregate is stated.**
#:
#: ``GateContext.portfolio_mw`` and ``GateContext.portfolio_threshold_mw`` had NO production
#: producer anywhere in ``src/``: this function, the only production construction of a
#: `GateContext`, omitted both. ``EligibilityVerdict.route`` could therefore never be
#: ``'aggregated'``, `gates.meets_portfolio_threshold` could never return ``True``, and the
#: aggregation arithmetic that already existed in `gates.market_entry_eligibility` was INERT on
#: every run. Every sub-MW site was refused the Balancing Mechanism, DC/DM/DR response and the
#: Capacity Market on a threshold D57 declares ASSUMED SATISFIED.
#:
#: **The value is the market's own sourced minimum, and the £ is INVARIANT above it.** On the
#: aggregated route `gates.market_entry_eligibility` returns ``sellable_mw = unit_mw`` -- the
#: site's own MW, deliberately untruncated, because pooling fractional contributions into one
#: whole-MW order is what an aggregator does. So the portfolio figure opens the gate and never
#: scales a number: any value at or above the threshold produces byte-identical revenue. Stating
#: the threshold itself is therefore the minimal admissible choice, and it is a SOURCED figure
#: (SI 2014/2043 reg 15 for the CM, the Wider Access 1 MW BM Unit minimum, the 1 MW dynamic
#: response unit minimum -- all carried in ``gb_streams._MIN_UNIT_MW_BY_STREAM``) rather than an
#: invented fleet size. D56's citation boundary survives D57: what changed is that the condition
#: is assumed satisfied, never that the number may be made up.
#:
#: **Direction of effect: revenue UP** on every sub-MW site, for BM, DC/DM/DR and the CM. The
#: assumption is enumerated per site on the assumptions register as ASSUMED APPROVED under D57,
#: never as an aggregation agreement a third party has granted (D45).
_D57_AGGREGATION_ASSUMED = (
    "D57 ASSUMED APPROVED: this site is assumed aggregated into a portfolio of "
    "{mw:,.3f} MW, which is the market's own published minimum unit size, so every "
    "minimum-size threshold is cleared via the aggregation route. The owner (Olly) is the "
    "authority for the assumption; no aggregator has granted it and none is asserted to have "
    "(D45). The figure opens the gate and scales nothing: on the aggregated route the priced "
    "volume is the site's own MW, so any portfolio at or above the threshold prices identically."
)


def d57_portfolio_aggregate_mw() -> float:
    """The ASSUMED-APPROVED aggregated portfolio MW (D57) -- the largest sourced minimum.

    Read from `gb_streams.eligibility_threshold_mw` rather than restated, so the aggregate can
    never drift below a threshold a later release arms higher. Call-time import: ``gb_streams``
    reaches ``gridq.optimisation`` transitively and ``optimisation.engine`` imports
    ``gridq.sizing``, so a module-level import here is circular (the same reason
    :func:`_assess_commitment` imports ``value_for_duration`` at call time).
    """
    from gridq.value_stack.gb_streams import eligibility_threshold_mw

    thresholds = [
        t for t in (eligibility_threshold_mw(s) for s in MarketStream) if t is not None and t > 0
    ]
    # No armed threshold anywhere would mean no gate to clear; 1.0 MW is then still the sourced
    # BM/CM/response minimum this repo carries, so the aggregate never becomes an invented number.
    return max(thresholds) if thresholds else 1.0


def _maybe(assumptions: AssumptionLibrary, section: str, name: str) -> Assumption | None:
    """Fetch an assumption if present, else ``None`` — the same never-fabricate convention as
    ``optimisation._assumptions.maybe`` / ``finance``'s local readers (importing the optimisation
    package here at module level would be circular: optimisation.engine imports gridq.sizing)."""
    try:
        return assumptions.get(section, name)
    except KeyError:
        return None


def _numeric(a: Assumption | None) -> float | None:
    """The numeric value of an assumption, or ``None`` (booleans/structured values excluded)."""
    if a is None:
        return None
    v = a.value
    if isinstance(v, bool):
        return None
    return float(v) if isinstance(v, (int, float)) else None


def commitments_from_constraints(
    constraints: list[DispatchConstraintDescriptor],
) -> list[AvailabilityCommitment]:
    """The unique typed commitments riding the constraint seam (C026), in seam order."""
    out: list[AvailabilityCommitment] = []
    seen: set[str] = set()
    for d in constraints:
        c = d.parameters
        if d.kind in _ASSESSABLE_KINDS and c is not None and c.commitment_id not in seen:
            seen.add(c.commitment_id)
            out.append(c)
    return out


def _assess_commitment(
    c: AvailabilityCommitment,
    *,
    power_kw: float,
    energy_kwh: float,
    duration_hours: float,
    fade: float,
    fade_status: str,
    assumptions: AssumptionLibrary,
) -> tuple[bool | None, float | None, list[str]]:
    """(verdict, capability_kw, notes) for ONE tenored commitment — the shared W7-3/W7-4 core."""
    # Call-time import: keeps the D28 flooring semantics single-sourced without the circular
    # module-level dependency (optimisation.engine imports gridq.sizing).
    from gridq.optimisation._assumptions import value_for_duration

    tenor = float(c.tenor_years or 0.0)
    retention = (1.0 - fade / 100.0) ** tenor
    derate = 1.0
    notes: list[str] = []
    if c.stream == MarketStream.CAPACITY_MARKET:
        table = _maybe(assumptions, "capacity_market", "de_rating_factor_by_duration_h")
        d_val, d_warn = value_for_duration(
            table.value if table is not None else None, duration_hours
        )
        if d_val is None:
            return (
                None,
                None,
                [
                    f"commitment '{c.commitment_id}': CM de-rating unusable ({d_warn}) — not "
                    "assessable (data-ask, never a fabricated pass)."
                ],
            )
        derate = float(d_val)
        if d_warn:
            notes.append(f"commitment '{c.commitment_id}': {d_warn}.")
    capability_kw = power_kw * derate * retention
    ok_power = capability_kw + _TOL >= c.committed_power_kw
    ok_energy = (
        True if c.held_energy_kwh is None else energy_kwh * retention + _TOL >= c.held_energy_kwh
    )
    verdict = ok_power and ok_energy
    notes.append(
        f"commitment '{c.commitment_id}' ({c.stream.value}): capability at year "
        f"{tenor:g} = {capability_kw:.1f} kW (derate {derate:g} × retention "
        f"{retention:.4f}; fade {fade:g}%/yr, status '{fade_status}') vs "
        f"committed {c.committed_power_kw:g} kW"
        + (
            f"; held energy {energy_kwh * retention:.1f} vs {c.held_energy_kwh:g} kWh"
            if c.held_energy_kwh is not None
            else ""
        )
        + f" → {'deliverable' if verdict else 'NOT deliverable'}."
    )
    return verdict, capability_kw, notes


def assess_end_of_life_deliverability(
    *,
    power_kw: float,
    energy_kwh: float,
    duration_hours: float,
    constraints: list[DispatchConstraintDescriptor],
    assumptions: AssumptionLibrary,
) -> tuple[bool | None, list[str]]:
    """The aggregate closed-form verdict + working notes (see module docstring). Deterministic."""
    commitments = [
        c for c in commitments_from_constraints(constraints) if c.tenor_years is not None
    ]
    if not commitments:
        return None, []  # no tenored commitment — the caller keeps its data-ask

    fade_a = _maybe(assumptions, "degradation", "annual_capacity_fade_pct")
    fade = _numeric(fade_a)
    if fade_a is None or fade is None or not 0 <= fade < 100:
        return None, [
            "end-of-tenor deliverability not assessable: degradation.annual_capacity_fade_pct "
            "is missing or unusable — recorded as a data-ask, never a fabricated pass."
        ]

    notes: list[str] = []
    any_failed = False
    any_unassessable = False
    for c in commitments:
        verdict, _, c_notes = _assess_commitment(
            c,
            power_kw=power_kw,
            energy_kwh=energy_kwh,
            duration_hours=duration_hours,
            fade=fade,
            fade_status=fade_a.status.value,
            assumptions=assumptions,
        )
        notes.extend(c_notes)
        if verdict is False:
            any_failed = True
        elif verdict is None:
            any_unassessable = True

    if any_failed:
        return False, notes  # a known end-of-tenor failure always wins
    if any_unassessable:
        return None, notes
    notes.append(
        "End-of-tenor verdicts are engine arithmetic on library values — evidence/provenance "
        "arrives with the GateContext producer (W7-4); bankability is unaffected until the "
        "underlying assumptions are confirmed."
    )
    return True, notes


def produce_gate_context(
    *,
    power_kw: float,
    energy_kwh: float,
    duration_hours: float,
    constraints: list[DispatchConstraintDescriptor],
    assumptions: AssumptionLibrary,
) -> tuple[GateContext | None, list[str]]:
    """The first GateContext producer (W7-4): evidence-bearing gate facts from seam commitments.

    Rules (see module docstring): facts are asserted only from **referenced** commitments;
    deliverability comes from the W7-3 computed check (all referenced CM commitments must pass
    — one failure ⇒ ``False``); a referenced local-flex commitment IS the contracted-tender
    fact. Since D57 item 1 (7405d4b) a context is ALWAYS returned: it carries the assumed
    portfolio aggregate (``portfolio_mw``) and the D57 note saying so, and leaves
    ``deliverability_confirmed`` / ``local_flex_tender_open`` at ``None`` and ``evidence`` empty
    whenever nothing about deliverability can be asserted.
    """
    commitments = commitments_from_constraints(constraints)
    # D57: the portfolio aggregate is ASSUMED APPROVED on every run, so it is populated whether or
    # not the seam carries a commitment. Before this the no-commitment branch returned ``None`` and
    # the aggregation route was unreachable on exactly the runs that needed it most (a site with no
    # award is still a site D57 says may aggregate). ``deliverability_confirmed`` and
    # ``local_flex_tender_open`` stay ``None`` on that branch, which is what they were before, so
    # no gate that reads THOSE facts changes.
    portfolio_mw = d57_portfolio_aggregate_mw()
    if not commitments:
        return (
            GateContext(
                portfolio_mw=portfolio_mw,
                portfolio_threshold_mw=portfolio_mw,
            ),
            [_D57_AGGREGATION_ASSUMED.format(mw=portfolio_mw)],
        )

    warnings: list[str] = [_D57_AGGREGATION_ASSUMED.format(mw=portfolio_mw)]
    evidence: list[CommitmentEvidence] = []
    deliverability: bool | None = None
    tender_open: bool | None = None

    fade_a = _maybe(assumptions, "degradation", "annual_capacity_fade_pct")
    fade = _numeric(fade_a)
    fade_ok = fade_a is not None and fade is not None and 0 <= fade < 100

    for c in commitments:
        if c.reference is None:
            warnings.append(
                f"commitment '{c.commitment_id}' carries no contract reference — a modelling "
                "input, not evidence; no gate fact is asserted from it (C026)."
            )
            continue
        if c.stream == MarketStream.CAPACITY_MARKET:
            if c.tenor_years is None or not fade_ok:
                warnings.append(
                    f"commitment '{c.commitment_id}': deliverability not assessable (no tenor "
                    "or unusable degradation fade) — gate stays unset (data-ask, never a "
                    "fabricated pass)."
                )
                continue
            verdict, capability, c_notes = _assess_commitment(
                c,
                power_kw=power_kw,
                energy_kwh=energy_kwh,
                duration_hours=duration_hours,
                fade=fade if fade is not None else 0.0,
                fade_status=fade_a.status.value if fade_a is not None else "unknown",
                assumptions=assumptions,
            )
            if verdict is None:
                warnings.extend(c_notes)
                continue
            evidence.append(
                CommitmentEvidence(
                    commitment_id=c.commitment_id,
                    stream=c.stream,
                    verdict=verdict,
                    confirmed_by=_ENGINE_CONFIRMER,
                    basis=" ".join(c_notes),
                    reference=c.reference,
                    final_contract_year=c.tenor_years,
                    verified_capacity_kw=None if capability is None else round(capability, 3),
                )
            )
            deliverability = verdict if deliverability is None else (deliverability and verdict)
        elif c.stream == MarketStream.LOCAL_FLEX_PICLO:
            # D54: the asserter travels WITH the assertion. This used to stamp every local-flex
            # commitment ``confirmed_by='contract-record'`` on the basis "awarded tender /
            # contracted commitment reference supplied on the constraint seam" — a factual claim
            # about the outside world that this function never checked and cannot check. It is
            # true of a commitment transcribed from a DNO award record and FALSE of one the site's
            # owner attests, and a lender reading "contract-record" is entitled to believe a
            # contract was seen. So an award that names its own confirmer/basis keeps them, and
            # only a commitment that names neither falls back to the old sentence — which makes
            # every pre-D54 record byte-identical.
            evidence.append(
                CommitmentEvidence(
                    commitment_id=c.commitment_id,
                    stream=c.stream,
                    verdict=True,
                    confirmed_by=c.confirmed_by or "contract-record",
                    basis=c.basis
                    or (
                        "awarded tender / contracted commitment reference supplied on the "
                        "constraint seam."
                    ),
                    reference=c.reference,
                    final_contract_year=c.tenor_years,
                    verified_capacity_kw=c.committed_power_kw,
                )
            )
            tender_open = True
        # Other streams' commitments (e.g. a customer resilience reserve — Wave 9) carry no
        # recognised gate fact yet; they still shape dispatch via W7-2, just not the gates.

    # D57: a context is ALWAYS returned now, because the portfolio aggregate is always asserted.
    # The old `return None, warnings` here discarded the aggregate whenever no commitment carried a
    # recognised gate fact.
    return (
        GateContext(
            deliverability_confirmed=deliverability,
            local_flex_tender_open=tender_open,
            portfolio_mw=portfolio_mw,
            portfolio_threshold_mw=portfolio_mw,
            evidence=evidence,
        ),
        warnings,
    )
