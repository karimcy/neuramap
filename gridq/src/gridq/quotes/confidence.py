"""Confidence bands — how much weight a validation result can carry.

Implements `docs/validation_requirements.md` §2. **This replaces the withdrawn 20% levered IRR
target.** That target was a hurdle, and it was the wrong instrument: it is not a property this
asset class reliably has, and steering a model toward a number decided in advance inverts the
purpose of underwriting. The model exists to find out what a site earns.

## What a band is

A band answers *"how much weight can be put on this result?"* — never *"did it clear a hurdle?"*

It is **assigned from evidence, not chosen**, and it is **capped by the weakest input the result
actually depends on**. A High band is impossible on modelled load data however good the rest of
the case is, because the result cannot be more trustworthy than the data underneath it.

## What a band is NOT

- **Not a probability.** It must never be rendered as one.
- **Not P50/P90.** Those quantify the revenue *distribution*; a band quantifies *evidential
  strength*. A site can be High confidence in a poor result.
- **Not a recommendation.** A High-confidence negative result is a **successful validation** — it
  tells you something reliable about the site.

## Why the cap rather than a score

A weighted score lets a strong input compensate for a fatal one: perfect tariff data cannot make a
guessed load shape trustworthy. Every factor therefore states a *ceiling*, and the band is the
minimum across them. The factor that set the ceiling is always reported, so the reader knows what
to fix to raise it — which is the question anyone actually asks of a confidence rating.

This module also gives the data tier its teeth. Before it, `data_tier` was a label that changed no
number and blocked nothing (hostile review F-03): a quote built from a two-interval window carried
the same headline IRR as one built from twelve validated months. The tier now sets a hard ceiling.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from gridq.ingestion.hh_mandate import HalfHourlyAdmission, HalfHourlyBasis
from gridq.ingestion.reconciliation import BillReconciliationResult, ReconciliationResult
from gridq.schemas import (
    ConfidenceBand,
    DataTier,
    DerivedAssumption,
    Firmness,
    QuoteRecord,
    SiteModel,
    StudyBasis,
    ceiling_for_key,
    study_basis_label,
)

# `SHAPE_DISCLOSURE_KEYS` is read from its own module rather than the package: it is the disclosure
# policy for one factor here, not a schema contract other layers build against, and adding it to
# `gridq.schemas.__all__` would advertise it as one.
from gridq.schemas.derived import SHAPE_DISCLOSURE_KEYS

__all__ = [
    "ConfidenceAssessment",
    "ConfidenceBand",
    "ConfidenceFactor",
    "assess_confidence",
]

# `ConfidenceBand` now lives in `gridq.schemas.enums` and is re-exported here unchanged, so the
# band can be persisted on the `QuoteRecord` and enforced by the lifecycle gate (N21). Before that
# the band was computed here and never reached the record at all.


@dataclass(frozen=True)
class ConfidenceFactor:
    """One piece of evidence and the ceiling it imposes."""

    name: str
    ceiling: ConfidenceBand
    reason: str


@dataclass(frozen=True)
class ConfidenceAssessment:
    """The band, and — more usefully — what is holding it down."""

    band: ConfidenceBand
    factors: tuple[ConfidenceFactor, ...]

    @property
    def binding_factors(self) -> tuple[ConfidenceFactor, ...]:
        """The factors that actually set the band. These are what to fix to raise it."""
        return tuple(f for f in self.factors if f.ceiling is self.band)

    def explain(self) -> str:
        binding = self.binding_factors
        if not binding:
            return f"Confidence: {self.band.value.upper()}."
        why = "; ".join(f"{f.name} — {f.reason}" for f in binding)
        return (
            f"Confidence: {self.band.value.upper()}, capped by {len(binding)} factor(s): {why}. "
            "A band is evidential strength, not a probability and not a hurdle — a "
            f"{self.band.value} band on a good return means the return is not yet well evidenced, "
            "not that the site is bad."
        )


def _hh_factor(admission: HalfHourlyAdmission | None) -> ConfidenceFactor:
    if admission is None:
        return ConfidenceFactor(
            "half-hourly basis", ConfidenceBand.LOW, "no half-hourly admission record on the run"
        )
    if admission.basis is HalfHourlyBasis.METERED:
        return ConfidenceFactor(
            "half-hourly basis", ConfidenceBand.HIGH, "the site's own metered half-hourly export"
        )
    if admission.basis is None:
        # D44 / N9: Gate 2 records and continues. Neither of these is read as metered — an
        # undeclared provenance is an open question, and no series at all is structural.
        if admission.has_series:
            return ConfidenceFactor(
                "half-hourly basis",
                ConfidenceBand.MEDIUM,
                "a half-hourly series is attached and priced as supplied, but its provenance is "
                "NOT DECLARED — nothing records whether it is this site's own metered export or a "
                "modelled shape, so the top band is withheld until it is confirmed",
            )
        return ConfidenceFactor(
            "half-hourly basis",
            ConfidenceBand.LOW,
            "NO half-hourly series is attached, so there was no load shape to dispatch against — "
            "the run completed and priced nothing, which is an honest empty result rather than a "
            "refusal, but nothing downstream of it can be strongly evidenced",
        )
    return ConfidenceFactor(
        "half-hourly basis",
        ConfidenceBand.MEDIUM,
        f"load shape is MODELLED ({admission.reference_profile or 'unrecorded profile'}, scaled to "
        f"{admission.scaled_to or 'unrecorded totals'}) — evidence about a typical site of this "
        "type, not about this site",
    )


def _tier_factor(tier: DataTier) -> ConfidenceFactor:
    """The data tier, finally load-bearing (F-03)."""
    if tier is DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY:
        return ConfidenceFactor(
            "data tier", ConfidenceBand.HIGH, "T2 — a validated ~12-month half-hourly series"
        )
    if tier is DataTier.T1_PARTIAL_HALF_HOURLY:
        return ConfidenceFactor(
            "data tier",
            ConfidenceBand.MEDIUM,
            "T1 — partial half-hourly coverage, so annual figures are extrapolated from less than "
            "a full year and inherit the seasonal bias of the months present",
        )
    return ConfidenceFactor(
        "data tier",
        ConfidenceBand.LOW,
        "T0 — no genuine half-hourly coverage behind the series",
    )


def _tariff_factor(site: SiteModel) -> ConfidenceFactor:
    """Whether the delivered cost stack is evidenced, or only its commodity component.

    **A PARTIAL DUoS stack is not a present one.** The network test was ``any(...)``, so one
    band out of three satisfied it and the factor then asserted, at HIGH, that "commodity, network
    and levy lines all present" — over a site with two null band rates. Two things made that the
    worst available answer. It is a **false statement of completeness** in the one factor a reader
    consults to find out whether the delivered cost is evidenced; and a HIGH factor is never a
    binding factor, so the assertion is made and the reader is never shown the sentence that would
    let them check it.

    It is also the wrong direction of error. An all-null stack **understates** the case: the DUoS
    stream is held out of the priced floor entirely (`gridq.value_stack.gates` D45 guard). A
    *partial* stack **OVERSTATES** it: the absent bands are consumed as zero, widening the modelled
    red-to-green spread beyond the one the site actually faces, so red-band avoidance prices high.
    That is the case the old ``any(...)`` rated HIGH. The two states are reported separately here,
    each naming its own direction, because "incomplete tariff" alone tells a reader nothing about
    which way the number is wrong.
    """
    tariff = site.tariff_stack
    if tariff is None or not tariff.import_rates:
        return ConfidenceFactor(
            "tariff", ConfidenceBand.LOW, "no import tariff — avoided cost cannot be evidenced"
        )
    bands = {
        "duos_red_rate": tariff.duos_red_rate,
        "duos_amber_rate": tariff.duos_amber_rate,
        "duos_green_rate": tariff.duos_green_rate,
    }
    absent_bands = [name for name, rate in bands.items() if rate is None]
    has_levies = bool(tariff.levies)
    if not absent_bands and has_levies:
        return ConfidenceFactor(
            "tariff", ConfidenceBand.HIGH, "commodity, network and levy lines all present"
        )
    if absent_bands and len(absent_bands) < len(bands):
        named = ", ".join(f"tariff_stack.{name}" for name in absent_bands)
        levies = "" if has_levies else " The levy lines are absent as well."
        return ConfidenceFactor(
            "tariff",
            ConfidenceBand.MEDIUM,
            f"the DUoS stack is PARTIAL: {len(absent_bands)} of {len(bands)} band rates are null "
            f"({named}), while the rest are priced. An absent band is consumed as zero, which "
            "widens the modelled red-to-green spread beyond the one this site faces, so red-band "
            f"avoidance is OVERSTATED rather than understated on this run.{levies} Supplying the "
            "missing band rates from the site's own DNO charging statement is what raises this",
        )
    present_flags = (("DUoS", not absent_bands), ("levies", has_levies))
    missing = [name for name, present in present_flags if not present]
    return ConfidenceFactor(
        "tariff",
        ConfidenceBand.MEDIUM,
        f"commodity rates only — {' and '.join(missing)} absent. Most behind-the-meter value sits "
        "in the non-commodity stack, so avoided cost is understated on an incomplete tariff",
    )


def _revenue_factor(quote: QuoteRecord) -> ConfidenceFactor:
    """How much of the priced floor rests on contracted rather than merchant revenue.

    A real `QuoteRecord` carries its streams on ``optimisation_result.value_streams``, not on a
    top-level ``value_streams`` attribute — reading only the latter (which no `QuoteRecord` has)
    made this factor report "no priced floor revenue to assess" and pin the band at LOW for a
    reason that was never true. The duck-typed top-level read is kept first so lightweight test
    doubles still work.
    """
    streams = getattr(quote, "value_streams", None)
    if streams is None:
        result = getattr(quote, "optimisation_result", None)
        streams = list(result.value_streams) if result is not None else []
    priced = [s for s in streams if s.firmness in (Firmness.FIRM, Firmness.CONTRACTED)]
    total = sum(s.annual_revenue_gbp for s in priced)
    if total <= 0:
        return ConfidenceFactor(
            "floor composition", ConfidenceBand.LOW, "no priced floor revenue to assess"
        )
    contracted = sum(s.annual_revenue_gbp for s in priced if s.firmness is Firmness.CONTRACTED)
    share = contracted / total
    if share >= 0.5:
        return ConfidenceFactor(
            "floor composition",
            ConfidenceBand.HIGH,
            f"{share:.0%} of the priced floor is contracted revenue",
        )
    if share >= 0.2:
        return ConfidenceFactor(
            "floor composition",
            ConfidenceBand.MEDIUM,
            f"only {share:.0%} of the priced floor is contracted; the rest is merchant-shaped and "
            "carries price and volume risk",
        )
    return ConfidenceFactor(
        "floor composition",
        ConfidenceBand.LOW,
        f"just {share:.0%} of the priced floor is contracted — the case rests almost entirely on "
        "merchant revenue",
    )


def _financing_basis_factor(quote: QuoteRecord) -> ConfidenceFactor:
    """Whether the size on this record was chosen against a covenant, or with no debt structure.

    `docs/validation_requirements.md` §3 requires that a missing credit assessment or debt structure
    be underwritten on an **unlevered** basis "with the confidence band adjusted" — and this is the
    adjustment. Without it the unlevered selection basis added on 2026-08-17 would be a selection
    the reader is told about in prose while the band silently reports the same strength as a
    covenant-tested one, which is exactly how a caveat stops being load-bearing.

    LOW, because CLAUDE.md §2's ceiling table puts a **structural** input at low and an absent debt
    structure is structural for an underwriting: the P90 DSCR the whole covenant test turns on does
    not exist for this record, the reported return is unlevered rather than the levered one debt
    would be sized against, and no amount of good tariff or metered-load evidence substitutes for
    it. (The §3 wording is "adjusted" rather than "floored", so MEDIUM is arguable; LOW is the
    conservative reading and this change's direction of effect — a first-class selection where the
    record previously carried none — is upward, so over-capping is the safe error.)

    **Detection is on the selected point's own DSCR, not on the basis label.** Reading
    ``selection_basis == "unlevered"`` alone would have left a hole: `MilpEngine._choose`'s
    indicative fallback can also report a size for which no DSCR was ever computed (when the only
    viable candidates carry an explicit non-deliverable verdict, which `sizer._unlevered_pool`
    rejects), and that record would then have carried a HIGH financing factor with no debt structure
    anywhere in it. ``min_p90_dscr is None`` on the reported point IS the absence, whatever the
    label says, so the number is what fires this and the label is only quoted for legibility. Both
    are structured record fields; no prose is parsed, so a reworded verdict cannot switch it off.
    """
    grid = getattr(quote, "sizing_grid", None)
    selected = getattr(grid, "selected", None) if grid is not None else None
    if selected is None:
        # No size is reported, so there is no selection whose financing basis could be weak. The
        # no-spec case is capped by the factors that describe the inputs, not by this one.
        return ConfidenceFactor(
            "financing basis",
            ConfidenceBand.HIGH,
            "no system is specified on this record, so there is no selected size whose financing "
            "basis could cap it here",
        )
    if getattr(selected, "min_p90_dscr", None) is not None:
        return ConfidenceFactor(
            "financing basis",
            ConfidenceBand.HIGH,
            "a P90 DSCR was computed for the reported size, so a debt structure was resolved and "
            "the covenant gate actually ran on it",
        )
    basis = getattr(grid, "selection_basis", None) or "not recorded"
    return ConfidenceFactor(
        "financing basis",
        ConfidenceBand.LOW,
        f"NO P90 DSCR EXISTS for the reported size (sizing_grid.selection_basis: {basis}), because "
        "no debt structure was resolved on the sizing path. It has NEITHER passed nor failed a "
        "covenant, and the levered return a lender sizes debt against is absent, so what this "
        "record carries is an UNLEVERED case. Per validation_requirements.md §3 that is an "
        "unlevered underwriting with the band adjusted, never a refusal - and this is the "
        "adjustment. Supplying a credit assessment and a debt term sheet is what lets the covenant "
        "gate run and retires this cap",
    )


def _assumption_factor(
    quote: QuoteRecord, uses_unconfirmed_assumptions: bool | None
) -> ConfidenceFactor:
    """Whether load-bearing assumptions are sourced or placeholders.

    The flag is computed by the cashflow model (`CashflowModel.uses_unconfirmed_assumptions`) and
    is not a `QuoteRecord` field, so the caller passes it explicitly. ``None`` falls back to the
    duck-typed attribute for lightweight test doubles; absent both, the honest answer is that the
    run's assumption provenance was not established — which is a LOW ceiling, not a clean bill.
    """
    if uses_unconfirmed_assumptions is None:
        uses_unconfirmed_assumptions = getattr(quote, "uses_unconfirmed_assumptions", None)
    if uses_unconfirmed_assumptions is None:
        return ConfidenceFactor(
            "assumptions",
            ConfidenceBand.LOW,
            "the run did not record whether it consumed unconfirmed assumption values, so the "
            "provenance of its load-bearing numbers is unestablished",
        )
    if uses_unconfirmed_assumptions:
        return ConfidenceFactor(
            "assumptions",
            ConfidenceBand.LOW,
            "the run consumed placeholder or needs_review assumption values, so a load-bearing "
            "number in the result is not yet sourced",
        )
    return ConfidenceFactor(
        "assumptions", ConfidenceBand.HIGH, "no unconfirmed assumption values were consumed"
    )


def _reconciliation_factor(
    reconciles: bool | None, reconciliation: ReconciliationResult | None
) -> ConfidenceFactor:
    """Gate 3's contribution. ``reconciliation`` is the richer signal, ``reconciles`` the old bool.

    A WARNING-level divergence (5–15% on energy, 10–25% on peak) is not a pass: the reconciliation
    module's own contract is that "WARNINGs cap the confidence band, they do not block", and a bare
    bool could not express that — every warned run was reported as fully reconciled. With the result
    in hand the three states are distinguished.
    """
    if reconciliation is not None:
        if not reconciliation.reconciles:
            return ConfidenceFactor(
                "load reconciliation",
                ConfidenceBand.LOW,
                "the declared load basis does NOT reconcile with the measured series — the sizing "
                f"side and the revenue side of this case describe different loads. "
                f"{reconciliation.summary()}",
            )
        if reconciliation.warnings:
            return ConfidenceFactor(
                "load reconciliation",
                ConfidenceBand.MEDIUM,
                "the declared load basis reconciles only loosely against the measured series: "
                f"{reconciliation.summary()}",
            )
        return ConfidenceFactor(
            "load reconciliation",
            ConfidenceBand.HIGH,
            "declared load basis reconciles with the measured series",
        )
    if reconciles is None:
        return ConfidenceFactor(
            "load reconciliation",
            ConfidenceBand.MEDIUM,
            "the declared load basis was not reconciled against the measured series",
        )
    if reconciles:
        return ConfidenceFactor(
            "load reconciliation",
            ConfidenceBand.HIGH,
            "declared load basis reconciles with the measured series",
        )
    return ConfidenceFactor(
        "load reconciliation",
        ConfidenceBand.LOW,
        "the declared load basis does NOT reconcile with the measured series — the sizing side and "
        "the revenue side of this case describe different loads",
    )


def _bill_reconciliation_factor(result: BillReconciliationResult | None) -> ConfidenceFactor:
    """Gate 3b's contribution: has the modelled delivered cost ever met a real invoice?

    Gate 3 checks the modelled load against the measured series. It says nothing about whether the
    **tariff stack** turns that load into the right number of pounds, and the delivered cost is what
    every avoided-cost revenue line is a fraction of. Gate 3b closes that: it compares
    ``CustomerBill.annual_bill_before_gbp`` — the BEFORE side, which is dispatch-invariant, so this
    tests the tariff stack and not the battery — against the site's own invoices.

    ``None`` means the gate did not run, which is **not** a pass. Mirroring
    `_reconciliation_factor`, an un-run gate caps at MEDIUM, not HIGH: never having checked is not
    agreement. The band itself comes from `BillReconciliationResult.confidence_ceiling`, so this
    factor and the recorded assumption cannot disagree about the same verdict.
    """
    if result is None:
        return ConfidenceFactor(
            "bill reconciliation",
            ConfidenceBand.MEDIUM,
            "the modelled delivered cost was not compared against an invoice on this run",
        )
    if result.reconciles and result.confidence_ceiling is ConfidenceBand.HIGH:
        return ConfidenceFactor(
            "bill reconciliation",
            ConfidenceBand.HIGH,
            f"modelled delivered cost reconciles against the site's own invoices. "
            f"{result.summary()}",
        )
    return ConfidenceFactor("bill reconciliation", result.confidence_ceiling, result.summary())


def _shape_is_modelled(site: SiteModel, admission: HalfHourlyAdmission | None) -> bool:
    """Whether this run's shape is MODELLED, per the run's own finding or the site's label.

    Both are consulted rather than either alone: the admission is what the run actually concluded,
    but it is optional on this call, and a site can declare the basis without one being passed.
    """
    if admission is not None and admission.basis is HalfHourlyBasis.MODELLED:
        return True
    declared = (site.load_data.half_hourly_basis or "").strip().lower()
    return declared == HalfHourlyBasis.MODELLED.value


def _register_composition(records: Sequence[DerivedAssumption]) -> str:
    """What the register actually holds, counted by class rather than summed into one word.

    The sentence used to read "N input(s) required an assumption or a customer permission" over
    every record, which is a **false claim of provenance** the moment a record is a `not_applicable`
    evidenced absence: it tells the reader that Colnbrook's DUoS line was assumed, when Colnbrook's
    own record says no distribution charge exists for a 100% private-wire supply and the residual it
    does pay is already in the stack. Asking that customer to "confirm the assumption" asks them for
    a rate no supplier could ever issue them.

    Counted rather than listed so the sentence stays one line however long the register is; the
    per-record detail is the binding-record list beside it, and every record's full statement
    reaches the quote warnings from `gridq.quotes.pipeline`.
    """
    counts = {
        "derived_input": 0,
        "pending_permission": 0,
        "not_applicable": 0,
    }
    for record in records:
        counts[record.kind] = counts.get(record.kind, 0) + 1
    parts = [
        (counts["derived_input"], "required an assumption"),
        (counts["pending_permission"], "await a customer permission"),
        (counts["not_applicable"], "are EVIDENCED ABSENCES, not assumptions"),
    ]
    stated = [f"{n} input(s) {label}" for n, label in parts if n]
    if not stated:  # pragma: no cover - callers only reach here with a non-empty register
        return "0 input(s) in the assumption register"
    if len(stated) == 1:
        return stated[0]
    return f"{'; '.join(stated[:-1])}; and {stated[-1]}"


def _derived_factor(
    records: Sequence[DerivedAssumption],
    site: SiteModel,
    admission: HalfHourlyAdmission | None,
) -> ConfidenceFactor:
    """The ceiling imposed by every input this run had to stand in for (**D44**).

    Each record carries the ceiling its target field allows (`gridq.schemas.derived.ceiling_for_key`
    — structural feasibility inputs LOW, shape-like inputs MEDIUM, never HIGH), and this factor is
    the **weakest** of them. That is the same rule as the band itself: a well-sourced derived tariff
    line cannot compensate for a guessed connection capacity.

    No records is not evidence of strength — it means nothing had to be assumed, so this factor
    imposes no ceiling of its own and the other factors decide the band.

    **Except that "nothing had to be assumed" is not sayable about a modelled site, and the guard
    is structural rather than a wording choice.** A site whose `half_hourly_basis` is MODELLED has
    had its *entire within-day load shape* assumed from an archetype. The five Grundon sites
    carried that basis with an EMPTY register, so this factor reported "no input had to be
    assumed, every value the run consumed was supplied" over a run whose load shape was an
    assumption end to end. No
    number was wrong (the Gate-2 factor still capped the band at MEDIUM) but the **disclosure** was,
    and for a client report a false claim of provenance is worse than a wrong figure, because it
    tells the reader there is nothing to check.

    So the sentence is gated on the site, not on the record list: it is emitted only when the shape
    is not modelled. A modelled site with no shape record gets a REGISTER INCOMPLETE factor naming
    the gap instead. Back-filling the records is the fix for those five sites; this makes the same
    silence impossible for the next site, which is the part a wording change could not do.

    **Only a `derived_input` can discharge that disclosure.** The shape test was on the key alone,
    and `DerivedAssumption` permits a `pending_permission` on a shape key, so a valueless record
    reading *"requires customer consent"* against `load_data.half_hourly_reference_profile`
    satisfied it and silenced REGISTER INCOMPLETE while naming no archetype, no anchor and no
    confirmation the reader could act on. A record only discloses the shape if it says which shape
    was used, and only the `derived_input` class may carry that.

    **The three classes are counted separately, and the sentence says so.** A `not_applicable`
    record is an *evidenced absence*, not an assumption: folding it into "N inputs required an
    assumption" is the same false-provenance error as the sentence above, one class along. It still
    caps the band exactly as before (see the return below) — the class is a disclosure change and
    moves no band and no number.
    """
    modelled = _shape_is_modelled(site, admission)
    discloses_shape = any(
        r.key in SHAPE_DISCLOSURE_KEYS and r.kind == "derived_input" for r in records
    )
    if modelled and not discloses_shape:
        # Never HIGH, and never better than a modelled shape's own ceiling: the shape IS an
        # assumption whether or not anybody recorded it, so an unrecorded one cannot score better
        # than a recorded one. The record ceilings still apply on top, so a structural LOW elsewhere
        # is not softened by this branch.
        ceiling = ConfidenceBand.weakest(
            [
                ceiling_for_key("load_data.half_hourly_reference_profile"),
                *[r.confidence_ceiling for r in records],
            ]
        )
        return ConfidenceFactor(
            "derived assumptions",
            ceiling,
            "REGISTER INCOMPLETE. This site's half-hourly basis is MODELLED, so its entire "
            "within-day load shape is an assumption, but no derived-assumption record on the "
            "record discloses it: the assumption register cannot state which archetype was used, "
            "what the shape was scaled to, which components are therefore measured, or what would "
            "confirm it. Treat the disclosure as missing rather than the assumption as absent, and "
            "record the shape on `load_data.half_hourly_reference_profile` before this run is "
            "shown to a client",
        )
    if not records:
        return ConfidenceFactor(
            "derived assumptions",
            ConfidenceBand.HIGH,
            "no input had to be assumed — every value the run consumed was supplied, and the load "
            "shape is not modelled",
        )
    # The ceiling folds EVERY record, whatever its class. A `not_applicable` record is deliberately
    # NOT exempted: exempting it would raise the band on the private-wire sites whose DUoS lines it
    # describes, and a band is evidential strength about the whole case, not a per-line score. The
    # class earns its keep in the sentence below, not in the number.
    ceiling = ConfidenceBand.weakest([r.confidence_ceiling for r in records])
    binding = [r for r in records if r.confidence_ceiling is ceiling]
    named = "; ".join(f"{r.key} ({r.report_label})" for r in binding[:3])
    more = "" if len(binding) <= 3 else f" (+{len(binding) - 3} more)"
    return ConfidenceFactor(
        "derived assumptions",
        ceiling,
        f"{_register_composition(records)}; the binding one(s): {named}{more}. Each is stated on "
        "the quote with its basis and the exact confirmation ask — supplying them is what raises "
        "this ceiling",
    )


def _study_basis_factor(quote: QuoteRecord) -> ConfidenceFactor:
    """The cap imposed by the PRICING BASIS this record was priced on (**D46/D55**).

    `StudyBasis.note()` states, on every artifact of a study run, that "this run's confidence
    band is capped at LOW". Until this factor existed nothing in ``src/`` read
    ``StudyBasis.confidence_ceiling`` on the way to a band: `generate_quote` capped the record's
    ``confidence_ceiling`` FIELD, but `assess_confidence` builds the band from factors alone and
    never consults that field, so the sentence was true only by **coincidence** of the shipped
    library having zero confirmed assumptions (`_assumption_factor` → LOW on every run today). A
    sentence that is accurate by accident stops being accurate the moment the accident does, and the
    first confirmed assumption would have published a MEDIUM-band study run whose own disclosure
    said LOW.

    **The ceiling is COMPUTED from the D45 policy, not restated here.** A study basis stands on a
    derived export limit — a STRUCTURAL feasibility input — and a derived export tariff, which
    `ceiling_for_key` does not classify and therefore also caps at LOW. Taking the weakest of the
    two through the policy function means this factor cannot drift away from
    `StudyBasis._enforce_named_basis`, which validates the basis's own declared ceiling against the
    same two keys. Neither the factor nor the producer can raise it.

    **Detection is on the persisted id, never on prose.** ``study_basis_id`` is a `Literal` the
    schema writes (`quotes.assembly.assemble_quote_record`); the alternative — matching a prefix
    against `StudyBasis.note()` in the warnings — is what silently mislabelled a D55 run as an
    underwriting one on 2026-08-18, and a band must not be switchable by an edit to a disclosure
    sentence. An UNRECOGNISED id still caps: any declared second basis is a study basis, and
    over-capping is the safe error.

    ``None`` — the underwriting basis, and every record persisted before the field existed — imposes
    no cap. That is deliberate rather than lenient: the band is the minimum across factors, so a
    HIGH here can never raise a band, and a legacy study record's band was already assessed at the
    time it ran. What it does mean is that this factor must never be read as *evidence* that a
    ``None`` record was underwritten (see `QuoteRecord.study_basis_id`).

    Read with ``getattr``, matching `_disclosure_factor` above: a caller may legitimately hand
    `assess_confidence` a record-shaped stub rather than a full `QuoteRecord`, and a NEW optional
    field must not turn every such caller into an `AttributeError`. The absent case is the same
    "this record does not say" as an explicit ``None``, so the two collapse correctly.
    """
    basis_id = getattr(quote, "study_basis_id", None)
    if basis_id is None:
        return ConfidenceFactor(
            "pricing basis",
            ConfidenceBand.HIGH,
            "priced on the UNDERWRITING basis — wholesale/P415 and every other normally-gated "
            "stream are gated upside and contribute nothing to the priced floor, so the basis "
            "itself imposes no cap",
        )
    ceiling = ConfidenceBand.weakest(
        [ceiling_for_key(StudyBasis.TARIFF_KEY), ceiling_for_key(StudyBasis.EXPORT_LIMIT_KEY)]
    )
    banner = study_basis_label(basis_id) or basis_id
    return ConfidenceFactor(
        "pricing basis",
        ceiling,
        f"priced on the named FEASIBILITY-STUDY basis {basis_id!r} ({banner}), which puts "
        "normally-gated revenue into the priced floor at a DERIVED export tariff up to a DERIVED "
        "export limit, against consents and awards that are ASSUMED and not held. The export limit "
        "is a structural feasibility input and the tariff is unclassified, so the D45 ceiling "
        f"policy caps this run at {ceiling.value.upper()} whatever the rest of the evidence "
        "supports. A FIGURE FROM THIS RUN MAY NEVER BE PRESENTED AS AN UNDERWRITING OUTPUT — "
        "re-run on the underwriting basis for anything lender-facing",
    )


def _disclosure_factor(site: SiteModel, quote: QuoteRecord) -> ConfidenceFactor:
    """Whether the record contradicts itself, or asserts more than it can evidence.

    This is the factor that gets a disclosure *seen*. The pack prints the binding factors in the
    strip beside the verdict; everything else lands in the warnings further down. A record whose two
    accounts of its own verdict disagree, or one whose blocking ask is unanswered, is not merely
    documented badly — it is weak evidence, and the band is the right instrument for that.

    Only the disclosures marked ``caps_confidence`` count (a conflict, an unevidenced verdict claim,
    a blocking ask). The informational ones — the sizing verdict restated, the two ask lists
    separated — are disclosure, not a defect in the evidence, and must not move a band.
    """
    from gridq.quotes.disclosures import confidence_capping_disclosures, record_disclosures

    result = getattr(quote, "optimisation_result", None)
    grid = getattr(quote, "sizing_grid", None)
    capping = confidence_capping_disclosures(record_disclosures(site, result, grid))
    if not capping:
        return ConfidenceFactor(
            "record disclosures",
            ConfidenceBand.HIGH,
            "the record does not contradict itself, asserts nothing its own figures cannot "
            "evidence, and carries no ask marked blocking",
        )
    codes = ", ".join(dict.fromkeys(d.code for d in capping))
    return ConfidenceFactor(
        "record disclosures",
        ConfidenceBand.LOW,
        f"{len(capping)} disclosure(s) on this record cap what it can carry ({codes}) — each is "
        "stated in full in the warnings. A record that holds two incompatible accounts of its own "
        "verdict, or a claim no persisted figure evidences, or an unanswered blocking ask, cannot "
        "be strong evidence however good the rest of the inputs are",
    )


def assess_confidence(
    site: SiteModel,
    quote: QuoteRecord,
    *,
    admission: HalfHourlyAdmission | None = None,
    reconciles: bool | None = None,
    reconciliation: ReconciliationResult | None = None,
    bill_reconciliation: BillReconciliationResult | None = None,
    derived_assumptions: Sequence[DerivedAssumption] | None = None,
    uses_unconfirmed_assumptions: bool | None = None,
) -> ConfidenceAssessment:
    """Assign the confidence band from the evidence, capped by the weakest input.

    Every factor states a CEILING and the band is the minimum. A strong input never compensates
    for a fatal one — perfect tariff data cannot make a guessed load shape trustworthy.

    ``uses_unconfirmed_assumptions`` comes from the run's `CashflowModel` (it is not a
    `QuoteRecord` field); ``None`` means the run did not record it, which is itself a LOW ceiling.

    ``reconciliation`` is Gate 3's full verdict and is preferred over the legacy ``reconciles``
    bool, which cannot express a WARNING-level divergence. ``derived_assumptions`` are the inputs
    this run had to stand in for (**D44**): the site's own recorded derived values plus whatever the
    gates recorded on the way through. Omitting them is honestly "nothing was assumed" — the band is
    then decided by the other factors, exactly as before.
    """
    records = list(derived_assumptions or ())
    if not records:
        records = list(site.assumptions_requiring_confirmation)
    factors = (
        _hh_factor(admission),
        _tier_factor(site.data_tier),
        _tariff_factor(site),
        _reconciliation_factor(reconciles, reconciliation),
        _bill_reconciliation_factor(bill_reconciliation),
        _revenue_factor(quote),
        # §3's "with the confidence band adjusted" for a missing debt structure, made real: an
        # unlevered selection is a size no covenant was ever applied to, and the band says so.
        _financing_basis_factor(quote),
        _assumption_factor(quote, uses_unconfirmed_assumptions),
        # D46/D55: the PRICING BASIS caps the band. Read off `QuoteRecord.study_basis_id` — the
        # structured field, never the disclosure prose — so a study run cannot publish a band
        # stronger than the one its own `note()` promises the reader. HIGH (no cap) on the
        # underwriting basis, so no existing band moves.
        _study_basis_factor(quote),
        # `site` and `admission` are passed, not a precomputed flag: the "nothing was assumed"
        # sentence must be unsayable for a modelled site by construction, and a flag a caller can
        # forget to set would put that guarantee back in the caller's hands.
        _derived_factor(records, site, admission),
        # Last, and read off the record itself: a record that contradicts its own verdict or claims
        # more than it evidences is weak evidence, and this is what carries that into the band —
        # and so into the strip the pack prints beside the verdict.
        _disclosure_factor(site, quote),
    )
    band = ConfidenceBand.weakest([f.ceiling for f in factors])
    return ConfidenceAssessment(band=band, factors=factors)
