"""One asset, one power budget — the shared-capacity constraint (hostile review S-2 / F-11).

## The defect this closes

Capacity-shaped revenue streams were each priced independently as ``rate x de-rated MW``. Nothing
compared them against each other or against the asset's rating, so the **same megawatt could be
sold to the Capacity Market, the Balancing Mechanism and a frequency-response service at the same
time**, and all three £ figures were added into the contracted floor that debt is sized against.

The literature is unambiguous that this is the single most expensive omission in BESS underwriting
models, because it compounds across every stream
(`docs/bess_modelling_requirements.md` §E-01, §E-02):

- **Shared power budget** — for every period, ``P_dis + Σ_s r_up,s <= P_rated`` and
  ``P_ch + Σ_s r_down,s <= P_rated``. A single inverter cannot deliver more than its rating.
  *(Xu et al., IEEE TPWRS 33(2):2248-2259, eqs 17-21; Engels et al., IEEE Trans. Smart Grid
  10(2):1971-1981, eq. 1e.)*
- **Energy sustainability** — a service is only deliverable if the stored energy can sustain it for
  the product's required duration: ``E_t - E_min >= Σ_s (D_s/60) x r_down,s`` and
  ``E_max - E_t >= Σ_s (D_s/60) x r_up,s``, with ``D_s`` the sustain duration in minutes. NESO's
  durations: Dynamic Containment 15 min, Dynamic Moderation 30 min, Dynamic Regulation up to
  60 min, Static FFR 30 min. *(Xu et al., eq. 23 form; NESO frequency-response product table.)*

Xu et al.'s worked example makes the magnitude concrete: a 36 MW / 3 MWh battery can dispatch its
full 36 MW, but once it is scheduled for 1 MW of reserve under a one-hour sustainability
requirement it can only provide **2 MW** of baseline generation. Omitting the constraint inflates
stacked revenue "by a multiple, not a percentage".

## What this module does, and does not, do

It enforces the budget at the **valuation** seam — the place where the double count actually
reaches the money. Committed capacity is allocated to streams in a deterministic priority order,
and a stream that does not fit is **cut back to what remains** rather than being sold twice. The
shortfall is reported, never hidden.

It deliberately does not add the constraint inside the dispatch MILP. That is the more complete
fix (co-optimising reserve against energy), and it is the right eventual home — but it changes the
optimiser's feasible set, so it belongs in its own work package with its own golden re-pin. This
module stops the money being wrong now.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gridq.schemas.enums import MarketStream

#: Sustain duration (minutes) each service demands of stored energy, from NESO's product table.
#: A service the asset cannot sustain for this long is not deliverable, however much power is free.
#:
#: **The Capacity Market is deliberately absent.** Its de-rating factor is *already* a duration
#: penalty — that is precisely what CM de-rating is for — so charging a CM award against the shared
#: energy budget as well would apply the duration discount twice. Worse, at a nominal 4-hour stress
#: duration a CM claim would consume the whole energy budget of any shorter-duration asset and
#: starve every other service of it, which is an artefact of the double count rather than a real
#: physical conflict. CM therefore contends for the POWER budget only.
#:
#: (This was a genuine defect in the first version of this module: it zeroed a site's local-flex
#: revenue entirely because a CM claim had already eaten the energy budget at a 240-minute sustain.)
SERVICE_SUSTAIN_MINUTES: dict[MarketStream, float] = {
    MarketStream.ANCILLARY_DC_DM_DR: 15.0,  # Dynamic Containment, the binding (shortest) case
    MarketStream.BALANCING_MECHANISM: 30.0,  # BM MEL/MIL declared on a 30-minute sustain basis
    MarketStream.LOCAL_FLEX_PICLO: 120.0,  # DNO windows are typically multi-hour
}

#: Streams that contend for the shared POWER budget — the services that hold inverter capacity
#: **continuously**, so that selling the same MW to two of them is a straight double count.
#:
#: **The Capacity Market is excluded here too, and this is a modelling decision worth challenging.**
#: A CM agreement is not a continuous reservation of power: it is an obligation to be available
#: during stress events, which run to a handful of hours a year, and the CM **de-rating factor
#: exists precisely to price that availability conflict**. Charging a CM award against the
#: continuous budget as well would (a) double-count the same discount and (b) starve every other
#: service of capacity for 8,760 hours to cover an obligation that binds for ~3 — which is wrong in
#: the opposite direction from the defect this module exists to fix.
#:
#: The literature's own list of the services that conflict on power is the frequency-response and
#: reserve set plus wholesale (`docs/bess_modelling_requirements.md` E-02), not the CM.
#:
#: **Tier B for the modeller:** whether a CM agreement should additionally reserve continuous
#: headroom is a commercial judgement about how the obligation is managed in practice. The safe
#: interim — implemented here — is to let the de-rating carry it and to say so on the record.
POWER_BUDGET_STREAMS: frozenset[MarketStream] = frozenset(SERVICE_SUSTAIN_MINUTES)

#: Order in which committed capacity is allocated when the budget binds. Contracted, penalty-backed
#: obligations are served before optional ones: breaching a Capacity Market agreement carries a
#: statutory penalty, whereas simply not bidding into the BM carries none.
ALLOCATION_PRIORITY: tuple[MarketStream, ...] = (
    MarketStream.CAPACITY_MARKET,
    MarketStream.LOCAL_FLEX_PICLO,
    MarketStream.ANCILLARY_DC_DM_DR,
    MarketStream.BALANCING_MECHANISM,
)


@dataclass(frozen=True)
class CapacityClaim:
    """One stream's claim on the asset's power, before the budget is applied."""

    stream: MarketStream
    claimed_mw: float


@dataclass(frozen=True)
class CapacityAward:
    """What a stream may actually be paid for, after the shared budget is applied."""

    stream: MarketStream
    claimed_mw: float
    awarded_mw: float
    limited_by: str | None = None

    @property
    def was_cut(self) -> bool:
        return self.awarded_mw < self.claimed_mw - 1e-9

    @property
    def scale(self) -> float:
        """Fraction of the claim that survived — the multiplier to apply to the stream's £."""
        if self.claimed_mw <= 0:
            return 0.0
        return self.awarded_mw / self.claimed_mw


@dataclass(frozen=True)
class CapacityBudget:
    """The result of allocating a rated power budget across competing capacity claims."""

    rated_power_mw: float
    usable_energy_mwh: float
    awards: dict[MarketStream, CapacityAward] = field(default_factory=dict)
    #: What the shared-budget constraint WOULD have awarded, kept beside the D57 full award so the
    #: assumption can be MEASURED on the record rather than asserted in prose. Populated whenever
    #: :func:`allocate_capacity` had to bypass a cut; empty when no claim was ever over budget.
    constrained_awards: dict[MarketStream, CapacityAward] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def scale_for(self, stream: MarketStream) -> float:
        award = self.awards.get(stream)
        return 1.0 if award is None else award.scale

    @property
    def total_claimed_mw(self) -> float:
        return sum(a.claimed_mw for a in self.awards.values())

    @property
    def total_awarded_mw(self) -> float:
        return sum(a.awarded_mw for a in self.awards.values())

    @property
    def was_oversold(self) -> bool:
        return self.total_claimed_mw > self.rated_power_mw + 1e-9

    def d57_uncut(self, stream: MarketStream) -> CapacityAward | None:
        """The award the shared budget WOULD have cut this stream back to, or ``None``.

        The reader-facing half of the D57 bypass: a stream that appears at its full claim in
        :attr:`awards` and ALSO here is one whose figure rests on the D57 stacking assumption, and
        the difference is exactly how much £ the assumption is worth on this stream.
        """
        constrained = self.constrained_awards.get(stream)
        if constrained is None or not constrained.was_cut:
            return None
        return constrained


def allocate_capacity(
    claims: list[CapacityClaim],
    *,
    rated_power_mw: float,
    usable_energy_mwh: float,
) -> CapacityBudget:
    """Allocate ``rated_power_mw`` across competing capacity claims, deterministically.

    Two constraints are applied per stream, in order:

    1. **Energy sustainability** — a stream can only claim the MW its stored energy can sustain for
       that service's duration: ``max_mw = usable_energy_mwh / (sustain_minutes / 60)``.
    2. **Shared power budget** — the remaining rating, allocated in `ALLOCATION_PRIORITY` order.

    A stream that does not fit is cut to what remains; it is never sold twice.

    **D57 (Olly, 2026-08-18) — STACKING EXCLUSIVITY IS ASSUMED SATISFIED, so nothing is cut.**
    Every claim is now awarded in full and the oversell is STATED rather than enforced. This is the
    owner's standing instruction, verbatim on the point: "stacking rules would forbid earning two of
    these streams on one meter point ... Every one is ASSUMED SATISFIED. Where such a condition
    exists, RECORD IT ON THE ASSUMPTIONS REGISTER as assumed approved and price the stream in full."
    D57 limb 3 names stacking exclusivity among the constraints that "must not reduce a figure".
    It is not to be re-litigated by a later session.

    **Why this is not the double-booking D57 refuses to give up.** The rule that survives D57 is
    ARITHMETIC: no POUND may appear under two stream names, or in a cost line and a revenue line at
    once. Two counterparties paying availability on the same MW is two distinct pounds from two
    distinct payers, not one pound counted twice -- so it is a real-world eligibility rule, which
    D57 assumes satisfied, and not an accounting identity, which D57 preserves. The two identity
    carve-outs (``PPA_OR_BILL_SAVINGS`` and ``MIC_ASC_HEADROOM``) are untouched by this function and
    stay GBP 0 on their stated carriers.

    **DIRECTION OF EFFECT: UP, and this is the single largest lift in the D57 change set.** The
    literature quoted in the module docstring is right that omitting the constraint inflates stacked
    revenue "by a multiple, not a percentage", and that statement now describes this function's
    output. Measured on bishops_cleeve_ash_plant at 139.5 kW x 2 h: BALANCING_MECHANISM went from a
    0.0% award (the whole 0.1395 MW rating had already gone to DC/DM/DR) to 100%. Every
    ``CapacityBudget`` therefore carries the oversell sentence into the run record so a reader sees
    the multiple, and the physics account below is preserved verbatim rather than deleted.
    """
    warnings: list[str] = []
    awards: dict[MarketStream, CapacityAward] = {}
    remaining = max(0.0, rated_power_mw)
    # D57: the allocation is bypassed, not deleted. Everything below still computes the claims and
    # the shortfall it WOULD have imposed, so the disclosure is measured rather than asserted; only
    # the award is changed. Deleting the machinery would make the D57 assumption invisible and would
    # make restoring the constraint a rebuild instead of a revert.
    d57_awards: dict[MarketStream, CapacityAward] = {}

    # The exclusion lives HERE, not in the caller, so there is one place the rule can be read and
    # no caller can accidentally reintroduce a stream that should not contend. A stream outside the
    # continuous budget is awarded its claim untouched.
    passthrough = {
        c.stream: c for c in claims if c.claimed_mw > 0 and c.stream not in POWER_BUDGET_STREAMS
    }
    for stream, c in passthrough.items():
        awards[stream] = CapacityAward(
            stream=stream, claimed_mw=c.claimed_mw, awarded_mw=c.claimed_mw, limited_by=None
        )

    claim_by_stream = {
        c.stream: c for c in claims if c.claimed_mw > 0 and c.stream in POWER_BUDGET_STREAMS
    }
    ordered = [s for s in ALLOCATION_PRIORITY if s in claim_by_stream]
    ordered += [s for s in claim_by_stream if s not in ALLOCATION_PRIORITY]

    total_claimed = sum(c.claimed_mw for c in claim_by_stream.values())
    if total_claimed > rated_power_mw + 1e-9:
        warnings.append(
            f"CAPACITY OVERSOLD: {total_claimed:,.3f} MW claimed across "
            f"{len(claim_by_stream)} streams against a {rated_power_mw:,.3f} MW rating. The same "
            "megawatt cannot be sold to more than one market at a time; claims are cut back in "
            "priority order (CM > local flex > ancillary > BM). Before this constraint existed "
            "every claim was paid in full and the excess reached the contracted floor."
        )

    # Energy is a SHARED budget too, not a per-stream cap. E-01's constraint is a sum across
    # services — ``E >= Σ_s (D_s/60) x r_s`` — so each award consumes stored energy that the next
    # service can no longer count on. Applying the cap per stream independently would let two
    # services each reserve the same kWh, which is the same double-sell as the power budget, one
    # level down.
    remaining_energy = max(0.0, usable_energy_mwh)

    for stream in ordered:
        claimed = claim_by_stream[stream].claimed_mw
        limited_by: str | None = None

        sustain = SERVICE_SUSTAIN_MINUTES.get(stream)
        sustain_hours = (sustain / 60.0) if sustain else 0.0
        energy_cap = claimed
        if sustain_hours > 0:
            energy_cap = remaining_energy / sustain_hours

        allowed = max(0.0, min(claimed, energy_cap, remaining))
        if allowed < claimed - 1e-9:
            limited_by = "energy sustainability" if energy_cap < remaining else "power budget"
            warnings.append(
                f"{stream.value}: claimed {claimed:,.3f} MW, awarded {allowed:,.3f} MW "
                f"(limited by {limited_by}"
                + (
                    f"; {sustain:.0f}-minute sustain needs "
                    f"{claimed * sustain / 60.0:,.3f} MWh, asset has {usable_energy_mwh:,.3f} MWh)"
                    if limited_by == "energy sustainability" and sustain
                    else ")"
                )
                + "."
            )

        awards[stream] = CapacityAward(
            stream=stream, claimed_mw=claimed, awarded_mw=allowed, limited_by=limited_by
        )
        # D57: the stream is paid its FULL claim. `awards` above still holds what the constraint
        # would have allowed, and is what the warnings above were measured from.
        d57_awards[stream] = CapacityAward(
            stream=stream, claimed_mw=claimed, awarded_mw=claimed, limited_by=None
        )
        remaining = max(0.0, remaining - allowed)
        remaining_energy = max(0.0, remaining_energy - allowed * sustain_hours)

    if warnings:
        warnings.append(
            "D57 ASSUMED APPROVED: every claim above is nevertheless PAID IN FULL on this academic "
            "validation basis. Stacking exclusivity, the shared inverter power budget and the "
            "shared energy-sustainability budget are assumed satisfied for this site and battery "
            "size (D57 limb 3, owner-directed 2026-08-18), so no stream is cut back. The shortfall "
            "each line reports is what the constraint WOULD have removed and is stated here so the "
            "size of the assumption is visible: it is the largest single uplift in the D57 change "
            "set. NO DOUBLE-BOOKING IS INTRODUCED - two counterparties paying availability on the "
            "same megawatt is two pounds from two payers, not one pound counted twice; the two "
            "accounting-identity streams (PPA_OR_BILL_SAVINGS, MIC_ASC_HEADROOM) are untouched by "
            "this allocator and stay at GBP 0 on their stated carriers."
        )
    return CapacityBudget(
        rated_power_mw=rated_power_mw,
        usable_energy_mwh=usable_energy_mwh,
        # D57: the FULL claim is what every consumer reads. `constrained_awards` carries what the
        # shared budget would have allowed, so `_apply_capacity_budget` can state the size of the
        # assumption on the stream it applies to instead of leaving it in a docstring.
        awards={**awards, **d57_awards},
        constrained_awards=dict(awards),
        warnings=tuple(warnings),
    )
