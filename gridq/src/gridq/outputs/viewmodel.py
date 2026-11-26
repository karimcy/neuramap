"""Extract a typed, render-ready view model from a stamped `QuoteRecord` (+ optional `SiteModel`).

This is the only module in M8 that *reads the run record*. It does two honest things and nothing
else:

1. **Classify** what the record already says — e.g. each value stream's firmness decides whether
   it sits in the priced floor (firm/contracted), the merchant upside (conditional + gated), or is
   excluded; the optimisation result's `bankable_status` decides the banner.
2. **Aggregate values that already exist on the record** — e.g. the floor revenue total is the
   ``sum`` of the firm/contracted streams' own ``annual_revenue_gbp``. No new financial figure is
   *authored*: a total is only ever a sum of record values (tests pin this), the cashflow series
   are passed through verbatim, and a missing figure becomes ``None`` (rendered as an explicit
   *unavailable* label downstream), never a guess.

The renderers consume this view model and never touch the raw record, so the "numbers come only
from the record" and "no arithmetic in the HTML generator" rules hold by construction.

The `QuoteRecord` does not embed the `SiteModel` (it carries only ``site_id``), so the
counterparty / consents / gate sections need the site passed alongside; without it they render as
*site context not supplied* rather than inventing anything.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from itertools import chain
from textwrap import shorten

from gridq.schemas import (
    AvailabilityWindow,
    BankableStatus,
    DerivedAssumption,
    Firmness,
    LifecycleRecord,
    QuoteMode,
    QuoteRecord,
    SiteModel,
    SizingGrid,
    SizingGridPoint,
    ValueStreamResult,
    study_basis_label,
)

# The priced floor is firmness ∈ {firm, contracted}; conditional + gated upside are merchant
# (held out of the priced case); excluded is not priced. Mirrors finance `_PRICED_FIRMNESS`
# (cashflows.py) and the value-stack / data-contract definition (OPEN_COMMENTS C014).
_FLOOR_FIRMNESS: frozenset[Firmness] = frozenset({Firmness.FIRM, Firmness.CONTRACTED})

# Only the firm (underwritten and later) modes cite gated streams. The fast/banded path
# (draft, indicative) "never cites gated streams" (System Brief, indicative lifecycle) — the
# dedicated `gated_upside` entries are omitted from those artifacts.
_GATED_CITING_MODES: frozenset[QuoteMode] = frozenset(
    {QuoteMode.UNDERWRITTEN, QuoteMode.CONTRACTED, QuoteMode.OPERATING}
)

# The standing seam notes for figures v1 cannot produce honestly (named, never invented).
_RATE_TENOR_NOTE = (
    "Per-site discount rate & tenor come from the M9 counterparty credit→rate/tenor mapping, "
    "which is unresolved (BLOCKERS B001). Not shown rather than guessed."
)
_BILL_PENDING_REASON = (
    "Bill before/after and savings need the customer-bill model (M5), which v1 does not yet "
    "compute. Surfaced as a labelled seam, never estimated from partial data."
)
_BILL_DATA_ASK = (
    "Supply the site's current annual bill (or a full tariff stack + 12-month half-hourly load) "
    "and confirm the dispatch energy volumes so finance can compute the before/after bill."
)
#: The BEFORE-only seam: a bill IS persisted, its BEFORE side is real, and the AFTER side does not
#: exist because no dispatch does. Distinct from `_BILL_PENDING_REASON`, which states the opposite
#: fact (no bill at all), and it carries no data ask on purpose: the after side is missing because
#: no configuration was selected, not because the customer owes us a figure. Inventing an ask here
#: would be a fresh false claim in place of the one it replaces.
#: The marker the pricing path writes on a value stream whose revenue was genuinely NEVER COMPUTED,
#: so the recorded ``0.0`` is an absent figure rather than a priced one. Matched as a substring
#: because the pricing path prefixes it with the stream's own context.
#:
#: **This is a contract with `optimisation.valuation.VALUE_UNAVAILABLE_MARKER`**, which is the
#: producer and the authority on when the condition holds; the two strings are pinned equal by
#: `tests/test_no_spec_dispatch_evidence.py` rather than imported, so `outputs` keeps no dependency
#: on `optimisation`. Duplicated deliberately, pinned so it cannot drift.
#:
#: **It is no longer a proxy for "a run that selected nothing".** It used to ride the sentence "no
#: dispatch solved - value unavailable", which a no-spec record wrote on EVERY stream and which was
#: false on all eight live campaign sites: dispatch was solved (162 candidates, all optimal) and, on
#: six of them, it also RAN. A no-spec run whose candidates dispatched nothing now reports a
#: MEASURED ``0.0`` and carries no marker, because that zero is a result; a no-spec run whose
#: candidates dispatched still carries it, because this record reports no candidate's figures.
_VALUE_UNAVAILABLE = "value unavailable (not invented)"
#: The label the pricing path writes on a run priced under ANY named feasibility-study basis.
#: Matched as a prefix on the record's own warnings: the label is the signal, the body its reason.
#:
#: **Deliberately basis-agnostic, and this is load-bearing.** It read ``"STUDY BASIS (D46)"`` until
#: 2026-08-18, which silently excluded D55's ``all_streams_enabled_study_v1`` — whose label begins
#: ``"STUDY BASIS (D55)"``. Measured consequence on a real bc_mrf record: ``vm.study_basis`` was
#: ``False`` on a D55 run, so all four renderers dropped their basis banner and the customer
#: proposal called a study run an "underwriting run". D46's doctrine is that the labelling IS the
#: mitigation, so a detector that recognises one basis by number is the single worst place in the
#: codebase to hard-code one. Any future basis matches here for free; what a renderer PRINTS comes
#: from `OutputViewModel.study_basis_label` — the record's own words — never from a constant here.
#:
#: **It is the FALLBACK, not the detector.** `QuoteRecord.study_basis_id` (2026-08-18) is the
#: machine-readable discriminator and is read first; this prefix exists only for a record persisted
#: before that field, which carries ``study_basis_id=None`` even when a study basis priced it. See
#: `detect_study_basis`.
_STUDY_BASIS_PREFIX = "STUDY BASIS ("

#: The banner for a record that SAYS it was priced on a study basis but whose basis this build
#: cannot name: a ``study_basis_id`` newer than this code, with no prose note to fall back on.
#:
#: It exists so the honest half of the statement survives the unknown half. The alternative -- and
#: the state before it -- was `study_basis_label` returning ``None`` and the memo banner rendering
#: the literal ``> **None:**``, which reads as a rendering bug and invites the reader to discount
#: the whole line. Deliberately says LESS than any real basis label: it claims only what is
#: certain (this is not an underwriting output) and names the gap, because the one thing worse than
#: an unnamed basis is a named wrong one. Zero em dashes: it reaches the memo, which is not
#: scrubbed at document level.
#: Phrases joined by ", " on purpose, matching what `formatting.no_dashes` leaves behind when it
#: scrubs a real `StudyBasis.LABEL`, so `consulting_report._basis_short_form` cuts this label to its
#: leading phrase exactly as it cuts a recognised one.
_STUDY_BASIS_UNNAMED_LABEL = (
    "STUDY BASIS (BASIS NOT RECOGNISED BY THIS BUILD), NOT AN UNDERWRITING OUTPUT"
)

#: The label the pricing path writes when a stream's ``0.0`` is the optimiser's own priced
#: decision: candidates were solved on the full series and no half-hour's spread repaid the round
#: trip. **A contract with `optimisation.valuation.ZERO_DISPATCH_LABEL`**, which is the producer;
#: the two strings are pinned equal by a test rather than imported, so `outputs` keeps no
#: dependency on `optimisation` (the same arrangement as `_VALUE_UNAVAILABLE`).
_ZERO_DISPATCH_LABEL = "GBP 0 IS A DISPATCH DECISION, NOT A MISSING INPUT"

#: The three bases a recorded figure can rest on, which a bare "GBP 0.00" collapses into one.
#: A reader of a zero row most needs to know **which** zero it is, and the record already
#: distinguishes all three; before this they were indistinguishable on the page.
#:
#: - `FIGURE_BASIS_DECIDED`: the optimiser priced the stream and declined the trade.
#: - `FIGURE_BASIS_GATED`: the stream was held out of the priced floor by an availability gate
#:   (a null declared input under D45, or an eligibility rule the site does not clear).
#: - `FIGURE_BASIS_UNMEASURED`: no dispatch was solved, so no figure was ever computed.
FIGURE_BASIS_DECIDED = "decided"
FIGURE_BASIS_GATED = "gated"
FIGURE_BASIS_UNMEASURED = "unmeasured"

#: Display copy for each basis, held here so the three renderers cannot word it differently.
_FIGURE_BASIS_LABELS: dict[str, str] = {
    FIGURE_BASIS_DECIDED: "Priced, then declined",
    FIGURE_BASIS_GATED: "Held out by a gate",
    FIGURE_BASIS_UNMEASURED: "Not measured",
}

#: The gate states that hold a stream out of the priced floor, from
#: `value_stack.interface.GateStatus` (``closed`` = a required input is wholly absent;
#: ``conditional`` = unlockable but a precondition is unmet). Read from the **structured** field
#: rather than sniffed out of warning prose: the zero-dispatch label is stamped on every stream of
#: an unselected run, including gated ones, so prefix-matching alone would report a gated stream as
#: one the optimiser priced and declined, which is a claim its record does not make.
_GATED_GATE_STATUS: frozenset[str] = frozenset({"closed", "conditional"})
_BILL_BEFORE_ONLY_REASON = (
    "No system is recommended on this run, so no after-battery bill and no saving exist to set "
    "against it. The bill shown is the site's delivered cost today as modelled from its "
    "recorded load on the tariff stack the record states (the bill's basis says whose rates "
    "they are), which is dispatch-invariant and is the figure the bill-vs-invoice gate "
    "reconciles."
)

#: The one heading the best-candidate block carries in all four documents. It is a full clause, not
#: a noun, because the two halves must never be separated: a reader who skims the heading and reads
#: nothing else must still not come away with a size they think is on offer. Held here so the four
#: renderers cannot word it differently, and so a test can pin the phrase a reader is owed.
BEST_CANDIDATE_HEADING = "Best evaluated candidate, not a recommendation"

#: The two "Counted in" values the candidate register uses. Held apart from the top-level
#: register's vocabulary on purpose: nothing on a no-spec run is counted in anything, and reusing
#: "Priced floor" unqualified beside a candidate size would read as a case being made for it.
BEST_CANDIDATE_IN_FLOOR = "In this candidate's priced floor"
BEST_CANDIDATE_HELD_OUT = "Held OUT, not in any total here"

#: An absent cost line on an evaluated candidate. It carries its reason wherever it is printed: an
#: unrecorded cost is NOT a cost of zero, so the revenue-vs-cost comparison is withheld rather than
#: completed with a stand-in, and a bare "n/a" would be the same cell a zero could occupy (D45).
NO_CANDIDATE_COST_REASON = (
    "the sizing grid records no such cost line for this candidate, which is an absent figure and "
    "not a cost of zero"
)

#: A record with NO ``financial_case`` at all. The pipeline's finance call refuses for want of a
#: positive sizing, so this state means "no system was costed", never "a case was run and fell
#: short". An UNLEVERED selection (`SizingGrid.selection_basis == "unlevered"`) DOES produce a case,
#: verified on a real production run, so the two absences are genuinely different facts and the four
#: documents state this one in the same words.
NO_FINANCIAL_CASE_REASON = (
    "no financial case exists on this record: the sizing search selected no system, so no "
    "cashflow, return or coverage figure was ever computed for one. This is an absence, not a "
    "figure that failed a test, and it is not an economic verdict"
)

#: Why the block exists at all, in the renderers' own voice. Stated wherever the block is printed,
#: because the figures under it are the only measured revenue on a no-spec pack and a reader has to
#: know both that they are real and that they bind nobody.
BEST_CANDIDATE_CAVEAT = (
    "These are CANDIDATE measurements from the sizing search, not a specification. The search "
    "selected no system, so nothing here is offered, quoted or recommended, and no capex, debt "
    "service or tax is netted from the figures below. They are stated because the alternative "
    "reading of an all-zero revenue column, that this site earns nothing, is not what the record "
    "says."
)


@dataclass(frozen=True)
class StreamRow:
    """One value stream as the artifacts display it (revenue + firmness + gating)."""

    stream: str
    annual_revenue_gbp: float
    firmness: str
    gate_status: str | None
    unlock_condition: str | None
    assumptions_used: tuple[str, ...]
    # The orthogonal "consent given" label copied verbatim from the record's
    # ``ValueStreamResult.consent_status`` ("given" when the customer explicitly authorised this
    # stream at intake, else None). It never affects firmness or a number — it drives the
    # renderers' "Consent given" annotation only (Revenue-Data Drive slice 2c / D43).
    consent_status: str | None = None
    #: The stream's own warnings, verbatim from `ValueStreamResult.warnings`. Carried because the
    #: record uses them to say when a stream's ``annual_revenue_gbp`` is a stand-in rather than a
    #: measurement: a run that selected no system writes ``0.0`` alongside "no dispatch solved,
    #: value unavailable (not invented)". Without this the artifacts could not tell a priced zero
    #: from an absent one, and they printed the second as the first.
    warnings: tuple[str, ...] = ()
    #: Citation numbers into `ContractedMerchantSplit.stream_notes` for this row's own warnings,
    #: assigned in `_split` once the notes have been de-duplicated across every group. Empty when
    #: the record wrote no note against the stream. The row cites; the note carries the text.
    note_numbers: tuple[int, ...] = ()

    @property
    def figure_basis(self) -> str | None:
        """Which of the three bases this row's recorded figure rests on, or None if unstated.

        A "GBP 0.00" cell reads identically whether the optimiser priced the trade and declined it,
        a gate held the stream out of the priced floor, or no dispatch was solved at all. The
        record distinguishes all three; this is where that distinction survives into a renderer.

        Order matters, and it is strictest-first. An unmeasured figure is not a figure, so it
        outranks everything. A gate that held the stream out is read from the **structured**
        ``gate_status``, never from warning prose, because the zero-dispatch label is stamped on
        every stream of a run that selected nothing, gated streams included. Only a stream whose
        gate is not shut may be reported as one the optimiser priced and declined.
        """
        if not self.warnings:
            return None
        if not self.value_measured:
            return FIGURE_BASIS_UNMEASURED
        if self.gate_status in _GATED_GATE_STATUS:
            return FIGURE_BASIS_GATED
        if any(w.startswith(_ZERO_DISPATCH_LABEL) for w in self.warnings):
            return FIGURE_BASIS_DECIDED
        return None

    @property
    def figure_basis_cell(self) -> str:
        """The basis marker plus its note citations, as every renderer prints it.

        Assembled once here rather than three times in the renderers so the wording cannot drift
        between the memo, the report and the consulting pack. String assembly only: no figure is
        computed and no record text is rewritten.
        """
        label = _FIGURE_BASIS_LABELS.get(self.figure_basis or "")
        if not self.note_numbers:
            return label or "n/a"
        word = "note" if len(self.note_numbers) == 1 else "notes"
        cite = f"{word} {', '.join(str(n) for n in self.note_numbers)}"
        return f"{label} (see {cite})" if label else f"See {cite}"

    @property
    def value_measured(self) -> bool:
        """Whether this row's revenue is a measured figure rather than an unavailable stand-in.

        Keyed on the record's own disclaimer, not on the value: ``0.0`` is a perfectly good priced
        result for a stream that earns nothing, and the two states must not be conflated in either
        direction.

        The disclaimer it keys on is now a real absence marker (see `_VALUE_UNAVAILABLE`), so this
        answers the question it always claimed to. Before, every no-spec record carried the marker
        on every stream, which made this permanently ``False`` on all eight live campaign sites --
        including the two whose ``0.0`` is the most thoroughly measured figure on the record.
        """
        return not any(_VALUE_UNAVAILABLE in w for w in self.warnings)


@dataclass(frozen=True)
class StreamNote:
    """One distinct warning the record wrote against a value stream, numbered for citation.

    Notes are de-duplicated across the whole stack, which is the point of the type. A run that
    selected no system writes the same paragraph of dispatch arithmetic onto **every** stream, so
    printing each row's warnings under each row reproduces one 250-word paragraph eight times, in
    a pack an adversarial review already returned as bloated. Stated once, with the streams it
    covers named, the same disclosure costs an eighth of the words and reads as one finding.

    ``streams`` is why the note survives where the row does not: `report.html` and `memo.md`
    render excluded streams as a name list rather than table rows, so a null-input stream such as
    ``DUOS_RED_BAND_AVOIDANCE`` has no row to carry a marker. The note names its own streams and is
    therefore complete on its own.
    """

    number: int
    text: str
    streams: tuple[str, ...]


@dataclass(frozen=True)
class ContractedMerchantSplit:
    """The contracted-vs-merchant split (System Brief, Output 2).

    ``floor_total_gbp`` / ``upside_total_gbp`` are sums of the rows' own record revenues — an
    aggregation of existing values, not a new figure.
    """

    floor: tuple[StreamRow, ...]
    upside: tuple[StreamRow, ...]
    excluded: tuple[StreamRow, ...]
    floor_total_gbp: float
    upside_total_gbp: float
    # True iff any classified stream carries an explicit "consent given" basis. A categorical
    # fact (not a number) the number-free prose keys off to add its consent clause; False keeps
    # the copy identical to the no-consent path (slice 2c).
    has_consented: bool = False
    #: Every distinct `ValueStreamResult.warnings` entry across the three groups, stated once,
    #: numbered in first-appearance order, each naming the streams it applies to. The rows cite
    #: these by number through `StreamRow.figure_basis_cell`. Empty when no stream carries a note.
    stream_notes: tuple[StreamNote, ...] = ()

    @property
    def floor_value_measured(self) -> bool:
        """Whether ``floor_total_gbp`` totals measured revenues or unavailable stand-ins.

        False when any floor stream carries the record's "value unavailable (not invented)"
        disclaimer. The total is still the record's own sum and is still shown; what changes is
        that no artifact may describe it as revenue the case counts, because the record says the
        figures behind it were never computed. An empty floor is vacuously measured: there is
        nothing standing in for anything.
        """
        return all(r.value_measured for r in self.floor)

    @property
    def upside_value_measured(self) -> bool:
        """The same question for the merchant upside total (see `floor_value_measured`)."""
        return all(r.value_measured for r in self.upside)


@dataclass(frozen=True)
class BestCandidateStreamRow:
    """One value stream's £ on the best evaluated candidate, with its floor membership.

    A copy of `SizingGridStreamRevenue`: no reclassification happens here. ``in_priced_floor`` is
    the grid's own flag, stamped by the pricing path from `schemas.market.is_priced_floor`, so the
    floor rows on the page compose exactly the total the finance model built the candidate's
    cashflows on.
    """

    stream: str
    annual_revenue_gbp: float
    firmness: str
    gate_status: str | None
    in_priced_floor: bool


@dataclass(frozen=True)
class BestCandidateView:
    """What the best size the search evaluated MEASURED, on a run that specified no system.

    **The defect this exists to close.** A no-spec record reports no candidate, so every value
    stream on its top-level `OptimisationResult` reads ``0.00`` and every row of the pack's stream
    register prints "Not measured". A client reads that column as "there is no value at this site".
    On the 2026-08-15 campaign that reading was false in the strongest possible way: the same run's
    persisted sizing grid held a best candidate measuring 893.85 of tariff arbitrage and 569.30 of
    DUoS red-band avoidance, from a dispatch its own stream note recorded as discharging
    30,133.0 kWh/yr. The customer proposal went further and asserted "no value has been calculated
    for any of these streams", which the record contradicts.

    The figures below are that measurement, lifted verbatim off the grid. Every one of them is a
    **candidate** figure and none of them is a recommendation: the search selected nothing, and what
    the record says beside that is that on these sites the measured revenue does not cover the
    measured cost. (Whether that *is* why the search selected nothing is a cause the record does not
    state, and nothing this view supplies asserts it: see `customer_paragraph`, whose closing clause
    is branched for exactly that reason.) Read as evidence under the negative verdict the numbers
    strengthen it; read as a system on offer they would be a fabrication, which is why `headline`
    carries the words "not a recommendation" into all four documents.

    **Scoped to no-spec runs.** On a run that DID select a system the pack states that system, and a
    second "best candidate" block beside it would invite exactly the confusion this class exists to
    prevent. `_best_candidate` returns ``None`` there.

    **Confirmed candidates only.** Proxy points are typical-period scoring solves scaled to a year
    and they read HIGH (on the demo site the best proxy floor is 66,651.91 against 61,889.20
    confirmed). Quoting one would overstate the site in the very sentence written to stop it being
    understated.

    The three sentence builders take a money formatter rather than importing
    `gridq.outputs.formatting`, so the copy is authored ONCE for the four renderers (the
    `_FIGURE_BASIS_LABELS` arrangement) while the module stays value-only and free of a
    `report_charts` import cycle.
    """

    power_kw: float
    duration_hours: float
    energy_kwh: float
    #: Year-1 PRICED-FLOOR revenue, verbatim from ``SizingGridPoint.annual_revenue_gbp`` (itself a
    #: copy of ``CashflowModel.total_annual_revenue_gbp``). NOT the sum of ``floor_streams`` plus
    #: ``held_out_streams``: the held-out entries are outside the floor by construction.
    annual_revenue_gbp: float
    annual_operating_cost_gbp: float | None
    annual_dispatch_cost_gbp: float | None
    #: The two recorded cost lines added, or ``None`` when either was not recorded. An unrecorded
    #: cost line is not a cost of zero (D45), so the comparison is withheld rather than completed
    #: with a stand-in.
    annual_cost_total_gbp: float | None
    #: Revenue less the recorded cost stack: a year-1 operating figure BEFORE capex, debt service
    #: and tax. ``None`` on the same terms as ``annual_cost_total_gbp``. Never a return.
    net_annual_gbp: float | None
    #: How many confirmed (non-proxy) candidates carried a measured floor, i.e. the population this
    #: one is the best of. Stated so "best" is a rank in a named set, not a superlative.
    confirmed_candidate_count: int
    floor_streams: tuple[BestCandidateStreamRow, ...] = ()
    held_out_streams: tuple[BestCandidateStreamRow, ...] = ()

    @property
    def covers_cost(self) -> bool | None:
        """Whether the measured floor covers the measured cost stack, or ``None`` if unknowable."""
        if self.annual_cost_total_gbp is None:
            return None
        return self.annual_revenue_gbp > self.annual_cost_total_gbp

    def headline(self, gbp: Callable[[float], str]) -> str:
        """The exhibit's own finding, carrying the label every renderer must print.

        The words "best evaluated candidate" and "not a recommendation" are in one sentence on
        purpose: a reader who takes only the heading away must not be able to leave with a size.
        """
        return (
            f"{BEST_CANDIDATE_HEADING}: the best of {self.confirmed_candidate_count} confirmed "
            f"candidate(s) measured {gbp(self.annual_revenue_gbp)} a year on the priced floor, and "
            "no system is recommended on this record"
        )

    def register_lead(self, gbp: Callable[[float], str]) -> str:
        """The sentence that goes immediately above the all-zero stream register.

        Placed at the point of misreading rather than left to a warnings block two thirds of the
        way down: the register is what a reader's eye lands on, and every row of it reads 0.00.
        """
        return (
            f"Every revenue below reads {gbp(0.0)} because this record reports NO CANDIDATE, not "
            "because the dispatch earned nothing. The sizing search measured this site at "
            f"candidate sizes: its best confirmed candidate earned {gbp(self.annual_revenue_gbp)} "
            f"a year on the priced floor. Those figures are stated in full under "
            f"'{BEST_CANDIDATE_HEADING}', and they are candidate measurements, not a system on "
            "offer."
        )

    def customer_paragraph(self, gbp: Callable[[float], str]) -> str:
        """The plain-language version, for the document the customer actually reads.

        The proposal previously said "no value has been calculated for any of these streams", which
        this record contradicts. The replacement is the same verdict with the measurement in it, so
        the do-not-proceed conclusion is strengthened rather than softened.

        **The closing clause is branched, and that is load-bearing.** Where the measured floor does
        NOT cover the recorded cost, the shortfall genuinely is why nothing is proposed. Where it
        DOES cover it, "that is why we are not proposing a system" would be a false causal claim
        about a record that states no cause: the demo site is exactly that shape (a measured
        operating surplus on a run that still selected nothing), so the wrong version of this
        sentence would ship to a customer today, not hypothetically. The covering branch therefore
        says only what the comparison supports, and names no cause the proposal cannot know.
        """
        if self.annual_cost_total_gbp is None or self.net_annual_gbp is None:
            return (
                "We did model your site at a range of battery sizes, and value WAS calculated. The "
                f"best size we tested would earn about {gbp(self.annual_revenue_gbp)} a year on "
                "the revenue we would underwrite. We are still not proposing a system: the size "
                "above is not an offer and it is not a recommendation. It is the best of "
                f"{self.confirmed_candidate_count} sizes we tested, and the running costs it would "
                "have to cover are not recorded on this run, so we will not tell you it pays for "
                "itself."
            )
        if self.covers_cost:
            verdict = "covers"
            closing = (
                "Covering the running costs is not the same as paying for the equipment, which is "
                "not in that comparison, and the modelling still selected no system. "
            )
        else:
            verdict = "does not cover"
            closing = "That is why we are not proposing a system. "
        return (
            "We did model your site at a range of battery sizes, and value WAS calculated. The "
            f"best size we tested ({self.power_kw:g} kW for {self.duration_hours:g} hours) would "
            f"earn about {gbp(self.annual_revenue_gbp)} a year on the revenue we would underwrite, "
            f"and {verdict} the {gbp(self.annual_cost_total_gbp)} a year it would cost to run, "
            f"before the cost of the equipment itself: {gbp(self.net_annual_gbp)} a year. "
            f"{closing}The size above is the best of "
            f"{self.confirmed_candidate_count} we tested. It is not an offer and it is "
            "not a recommendation."
        )


@dataclass(frozen=True)
class SystemSpecView:
    """The sized system (Output 1 figures the memo references).

    ``selection_status`` carries the sizing verdict verbatim from the record (additive,
    2026-07-07): ``"no_viable_configuration"`` means candidates were evaluated and none is
    economically viable — outputs must present that as a first-class verdict, never render an
    empty spec as if data were merely missing. ``selection_verdict_reason`` is the engine's own
    stated reason (the record warning that starts "no viable configuration"), verbatim.
    """

    selected_power_kw: float | None
    selected_energy_kwh: float | None
    duration_hours: float | None
    implementation_level: str | None
    solver_status: str | None
    selection_status: str | None = None
    selection_verdict_reason: str | None = None
    #: The constraints the chosen size sits against, verbatim from
    #: ``OptimisationResult.constraints_binding`` (E-6). One line of caption truth for the spec
    #: panel and the sizing map: whether connection, throughput or energy limited the selection.
    #: Empty means the record names no binding constraint, which is not the same as none binding.
    constraints_binding: tuple[str, ...] = ()


@dataclass(frozen=True)
class CashflowView:
    """P50/P90 free-cashflow series, passed through verbatim from the record."""

    available: bool
    p50: tuple[float, ...]
    p90: tuple[float, ...]
    year0_p50: float | None
    year0_p90: float | None
    operating_years: int | None


@dataclass(frozen=True)
class DscrView:
    """DSCR + covenant. ``available`` is False when the record carries no min DSCR.

    WP-UW-03 (D75/D76): ``unavailable_reason`` is the record's own reason
    (`DscrSummary.unavailable_reason`), falling back to the historical text only for records
    written before D75. The trailing fields carry the DSCR status, the lock-up tier, the covenant
    source and the lender terms' one-line label, all copied off the record, never derived here.
    """

    available: bool
    min_dscr: float | None
    by_year: tuple[float, ...]
    basis: str
    covenant: float | None
    covenant_min_p90_dscr: float | None
    covenant_passes: bool | None
    unavailable_reason: str | None
    status: str | None = None
    lock_up: float | None = None
    lock_up_passes: bool | None = None
    covenant_source: str | None = None
    covenant_not_evaluated_reason: str | None = None
    lender_terms_label: str | None = None
    lender_terms_synthetic: bool = False


@dataclass(frozen=True)
class SensitivityRow:
    """One row of the DSCR/return sensitivity table (e.g. a scenario case), if supplied."""

    label: str
    min_dscr: float | None
    irr_pct: float | None
    cod_delay_months: int = 0
    note: str | None = None


@dataclass(frozen=True)
class AssumptionSourceRow:
    """One assumption the record's streams cite — value, status, confidence, source (C020/C023).

    The provenance moat displayed where a lender looks: every row is copied off the versioned
    library entry the run actually used; a key the supplied release cannot resolve is shown as
    absent, never silently dropped.
    """

    key: str
    value: str
    unit: str
    status: str
    confidence: str
    source: str


# --- D59: the per-stream assumption register and the methodology view -----------------------
#
# Owner instruction, verbatim (2026-08-18): "somewhere in the report it clearly shows the breakdown
# of revenues per stream and the assumptions made for each of the streams should be shown", and
# "starting with the IRR and Revenue breakdown and graphs, then the methodology and then the
# assumptions register for each site".
#
# Two views, because the owner asked two different questions. `StreamAssumptionRow` answers "what
# is behind THIS stream's pound" for one stream at a time: the library keys its own dispatch cited,
# resolved to their values and sources, and the statements the engine wrote onto the stream,
# including every D57 approval assumed FOR IT. `MethodologyView` answers "how does the machine that
# produced these figures work", and it is deliberately half fixed mechanism copy and half recorded
# fact: the copy describes the method, the recorded rows say what this particular run did, and no
# sentence claims a solver, a stage or an objective the record does not name.


@dataclass(frozen=True)
class StreamAssumptionRow:
    """One revenue stream with the assumptions behind its own figure.

    Every field is a display copy of the record. ``library`` holds the `AssumptionSourceRow`s for
    the keys THIS stream's own `ValueStreamResult.assumptions_used` cites, resolved against the
    release the record pins; ``statements`` are the stream's own warnings verbatim, which is where
    the engine writes the rate it used, the range it took, and the route it assumed; ``approvals``
    is the subset of those statements naming D57, i.e. the approvals assumed for THIS stream.

    A stream that cites no key is a real state and it is stated (``keys_note``), never left blank:
    on the 2026-08-19 campaign five of twelve streams cite none, and a reader is entitled to know
    that the figure's provenance lives in the stream's own statement rather than in the library.
    """

    #: The raw `MarketStream` value. Renderers label it through `formatting.stream_label`.
    stream: str
    annual_revenue_gbp: float
    #: "Priced floor" / "Merchant upside" / "Excluded", the same three words the register uses.
    counted_in: str
    firmness: str
    gate_status: str
    #: The library entries this stream's dispatch cited, in the order the keys sort.
    library: tuple[AssumptionSourceRow, ...]
    #: Said instead of the table when the stream cites no key. Never empty.
    keys_note: str
    #: The statements this stream carries ALONE, verbatim and de-duplicated, order preserved. A
    #: statement carried by more than one stream is CITED instead (see `shared_notes`), never
    #: reproduced here: a run that selects nothing writes the same 250-word dispatch paragraph onto
    #: every stream, and printing it once per stream is the bloat the numbered notes exist to avoid.
    statements: tuple[str, ...]
    #: Those own statements that name D57: the approvals assumed for this stream alone (limb 4). An
    #: approval shared across streams is cited below and printed once in the stream notes.
    approvals: tuple[str, ...]
    #: Citation numbers into `ContractedMerchantSplit.stream_notes` for the statements this stream
    #: shares with another. The number is the citation; the note carries the text, once.
    shared_notes: tuple[int, ...]
    #: Said instead of the statement list when the stream recorded none.
    statements_note: str


#: Said against a stream whose record cites no assumption key. It states the consequence rather
#: than the absence, because a blank cell reads as "no assumption was made", which is false.
_NO_STREAM_KEYS = (
    "This stream's dispatch cited NO assumption-library key on the record, so no library entry can "
    "be shown against its figure. That is a traceability gap, not evidence the figure rests on "
    "nothing: read the stream's own recorded statements below, where the engine wrote the "
    "rate it used and the route it assumed."
)
#: Said against a stream that recorded no statement of its own.
_NO_STREAM_STATEMENTS = (
    "This stream recorded no statement of its own on this run. Where a figure is non-zero and "
    "nothing is stated about it, treat the basis as unevidenced on this record and ask for it."
)
#: The three words the register and this page share, keyed off floor membership.
_COUNTED_IN_FLOOR = "Priced floor"
_COUNTED_IN_UPSIDE = "Merchant upside"
_COUNTED_IN_EXCLUDED = "Excluded"


def _dedup(items: Iterable[str]) -> tuple[str, ...]:
    """``items`` with repeats dropped and first-seen order kept.

    A stream can carry the same sentence more than once (the aggregation and stacking statements
    are written per claim), and printing one sentence five times is not disclosure.
    """
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return tuple(seen)


def _stream_assumptions(
    split: ContractedMerchantSplit,
    assumption_sources: tuple[AssumptionSourceRow, ...],
) -> tuple[StreamAssumptionRow, ...]:
    """One row per stream THE REVENUE REGISTER SHOWS, in the register's own order.

    **It is built from the split, not from the record, and that is a correctness requirement
    rather than convenience.** The split is where the gated-citing rule lives: the fast indicative
    and draft paths never cite gated streams (System Brief, slice 2c), so a register reading
    ``result.gated_upside`` directly puts Wholesale (P415) into an indicative memo that is
    contractually forbidden from mentioning it (`tests/test_outputs_indicative_gated.py`). Reading
    the split means the per-stream register and the revenue register enumerate exactly the same
    streams, in the same order, on every quote mode, which is also what the owner asked for: the
    assumptions behind each stream *in the breakdown*.

    ``split.stream_notes`` supplies the numbered, de-duplicated statements, and this register
    defers to them for anything shared. That is not a shortcut, it is the standing anti-bloat
    invariant: a run that selects nothing writes one 250-word dispatch paragraph onto every
    stream, and a per-stream register that reproduced it would print it once per stream in a
    document already reviewed as bloated (`tests/test_outputs_stream_notes_reach_the_pack.py`). So
    a statement carried by ONE stream is reproduced verbatim on that stream, where it is the only
    place it appears; a statement carried by several is cited by its note number and printed once
    under the revenue register.
    """
    by_key = {row.key: row for row in assumption_sources}
    # Which statements are shared, and what number the notes gave each. Keyed on the exact text, the
    # same key `_number_stream_notes` grouped on, so the two can never disagree about a citation.
    shared_number = {note.text: note.number for note in split.stream_notes if len(note.streams) > 1}
    rows: list[StreamAssumptionRow] = []
    for group, counted in (
        (split.floor, _COUNTED_IN_FLOOR),
        (split.upside, _COUNTED_IN_UPSIDE),
        (split.excluded, _COUNTED_IN_EXCLUDED),
    ):
        for row in group:
            keys = sorted(set(row.assumptions_used))
            statements = _dedup(row.warnings)
            own = tuple(text for text in statements if text not in shared_number)
            cited = tuple(sorted({shared_number[t] for t in statements if t in shared_number}))
            rows.append(
                StreamAssumptionRow(
                    stream=row.stream,
                    annual_revenue_gbp=row.annual_revenue_gbp,
                    counted_in=counted,
                    firmness=row.firmness,
                    gate_status=row.gate_status or "n/a",
                    library=tuple(by_key[key] for key in keys if key in by_key),
                    keys_note=_NO_STREAM_KEYS,
                    statements=own,
                    approvals=tuple(text for text in own if _D57_TOKEN in text),
                    shared_notes=cited,
                    statements_note=_NO_STREAM_STATEMENTS,
                )
            )
    return tuple(rows)


#: The methodology copy: how the engine works, in the order a reader meets it. Fixed prose, held
#: here rather than in four renderers so the four documents cannot describe the same machine
#: differently. Nothing here is a figure and nothing here is per-record: every per-record fact is a
#: row of `MethodologyView.recorded`, copied off the record.
_DISPATCH_METHOD = (
    "The battery's operation is decided by a half-hourly dispatch optimisation over the site's own "
    "load series, not by an annual rule of thumb. One charge variable, one discharge variable and "
    "one state of energy per half hour, bounded by the rated power, the usable energy and the "
    "connection.",
    "Every price component the site faces is a separate array over the SAME half hours: the "
    "commodity price, each DUoS band, the levies and the capacity charge. The optimiser therefore "
    "sees the whole delivered cost of a kilowatt hour at the moment it decides, which is why a "
    "red-band saving and an arbitrage margin can never be counted twice.",
    "Each half hour is sold ONCE, to whichever stream values it most. Service obligations enter as "
    "constraints on the same variables rather than as separate revenue arithmetic, and discharge "
    "carries the degradation cost per megawatt hour the release pins, so a cycle is only taken "
    "where it pays for the life it consumes.",
    "The solved year is the reported year. Revenue per stream is read out of the solved half "
    "hours; "
    "no stream is grossed up from a typical day, and the dispatch day shown in this pack is one "
    "real solved day of that series.",
)
_SIZING_METHOD = (
    "Size is chosen by an outer search over power and duration, with a real dispatch solve at "
    "every "
    "candidate. There is no interpolation between candidates anywhere in this pack: a point on the "
    "sizing surface exists because it was solved.",
    "The search runs in stages. A coarse lattice is scored first on a typical period, which is "
    "fast "
    "and is used only to steer; promising neighbourhoods are refined; and the candidates that "
    "matter are CONFIRMED by re-solving on the full half-hourly series. Proxy-scored and confirmed "
    "points are never presented as the same kind of evidence, and the sizing surface in this pack "
    "labels which is which.",
    "The selected point is the best confirmed candidate on the recorded objective, ranked by one "
    "ordering: where a debt structure resolves, candidates are ranked on the levered return "
    "subject "
    "to the covenant test; where none resolves, the same ordering runs on the unlevered project "
    "return and the selection is stamped unlevered. The record states which happened for this run.",
    "The full evaluated space is published in this pack, not a summary of it, so a reader can see "
    "what the search looked at, where it did not look, and how far the selected point sits from "
    "its "
    "neighbours.",
)
_FINANCE_METHOD = (
    "The financial model is built from the solved dispatch, in one direction only: dispatch to "
    "revenue to cashflow to return. Year-1 revenue per stream comes from the solved half hours; "
    "the "
    "operating and dispatch cost lines are kept separate exactly as the cashflow model keeps them, "
    "so revenue against cost can be checked rather than taken on trust.",
    "P50 and P90 are two runs of the same cashflow, not a point estimate with a haircut applied at "
    "the end. Streams whose contract does not span the term contribute nothing to the P90 inside "
    "the term where the model says so, and the pack states where that bites.",
    "The returns are computed on the cashflow series, and the DSCR is measured against the "
    "covenant "
    "on the P90 basis where a debt structure exists. Where it does not, the project return is "
    "reported unlevered and no covenant verdict is asserted: an untested covenant is never printed "
    "as a passed one.",
    "No language model touches any figure in this pack. Every number is produced by the "
    "deterministic engine, persisted on the run record, and formatted here for display.",
)
_METHODOLOGY_LEAD = (
    "How the figures above were produced. This section describes the method; the rows at the end "
    "of "
    "it are this run's own record of what the method actually did, copied from the run record."
)
_METHODOLOGY_NOTE = (
    "Identical inputs, assumption release and seed reproduce these figures exactly. The run "
    "identity in the provenance section pins which inputs, which release and which code version "
    "produced this pack."
)
_METHODOLOGY_NO_RECORD = (
    "This run records none of the solver, search or dispatch identifiers, so nothing below states "
    "what the method did on this record. Read the method above as a description of the engine, not "
    "as evidence about this run."
)


@dataclass(frozen=True)
class MethodologyView:
    """How the dispatch, the sizing search and the finance model work, plus what THIS run did.

    Split deliberately. ``dispatch``/``sizing``/``finance`` are fixed mechanism copy: they describe
    the engine and are identical in every pack, so four documents cannot give four accounts of one
    machine. ``recorded`` is per-record and is a display copy throughout: the solver status, the
    search version, the stage counts, the selection basis and the dispatch interval, each labelled.
    A methodology page that asserted a solver the record does not name would be the same class of
    false claim as an invented figure.
    """

    lead: str
    dispatch: tuple[str, ...]
    sizing: tuple[str, ...]
    finance: tuple[str, ...]
    #: (label, value) pairs, each copied off the record. Empty on a record naming none.
    recorded: tuple[tuple[str, str], ...]
    #: Said instead of the rows when the record names nothing. Never empty.
    empty_note: str
    note: str

    @classmethod
    def empty(cls) -> MethodologyView:
        """The view for a model built without a record. States the method, claims nothing else."""
        return cls(
            lead=_METHODOLOGY_LEAD,
            dispatch=_DISPATCH_METHOD,
            sizing=_SIZING_METHOD,
            finance=_FINANCE_METHOD,
            recorded=(),
            empty_note=_METHODOLOGY_NO_RECORD,
            note=_METHODOLOGY_NOTE,
        )


def _methodology(quote: QuoteRecord) -> MethodologyView:
    """The methodology view for one record: fixed method copy plus this run's own identifiers.

    Every row is a display copy. The counts are `len` over recorded lists, which is a count of
    records present and not a figure about the site, and the stage split is the record's own
    ``stage``/``proxy`` fields rather than an inference from the numbers.
    """
    rows: list[tuple[str, str]] = []
    result = quote.optimisation_result
    if result is not None:
        rows.append(("Dispatch solver status, verbatim", result.solver_status.value))
        rows.append(("Engine implementation level", result.implementation_level.value))
        if result.constraints_binding:
            rows.append(("Constraints the record says bind", ", ".join(result.constraints_binding)))
    profile = quote.dispatch_profile
    if profile is not None:
        rows.append(("Solved half hours on the dispatch day shown", str(len(profile.points))))
        rows.append(("Dispatch interval, hours", str(profile.interval_hours)))
        rows.append(("Which day is shown, and why", profile.selection_basis))
        rows.append(("Day index in the solved series", str(profile.representative_day_index)))
    grid = quote.sizing_grid
    if grid is not None:
        rows.append(("Sizing search version", grid.search_version))
        rows.append(("Candidate sizes evaluated", str(len(grid.points))))
        rows.append(
            (
                "Confirmed on the full half-hourly series",
                str(len([point for point in grid.points if point.stage == "confirm"])),
            )
        )
        rows.append(
            (
                "Scored on a typical-period proxy only",
                str(len([point for point in grid.points if point.proxy])),
            )
        )
        rows.append(("Selection basis, verbatim", grid.selection_basis or "not stated"))
        rows.append(("Selection reason, verbatim", grid.selection_reason or "not stated"))
    if quote.assumption_version is not None:
        rows.append(("Assumption release the method ran against", quote.assumption_version))
    if quote.study_basis_id is not None:
        rows.append(("Named pricing basis for this run", quote.study_basis_id))
    return MethodologyView(
        lead=_METHODOLOGY_LEAD,
        dispatch=_DISPATCH_METHOD,
        sizing=_SIZING_METHOD,
        finance=_FINANCE_METHOD,
        recorded=tuple(rows),
        empty_note=_METHODOLOGY_NO_RECORD,
        note=_METHODOLOGY_NOTE,
    )


# --- D56: the consolidated assumptions page -------------------------------------------------
#
# Owner instruction, verbatim (2026-08-18): "ensure all these assumption go on a fresh page in the
# report - always use optimistic assumptions". Recorded as D56 and as rule 4 of CLAUDE.md §2.0.
#
# The page is the mechanism that makes the owner's other three standing rules honest: the optimism
# lives in the value, the candour lives in the label, and the reader sees both in one place. So the
# classes below are never merged into one table — they carry very different weight, and a reader has
# to be able to tell an owner's assertion from a derived fact from a permission nobody has granted.


@dataclass(frozen=True)
class AssumptionPageRow:
    """One row of the consolidated assumptions page.

    Every field is a display copy of something on the record, or a fixed sentence keyed off a
    recorded fact. No figure is authored here: ``value`` is the record's own value formatted, and
    ``published_range`` is transcribed from the record's own basis text for that key.

    ``verbatim`` is the escape hatch, and it exists so a row can never be *dropped*: when a
    record-derived warning cannot be parsed into the columns, the whole statement is carried here
    and rendered in one wide cell instead. Losing a row silently is the one failure this page
    cannot survive.
    """

    #: The `DerivedAssumption` kind this row belongs to, or ``"attestation"`` for a commitment the
    #: site record carries. Drives which block the row lands in, never the report language.
    kind: str
    key: str
    value: str
    unit: str
    #: The class's report language, verbatim from `DerivedAssumption.report_label`. The three kinds
    #: never share a sentence, so this is copied rather than re-derived here.
    label: str
    #: The published range the value was chosen from (D56), or "" where none is transcribed.
    published_range: str
    source: str
    status: str
    #: "up" / "down" / "neutral" / "not established". Never a guess: see `_direction_for`.
    direction: str
    direction_basis: str
    confirmation_ask: str
    asserted_by: str = ""
    verbatim: str = ""


@dataclass(frozen=True)
class AssumptionsBlock:
    """One assumption class on the page, with the fixed lead that states what the class means.

    ``empty_note`` is rendered *instead of* the rows when there are none, so "no pending
    permissions on this run" and "we forgot to look" can never read the same way. An empty class is
    an explicit row, never a missing section.
    """

    title: str
    lead: str
    rows: tuple[AssumptionPageRow, ...]
    empty_note: str
    source: str


@dataclass(frozen=True)
class AssumptionsPageView:
    """The whole assumptions page: which basis produced the pack, then one block per class.

    Built by `_assumptions_page` from the run record, the site record and the pinned assumption
    release. Held on `OutputViewModel` so all four artifacts render the same page from the same
    rows rather than each assembling its own.
    """

    #: Which basis produced this pack, and whether it may be read as an underwriting output.
    basis_statement: str
    site_supplied: bool
    #: What this page could and could not see. Different sentence when no site record was supplied,
    #: because "nothing is recorded" and "we could not look" are different facts.
    site_note: str
    blocks: tuple[AssumptionsBlock, ...]
    #: The per-key library register (`_assumption_sources`), reproduced on the page so the claim
    #: "every assumption the run rests on" is true of the page and not only of its first half.
    library_rows: tuple[AssumptionSourceRow, ...]
    library_note: str
    footnote: str

    @property
    def row_count(self) -> int:
        """How many classed rows the page carries (the library register is counted separately).

        Counted with `len` over a flattened list rather than ``sum``: every render-time computation
        in this module is audited by `tests/test_provenance_claim.py`, which reads the AST, and a
        row count is not a figure anyone should have to find on a disclosure list to trust.
        """
        return len([row for block in self.blocks for row in block.rows])

    @classmethod
    def empty(cls) -> AssumptionsPageView:
        """The page for a view model built without one. Never silently blank: it says so."""
        return cls(
            basis_statement=_BASIS_NOT_ASSESSED,
            site_supplied=False,
            site_note=_SITE_NOT_SUPPLIED,
            blocks=(),
            library_rows=(),
            library_note=_LIBRARY_ABSENT,
            footnote=_PAGE_FOOTNOTE,
        )


@dataclass(frozen=True)
class PriceView:
    """The price recommendation (structure + value + whether it hits target).

    ``exceeds_counterfactual``/``counterfactual_import_gbp_per_kwh`` (additive, 2026-07-07)
    surface the commercial-viability check: when the price-to-target exceeds the customer's
    current blended import cost, outputs must present the number as a viability verdict, never
    an offer.

    ``structure`` is ``None`` when the record carries no `FinancialCase`. It used to default to
    ``"fixed_gbp_per_kwh"`` in that state, which is how eight do-not-proceed packs and their
    proposals came to print a commercial structure as a record fact beside an unavailable price.
    A structure is a term of an offer: no priced case, no structure. The five render sites all go
    through `formatting.structure_label`, so fixing the source fixes every one of them.
    """

    structure: str | None
    value_gbp_per_kwh: float | None
    hits_target_return: bool | None
    counterfactual_import_gbp_per_kwh: float | None = None
    exceeds_counterfactual: bool | None = None


@dataclass(frozen=True)
class DebtView:
    """Per-site debt assumptions; all-None means no debt terms reached the run.

    ``unavailable_reason`` (WP-UW-03) is the record's DSCR reason when no terms were supplied;
    ``principal_gbp`` / ``sized_by`` the loan actually sized and which bound set it (D77);
    ``lender_terms_source`` the supplied terms' provenance source, when any.
    """

    available: bool
    gearing_pct: float | None
    rate_pct: float | None
    tenor_years: float | None
    sculpting_basis: str | None
    principal_gbp: float | None = None
    sized_by: str | None = None
    lender_terms_source: str | None = None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class ReturnsView:
    """Project return metrics (IRR / payback) as the record carries them.

    ``equity_irr_pct`` is the levered (equity) IRR on the P50 base case, copied verbatim from
    `FinancialCase.equity_irr` (E-5). The view used to drop it, so the levered return never reached
    an artifact even when the record carried one, while the sizing grid plots exactly that metric.
    ``None`` when there is no debt structure or the equity series is non-conventional: measured,
    never guessed.
    """

    irr_pct: float | None
    payback_years: float | None
    equity_irr_pct: float | None = None
    #: Whether a `FinancialCase` exists at all. Without it, ``irr_pct`` and ``payback_years`` are
    #: absent because **nothing was evaluated**, which is a different fact from a case that was
    #: evaluated and produced no payback. The memo and the HTML report rendered the second reason
    #: for the first state, telling readers a system "does not pay back over the horizon" on runs
    #: where no system was ever costed. The reason is the claim here, so the flag is load-bearing.
    case_available: bool = False

    @property
    def irr_unavailable_reason(self) -> str:
        """Why no IRR is shown. Mirrors `DscrView.unavailable_reason`: absence carries a reason."""
        if not self.case_available:
            return "no financial case on this record, so no return was evaluated"
        return "no positive return to solve an IRR"

    @property
    def payback_unavailable_reason(self) -> str:
        """Why no payback is shown, and never the wrong one of the two reasons."""
        if not self.case_available:
            return "no financial case on this record, so no payback was evaluated"
        return "does not pay back over the horizon"


@dataclass(frozen=True)
class CounterpartyView:
    """Counterparty identity + coarse credit standing (needs the site)."""

    available: bool
    legal_name: str | None
    company_number: str | None
    sector: str | None
    credit_status: str | None
    expected_tenure_years: float | None
    notes: str | None
    rate_tenor_note: str


@dataclass(frozen=True)
class ConsentsGatesView:
    """Consent + physical-gate status (needs the site)."""

    available: bool
    consents: tuple[tuple[str, str], ...]
    gates: tuple[tuple[str, str], ...]
    gate_notes: str | None


@dataclass(frozen=True)
class BillSavingsView:
    """Customer bill before/after + savings, read from `QuoteRecord.customer_bill` (W7-5/C017).

    **Two independent facts, two flags, because since 2026-08-15 a bill no longer implies a
    saving.** ``available`` iff the record carries a persisted bill at all; a record without one
    keeps the labelled-pending seam, never an estimate. ``after_side_priced`` iff that bill has a
    priced AFTER side, mirroring `CustomerBill.after_side_priced`: a run that recommends no system
    still persists its dispatch-invariant BEFORE bill (the bill-vs-invoice gate reconciles exactly
    that side), and on such a record ``available`` is True while every AFTER figure is ``None``.

    **Every renderer that makes a claim ABOUT the saving branches on ``after_side_priced``, never
    on ``available``.** Branching on ``available`` is what put "the customer's own saving is on the
    record" into a report that holds no saving. The rule that no LLM authors a number extends to
    prose asserting what the numbers show: a sentence about a figure is as false as the figure.

    ``pending_reason`` is non-empty in BOTH absent states and states which one applies, so a
    consumer that reaches its unavailable branch by any route (a ``None`` total, a ``None`` saving)
    prints a reason rather than an empty parenthetical. ``basis`` is the bill's honesty label (what
    is and is NOT modelled) and must render alongside the headline wherever the saving is shown.
    """

    available: bool
    bill_before_gbp: float | None
    bill_after_gbp: float | None
    annual_savings_gbp: float | None
    pending_reason: str
    data_ask: str
    #: Mirrors `CustomerBill.after_side_priced`. Defaults to the claim-nothing value: a view built
    #: without stating the mode asserts no saving.
    after_side_priced: bool = False
    basis: str | None = None
    # Informational peak-import physics (W9/C029) — never priced; labelled where rendered.
    peak_before_kw: float | None = None
    peak_after_kw: float | None = None
    # The bill's component decomposition, verbatim from `CustomerBill` (E-4). Totals alone answer
    # "how much"; the components answer "where the change comes from", which is the customer's
    # actual question. ``standing_gbp`` and ``capacity_gbp`` are the battery-invariant fixed lines,
    # carried identically on both sides — they are kept as the two separate recorded values because
    # their sum is not a figure the record carries. All ``None`` on a record without a bill.
    energy_before_gbp: float | None = None
    energy_after_gbp: float | None = None
    duos_before_gbp: float | None = None
    duos_after_gbp: float | None = None
    levies_before_gbp: float | None = None
    levies_after_gbp: float | None = None
    standing_gbp: float | None = None
    capacity_gbp: float | None = None
    import_before_kwh: float | None = None
    import_after_kwh: float | None = None


@dataclass(frozen=True)
class ProvenanceView:
    """The traceability spine: every figure ties back to these (X.2)."""

    quote_id: str
    quote_mode: str
    site_id: str
    data_tier: str
    input_hash: str | None
    assumption_version: str | None
    code_version: str | None
    schema_version: str
    expiry_date: date | None
    created_at: datetime


@dataclass(frozen=True)
class OutputViewModel:
    """Everything the three artifacts render, extracted once from a single run record."""

    bankable: bool
    bankable_status: str
    quote_mode: str
    data_tier: str
    site_supplied: bool
    spec: SystemSpecView
    cashflows: CashflowView
    dscr: DscrView
    sensitivities: tuple[SensitivityRow, ...]
    split: ContractedMerchantSplit
    price: PriceView
    debt: DebtView
    returns: ReturnsView
    counterparty: CounterpartyView
    consents_gates: ConsentsGatesView
    bill_savings: BillSavingsView
    provenance: ProvenanceView
    assumption_sources: tuple[AssumptionSourceRow, ...]
    warnings: tuple[str, ...]
    #: What the best size the sizing search evaluated actually MEASURED, on a run that specified no
    #: system. ``None`` on a run that selected one (the pack states that system instead), and on any
    #: record whose grid carries no measured candidate. Every renderer prints it under
    #: `BEST_CANDIDATE_HEADING`; see `BestCandidateView` for why an all-zero revenue column on a
    #: no-spec pack is a false impression the record itself can correct.
    best_candidate: BestCandidateView | None = None
    #: True when the run was priced on ANY named feasibility-study basis — D46's export-only
    #: basis or D55's all-streams basis — each of which prices normally-gated revenue INTO the
    #: priced floor and is therefore **not an underwriting output** (CLAUDE.md §6, D46/D55). Read
    #: from `QuoteRecord.study_basis_id` where the record carries it and from the record's own
    #: labelled warning where it does not (`detect_study_basis`), never assumed. Every artifact
    #: must state it where a reader cannot miss it:
    #: the whole risk of a second basis is a reader taking it for the first, and it used to appear
    #: once, two thirds of the way down one of the four documents.
    study_basis: bool = False
    #: That warning verbatim, for the surfaces that show the reason in full. ``None`` on an
    #: underwriting-basis run **and** on a study record that names its basis by id but carries no
    #: prose note, so a surface that promises "the reason is reproduced verbatim below" must check
    #: this field rather than `study_basis` before making that promise.
    study_basis_note: str | None = None
    #: The consolidated assumptions page (D56 limb 2, rule 4 of CLAUDE.md 2.0): every assumption
    #: the run rests on, classed, with its value, its source or published range, its direction of
    #: effect and its exact confirmation ask. Every artifact renders it on a page of its own.
    #: Defaulted so a view model built by an older caller still carries an honest page that says it
    #: was built without a record, rather than a blank one.
    assumptions_page: AssumptionsPageView = field(default_factory=AssumptionsPageView.empty)
    #: The basis's OWN label — e.g. ``"STUDY BASIS (D46) — EXPORT PRICED IN THE PRICED FLOOR —
    #: NOT AN UNDERWRITING OUTPUT"`` or D55's all-streams equivalent — resolved from the record's
    #: ``study_basis_id`` and, on a legacy record, from the note's first sentence
    #: (`detect_study_basis`). Renderers print THIS rather than a constant of their own, so a
    #: five-stream basis is never described in export-only words. ``None`` when absent.
    study_basis_label: str | None = None
    #: Per-stream assumption rows (D59, owner instruction 2026-08-18): for EACH revenue stream, the
    #: assumption-library keys its own dispatch cited and the statements the engine wrote onto it,
    #: including every D57 approval assumed for that stream. Held here so all four artifacts show
    #: one register rather than four scattered accounts of the same figures.
    stream_assumptions: tuple[StreamAssumptionRow, ...] = ()
    #: How the dispatch, the sizing search and the finance model work, plus this run's own record of
    #: what they did (D59). Rendered as its own section, after the value story and before the
    #: assumptions register, which is the order the owner specified.
    methodology: MethodologyView = field(default_factory=MethodologyView.empty)
    #: The machine-readable basis, straight off `QuoteRecord.study_basis_id`: e.g.
    #: ``"all_streams_enabled_study_v1"``. Carried so a surface can tell two study bases apart
    #: without parsing display copy, and so the frontend formats a recorded value rather than
    #: deciding one (CLAUDE.md §8). ``None`` on an underwriting run **and** on a legacy study record
    #: detected by its prose alone, so it is never the thing to branch "is this a study run" on -
    #: that is `study_basis`.
    study_basis_id: str | None = None
    #: WP-UW-17 (D131): the record's lifecycle ledger — what the run did with the terminal
    #: value, decommissioning and replacements, construction / IDC and indexation — straight off
    #: `FinancialCase.lifecycle`. ``None`` when the record has no financial case (or predates it).
    lifecycle: LifecycleRecord | None = None


def _study_basis_label(note: str | None) -> str | None:
    """The basis's own LABEL, taken from the first sentence of its ``note()``.

    **The contract this stands on**, stated because it is a cross-package one:
    `schemas.study.StudyBasis.note()` and every subclass build the sentence as ``f"{LABEL}. …"``,
    and no ``LABEL`` contains a sentence break — they are single upper-case phrases joined by em
    dashes. So the text up to the first ``". "`` is exactly the label.
    `tests/test_all_streams_study_basis.py` pins that round trip for **both** shipped bases, so a
    future label that breaks the convention
    fails a test here rather than printing a truncated banner in four documents.

    **This is now the fallback path, not the primary one.** It is how the label was recovered before
    `QuoteRecord.study_basis_id` existed, and a record persisted before 2026-08-18 still needs it.
    A record that names its basis gets its label from `schemas.study.study_basis_label`, which
    returns the basis's OWN ``LABEL`` rather than a sentence-split guess at it. See
    `detect_study_basis`.
    """
    if note is None:
        return None
    return note.split(". ", 1)[0]


@dataclass(frozen=True)
class StudyBasisView:
    """Which pricing basis produced a record, and the words the artifacts must print for it.

    Three fields because a reader needs three different lengths of the same fact: a boolean for
    "must this document carry a basis banner", a label for the banner itself, and the record's own
    full statement for the surfaces that reproduce the reason verbatim.
    """

    #: True when the record was priced on ANY named feasibility-study basis.
    present: bool
    #: The basis's own ``LABEL``, or `_STUDY_BASIS_UNNAMED_LABEL` when the record names a basis this
    #: build does not know. ``None`` only when `present` is False.
    label: str | None
    #: The basis's own ``note()`` verbatim, when the record carries it. ``None`` on a record that
    #: names its basis by id but carries no prose note - a real state, see `detect_study_basis`.
    note: str | None
    #: The persisted ``basis_id``, or ``None`` on a legacy record detected by its prose alone.
    basis_id: str | None


def detect_study_basis(quote: QuoteRecord) -> StudyBasisView:
    """Which named study basis priced this record: the ID first, the record's prose as fallback.

    **The defect this closes (measured 2026-08-18 on a real bishops_cleeve_mrf record).** Detection
    was a literal prefix match against `QuoteRecord.warnings`, and the literal was
    ``"STUDY BASIS (D46)"``. D55's `AllStreamsEnabledStudyBasis.LABEL` begins
    ``"STUDY BASIS (D55)"``, so on the basis that prices EVERY stream into the floor the match
    failed, ``study_basis`` came
    back False, and all four renderers dropped their banner: the customer proposal called the run an
    "underwriting run" three times and the pack titled "Underwriting pack" stated its basis in one
    bullet at line 193 of 219, under "## Warnings".

    **Why an id and not a looser prefix.** Loosening the literal to ``"STUDY BASIS ("`` fixes the
    detection and leaves the identity resting on wording: reword a `StudyBasis.note()` and every
    artifact silently stops stating its basis again. `QuoteRecord.study_basis_id` is the structured
    discriminator (`quotes/assembly.py` persists it from the basis that actually priced the solve),
    and `schemas.study.study_basis_label` maps it to the basis's OWN label, so a D55 run can never
    be described in D46's export-only words.

    **Why the prose fallback stays, and this is the load-bearing half.** A record persisted before
    the field existed carries ``study_basis_id=None`` EVEN IF a study basis priced it. ``None``
    means "this record does not say", never "this was an underwriting run". Reading the id alone
    would therefore re-create the original defect on every archived pack. Both sources are consulted
    and EITHER is sufficient; the id wins where they disagree, because it is a field the pipeline
    wrote rather than a sentence a reader could reword.

    The converse case is equally real: `quotes.assembly.assemble_quote_record` called directly with
    ``study_basis=`` (as the mutation tests and any non-`generate_quote` caller do) persists the id
    and writes NO note, because the note is appended by `quotes.pipeline.generate_quote`. Such a
    record was invisible to every renderer under prose-only detection.
    """
    basis_id = quote.study_basis_id
    note = next((w for w in quote.warnings if w.startswith(_STUDY_BASIS_PREFIX)), None)
    if basis_id is None and note is None:
        return StudyBasisView(present=False, label=None, note=None, basis_id=None)
    # `study_basis_label` returns None for an unrecognised id rather than raising, so a
    # forward-dated record renders instead of crashing; the note is tried next and the generic
    # banner last, which states the certain half of the fact when the basis cannot be named.
    label = study_basis_label(basis_id) or _study_basis_label(note) or _STUDY_BASIS_UNNAMED_LABEL
    return StudyBasisView(present=True, label=label, note=note, basis_id=basis_id)


def _stream_row(v: ValueStreamResult) -> StreamRow:
    return StreamRow(
        stream=v.stream.value,
        annual_revenue_gbp=v.annual_revenue_gbp,
        firmness=v.firmness.value,
        gate_status=v.gate_status,
        unlock_condition=v.unlock_condition,
        assumptions_used=tuple(v.assumptions_used),
        consent_status=v.consent_status,
        warnings=tuple(v.warnings),
    )


def _number_stream_notes(
    *groups: list[StreamRow],
) -> tuple[tuple[StreamNote, ...], list[list[StreamRow]]]:
    """De-duplicate every row's warnings into numbered notes, and cite them back onto the rows.

    Grouping is by the warning's exact text, in first-appearance order across the groups as they
    are passed (floor, then upside, then excluded), so the numbering is deterministic for a given
    record and two runs of the same record cite the same numbers.

    No record text is altered, summarised or dropped: a note holds its warning verbatim, and every
    warning on every row becomes exactly one note. What changes is that the text is stated once
    instead of once per stream carrying it.
    """
    streams_by_text: dict[str, list[str]] = {}
    for row in chain.from_iterable(groups):
        # `dict.fromkeys` de-duplicates within a row while keeping the record's own order.
        for text in dict.fromkeys(row.warnings):
            carried = streams_by_text.setdefault(text, [])
            if row.stream not in carried:
                carried.append(row.stream)
    numbers = {text: number for number, text in enumerate(streams_by_text, start=1)}
    notes = tuple(
        StreamNote(number=numbers[text], text=text, streams=tuple(streams))
        for text, streams in streams_by_text.items()
    )
    # Ascending, not the record's own per-row order: a cell reading "notes 3, 4, 5, 6, 1" looks
    # like a mistake, and the numbering is a citation index, not a ranking.
    cited = [
        [
            replace(row, note_numbers=tuple(sorted({numbers[t] for t in row.warnings})))
            for row in group
        ]
        for group in groups
    ]
    return notes, cited


def _split(quote: QuoteRecord) -> ContractedMerchantSplit:
    """Classify the record's value streams into floor / merchant upside / excluded.

    Floor = firmness ∈ {firm, contracted}; merchant upside = conditional value streams + every
    `gated_upside` entry (gated upside can never be firm); excluded = explicitly priced-out.
    Totals are sums of the rows' own record revenues — aggregation, not authoring.

    Gated-upside citing (slice 2c): the fast/banded indicative path still "never cites gated
    streams" (System Brief) — *unless* the customer has explicitly consented to that stream
    (``consent_status == "given"``), in which case it is cited in every mode so the "consent
    given" basis is never hidden. ``_GATED_CITING_MODES`` continues to govern only the
    unconsented gated rows.
    """
    result = quote.optimisation_result
    if result is None:
        return ContractedMerchantSplit(
            floor=(), upside=(), excluded=(), floor_total_gbp=0.0, upside_total_gbp=0.0
        )

    floor: list[StreamRow] = []
    upside: list[StreamRow] = []
    excluded: list[StreamRow] = []
    for v in result.value_streams:
        if v.firmness in _FLOOR_FIRMNESS:
            floor.append(_stream_row(v))
        elif v.firmness == Firmness.EXCLUDED:
            excluded.append(_stream_row(v))
        else:  # conditional value streams are merchant upside, not the priced floor
            upside.append(_stream_row(v))
    # A consented gated stream is cited in every mode; an unconsented one only on the firm
    # (underwritten+) pack (System Brief). Excluded entries carry no priced revenue anyway.
    cites_unconsented_gated = quote.quote_mode in _GATED_CITING_MODES
    upside.extend(
        _stream_row(v)
        for v in result.gated_upside
        if v.consent_status == "given" or cites_unconsented_gated
    )

    floor_total = round(sum(r.annual_revenue_gbp for r in floor), 2)
    upside_total = round(sum(r.annual_revenue_gbp for r in upside), 2)
    has_consented = any(r.consent_status == "given" for r in (*floor, *upside, *excluded))
    # Numbering last, over the finished groups: a note's number is a property of the whole stack,
    # not of the group a row happens to sit in.
    notes, (floor, upside, excluded) = _number_stream_notes(floor, upside, excluded)
    return ContractedMerchantSplit(
        floor=tuple(floor),
        upside=tuple(upside),
        excluded=tuple(excluded),
        floor_total_gbp=floor_total,
        upside_total_gbp=upside_total,
        has_consented=has_consented,
        stream_notes=notes,
    )


def _best_candidate_streams(
    point: SizingGridPoint,
) -> tuple[tuple[BestCandidateStreamRow, ...], tuple[BestCandidateStreamRow, ...]]:
    """The candidate's per-stream figures, split into priced floor and held out.

    The split reads the grid's own ``in_priced_floor`` flag and never the £: a floor stream at 0.00
    was priced and the optimiser declined it, while a held-out stream at 0.00 was never in the
    floor to begin with, and a bare zero collapses those into one (D45). A held-out stream may also
    carry a NON-zero figure, which is why it is kept out of every total rather than merely labelled.
    """
    rows = [
        BestCandidateStreamRow(
            stream=v.stream,
            annual_revenue_gbp=v.annual_revenue_gbp,
            firmness=v.firmness,
            gate_status=v.gate_status,
            in_priced_floor=v.in_priced_floor,
        )
        for v in point.revenue_streams
    ]
    return (
        tuple(r for r in rows if r.in_priced_floor),
        tuple(r for r in rows if not r.in_priced_floor),
    )


def _best_candidate(quote: QuoteRecord) -> BestCandidateView | None:
    """The best CONFIRMED candidate on the persisted grid, on a run that specified no system.

    Selection is deliberately the same rule, on the same field, with the same tie-break as
    `gridq.quotes.disclosures.candidate_revenue_disclosure`, which states these figures in the
    record's warnings. Two pickers that could disagree about which candidate is "best" would put
    two different sizes on one page, which is the record-conflict defect this pack has already been
    caught publishing once. `tests/test_best_candidate_reaches_the_pack.py` pins the two equal on
    rendered bytes.

    Returns ``None`` (and the renderers print nothing) when the run selected a system, when there is
    no grid, or when no confirmed candidate carries a measured floor. The last case includes every
    grid persisted before contract ``0.2.0``, whose silence about revenue is an absence and must not
    be rendered as a zero.
    """
    result = quote.optimisation_result
    if result is not None and result.selected_power_kw:
        return None
    grid: SizingGrid | None = quote.sizing_grid
    if grid is None:
        return None
    # Paired with its own float so the ranking key never has to re-read an Optional the filter has
    # already resolved: the £ that ranks a candidate is the same £ the view reports.
    measured: list[tuple[float, SizingGridPoint]] = [
        (p.annual_revenue_gbp, p)
        for p in grid.points
        if not p.proxy and p.annual_revenue_gbp is not None
    ]
    if not measured:
        return None
    # `max` over (£, then the SMALLER system on a tie): an aggregation over record values, the same
    # operation `_split`'s totals are, and the identical key the record's own disclosure uses.
    floor_gbp, best = max(
        measured, key=lambda pair: (pair[0], -pair[1].power_kw, -pair[1].duration_hours)
    )
    opex = best.annual_operating_cost_gbp
    dispatch_cost = best.annual_dispatch_cost_gbp
    cost_total = None if opex is None or dispatch_cost is None else round(opex + dispatch_cost, 2)
    net = None if cost_total is None else round(floor_gbp - cost_total, 2)
    floor_streams, held_out = _best_candidate_streams(best)
    return BestCandidateView(
        power_kw=best.power_kw,
        duration_hours=best.duration_hours,
        energy_kwh=best.energy_kwh,
        annual_revenue_gbp=floor_gbp,
        annual_operating_cost_gbp=opex,
        annual_dispatch_cost_gbp=dispatch_cost,
        annual_cost_total_gbp=cost_total,
        net_annual_gbp=net,
        confirmed_candidate_count=len(measured),
        floor_streams=floor_streams,
        held_out_streams=held_out,
    )


def _spec(quote: QuoteRecord) -> SystemSpecView:
    result = quote.optimisation_result
    if result is None:
        return SystemSpecView(None, None, None, None, None)
    selection_status = (
        result.selection_status.value if result.selection_status is not None else None
    )
    verdict_reason = None
    if selection_status == "no_viable_configuration":
        verdict_reason = next(
            (w for w in result.warnings if w.startswith("no viable configuration")), None
        )
    return SystemSpecView(
        selected_power_kw=result.selected_power_kw,
        selected_energy_kwh=result.selected_energy_kwh,
        duration_hours=result.duration_hours,
        implementation_level=result.implementation_level.value,
        solver_status=result.solver_status.value,
        selection_status=selection_status,
        selection_verdict_reason=verdict_reason,
        constraints_binding=tuple(result.constraints_binding),
    )


def _cashflows(quote: QuoteRecord) -> CashflowView:
    fc = quote.financial_case
    if fc is None or not fc.p50_cashflows or not fc.p90_cashflows:
        return CashflowView(
            available=False, p50=(), p90=(), year0_p50=None, year0_p90=None, operating_years=None
        )
    p50 = tuple(fc.p50_cashflows)
    p90 = tuple(fc.p90_cashflows)
    return CashflowView(
        available=True,
        p50=p50,
        p90=p90,
        year0_p50=p50[0],
        year0_p90=p90[0],
        operating_years=len(p50) - 1,
    )


def _dscr(quote: QuoteRecord) -> DscrView:
    fc = quote.financial_case
    if fc is None:
        return DscrView(
            available=False,
            min_dscr=None,
            by_year=(),
            basis="P90",
            covenant=None,
            covenant_min_p90_dscr=None,
            covenant_passes=None,
            unavailable_reason="No financial case on this quote.",
        )
    d = fc.dscr_summary
    cov = fc.covenant_status
    available = d.min_dscr is not None
    lender = fc.lender_terms
    return DscrView(
        available=available,
        min_dscr=d.min_dscr,
        by_year=tuple(d.by_year),
        basis=d.basis,
        covenant=cov.covenant,
        covenant_min_p90_dscr=cov.min_p90_dscr,
        covenant_passes=cov.passes,
        unavailable_reason=(
            None if available else (d.unavailable_reason or _LEGACY_DSCR_UNAVAILABLE_REASON)
        ),
        status=d.status.value if d.status is not None else None,
        lock_up=cov.lock_up_dscr,
        lock_up_passes=cov.lock_up_passes,
        covenant_source=cov.source,
        covenant_not_evaluated_reason=cov.not_evaluated_reason,
        lender_terms_label=lender.label() if lender is not None else None,
        lender_terms_synthetic=lender.is_synthetic if lender is not None else False,
    )


#: The reason shown for a record written before WP-UW-03 (D75), which carries no reason of its own.
_LEGACY_DSCR_UNAVAILABLE_REASON = (
    "No debt structure on the record (gearing / rate / tenor absent: see B001); "
    "DSCR is measured, never guessed."
)


def _price(quote: QuoteRecord) -> PriceView:
    fc = quote.financial_case
    if fc is None:
        # No financial case means no price recommendation, so there is no structure to report.
        # Reading the enum's first member here invented a commercial term.
        return PriceView(structure=None, value_gbp_per_kwh=None, hits_target_return=None)
    p = fc.price_recommendation
    return PriceView(
        structure=p.structure.value,
        value_gbp_per_kwh=p.value_gbp_per_kwh,
        hits_target_return=p.hits_target_return,
        counterfactual_import_gbp_per_kwh=p.counterfactual_import_gbp_per_kwh,
        exceeds_counterfactual=p.exceeds_counterfactual,
    )


def _debt(quote: QuoteRecord) -> DebtView:
    fc = quote.financial_case
    if fc is None:
        return DebtView(False, None, None, None, None)
    da = fc.debt_assumptions
    available = any(x is not None for x in (da.gearing_pct, da.rate_pct, da.tenor_years))
    lender = fc.lender_terms
    return DebtView(
        available=available,
        gearing_pct=da.gearing_pct,
        rate_pct=da.rate_pct,
        tenor_years=da.tenor_years,
        sculpting_basis=da.sculpting_basis,
        principal_gbp=da.principal_gbp,
        sized_by=da.sized_by,
        lender_terms_source=lender.provenance.source.value if lender is not None else None,
        unavailable_reason=None if available else fc.dscr_summary.unavailable_reason,
    )


def _returns(quote: QuoteRecord) -> ReturnsView:
    fc = quote.financial_case
    if fc is None:
        return ReturnsView(None, None, case_available=False)
    return ReturnsView(
        irr_pct=fc.irr,
        payback_years=fc.payback_years,
        equity_irr_pct=fc.equity_irr,
        case_available=True,
    )


def _counterparty(site: SiteModel | None) -> CounterpartyView:
    if site is None:
        return CounterpartyView(
            available=False,
            legal_name=None,
            company_number=None,
            sector=None,
            credit_status=None,
            expected_tenure_years=None,
            notes=None,
            rate_tenor_note=_RATE_TENOR_NOTE,
        )
    cp = site.counterparty
    return CounterpartyView(
        available=True,
        legal_name=cp.legal_name,
        company_number=cp.company_number,
        sector=cp.sector,
        credit_status=cp.credit_status.value,
        expected_tenure_years=cp.expected_tenure_years,
        notes=cp.notes,
        rate_tenor_note=_RATE_TENOR_NOTE,
    )


def _consents_gates(site: SiteModel | None) -> ConsentsGatesView:
    if site is None:
        return ConsentsGatesView(available=False, consents=(), gates=(), gate_notes=None)
    c = site.consents
    consents = (
        ("G98", c.g98.value),
        ("G99", c.g99.value),
        ("G100", c.g100.value),
        ("DNO application", c.dno_application_state.value),
        ("Supplier consent", c.supplier_consent_state.value),
    )
    g = site.physical_gates

    def _gate(value: bool | None) -> str:
        if value is None:
            return "unknown"
        return "ok" if value else "issue"

    gates = (
        ("Footprint available", _gate(g.footprint_available)),
        ("Fire separation", _gate(g.fire_separation_ok)),
        ("Access", _gate(g.access_ok)),
        # planning_or_landlord_issue: True means there IS an issue, so invert for the "ok" reading.
        (
            "Planning / landlord",
            "unknown"
            if g.planning_or_landlord_issue is None
            else ("issue" if g.planning_or_landlord_issue else "ok"),
        ),
    )
    return ConsentsGatesView(available=True, consents=consents, gates=gates, gate_notes=g.notes)


def _bill_savings(quote: QuoteRecord) -> BillSavingsView:
    """Read the persisted bill (W7-5/C017); without one, the labelled-pending seam (never
    an estimate). Every figure is copied verbatim off the record — no arithmetic here.

    Three states, not two. A BEFORE-only bill (no dispatch, so no AFTER side) is a persisted bill
    whose saving does not exist, and it carries the reason WHY the AFTER figures are absent so no
    downstream renderer has to guess one or, worse, assert the opposite.
    """
    bill = quote.customer_bill
    if bill is not None:
        priced = bill.after_side_priced
        return BillSavingsView(
            available=True,
            after_side_priced=priced,
            bill_before_gbp=bill.annual_bill_before_gbp,
            bill_after_gbp=bill.annual_bill_after_gbp,
            annual_savings_gbp=bill.annual_saving_gbp,
            pending_reason="" if priced else _BILL_BEFORE_ONLY_REASON,
            data_ask="",
            basis=bill.basis,
            peak_before_kw=bill.peak_import_before_kw,
            peak_after_kw=bill.peak_import_after_kw,
            energy_before_gbp=bill.energy_before_gbp,
            energy_after_gbp=bill.energy_after_gbp,
            duos_before_gbp=bill.duos_before_gbp,
            duos_after_gbp=bill.duos_after_gbp,
            levies_before_gbp=bill.levies_before_gbp,
            levies_after_gbp=bill.levies_after_gbp,
            standing_gbp=bill.standing_gbp,
            capacity_gbp=bill.capacity_gbp,
            import_before_kwh=bill.import_before_kwh,
            import_after_kwh=bill.import_after_kwh,
        )
    return BillSavingsView(
        available=False,
        bill_before_gbp=None,
        bill_after_gbp=None,
        annual_savings_gbp=None,
        pending_reason=_BILL_PENDING_REASON,
        data_ask=_BILL_DATA_ASK,
    )


def _assumption_sources(
    quote: QuoteRecord, assumptions: object | None
) -> tuple[AssumptionSourceRow, ...]:
    """Per-assumption source rows from the keys the record's streams cite (C020/C023).

    ``assumptions`` is the versioned library matching ``quote.assumption_version`` (duck-typed:
    anything with ``.get(section, name)``). ``None`` ⇒ empty (the renderers keep an honest
    pending note; the run-level version always shows in provenance). Values are display copies —
    no arithmetic, truncated for legibility.
    """
    result = quote.optimisation_result
    if assumptions is None or result is None:
        return ()
    keys = sorted(
        {key for v in [*result.value_streams, *result.gated_upside] for key in v.assumptions_used}
    )
    rows: list[AssumptionSourceRow] = []
    for key in keys:
        # A cited key may name a LEAF INSIDE a dict-valued assumption, and the split has to allow
        # for that or the row lies about the release (2026-08-19).
        #
        # THE DEFECT THIS CLOSES. This loop did `section, _, name = key.partition(".")`, so a key
        # like `local_flex_piclo.awarded_zone_clears.<zone>.availability_h_per_year` resolved to
        # section `local_flex_piclo` and name
        # `awarded_zone_clears.<zone>.availability_h_per_year`, which `AssumptionLibrary.get` looks
        # up as `sect[name]` and so raises `KeyError`. The row then rendered as value "n/a", status
        # "absent", source "(key not present in the supplied release)" -- for GBP 174.99/MW/h and
        # 136 h/yr, both of which ARE in gb_v1_12 and both of which PRICE the local-flex
        # availability leg. A pack that tells an underwriter a sourced, priced figure is absent from
        # the release is asserting something false about its own evidence, which is the one thing
        # the assumptions register exists to prevent (D57 limb 4, D59 limb 3).
        #
        # THE SUB-VALUE INHERITS THE PARENT ENTRY'S PROVENANCE, and that is correct rather than
        # convenient: unit, status, confidence and source belong to the `awarded_zone_clears`
        # assumption as a whole, which is the entry that carries the citation. Only the VALUE is
        # narrowed to the leaf the stream actually priced from. A sub-path that genuinely does not
        # resolve still reports absent, and says which hop failed.
        section, _, remainder = key.partition(".")
        name, _, subpath = remainder.partition(".")
        try:
            a = assumptions.get(section, name)  # type: ignore[attr-defined]
        except (KeyError, AttributeError, TypeError):
            rows.append(
                AssumptionSourceRow(
                    key=key,
                    value="n/a",
                    unit="",
                    status="absent",
                    confidence="",
                    source="(key not present in the supplied release)",
                )
            )
            continue
        resolved: object = a.value
        missing_hop: str | None = None
        for part in subpath.split(".") if subpath else []:
            if isinstance(resolved, dict) and part in resolved:
                resolved = resolved[part]
            else:
                missing_hop = part
                break
        if missing_hop is not None:
            rows.append(
                AssumptionSourceRow(
                    key=key,
                    value="n/a",
                    unit=a.unit,
                    status="absent",
                    confidence=a.confidence.value,
                    source=(
                        f"(the release carries {section}.{name} but it has no "
                        f"'{missing_hop}' entry)"
                    ),
                )
            )
            continue
        value = str(resolved)
        if len(value) > 60:
            value = value[:57] + "…"
        rows.append(
            AssumptionSourceRow(
                key=key,
                value=value,
                unit=a.unit,
                status=a.status.value,
                confidence=a.confidence.value,
                source=a.source,
            )
        )
    return tuple(rows)


# --- D56: building the assumptions page's rows -----------------------------------------------

#: The fixed leading label the pricing path writes on every derived-assumption disclosure
#: (`gridq.quotes.pipeline`, two call sites). A prefix test on the label, never a search of the
#: prose: the label is the signal. Parsing the body recovers the gate-emitted records, which reach
#: the quote as warnings and are NOT written back onto the site (doing so would move the identity
#: of every existing run), so without this the page would miss them.
_ASSUMPTION_WARNING_PREFIX = "ASSUMPTION REQUIRING CONFIRMATION ["

#: The fixed tail seams of `DerivedAssumption.statement`, the format this page parses back. A
#: contract with `gridq.schemas.derived`, pinned by `tests/test_assumptions_page.py` against a
#: statement built by the schema itself rather than by a fixture of this module's own making.
_ASK_SEAM = ". Confirm: "
_CEILING_SEAM = ". Confidence capped at "
_BASIS_SEAM = ". Basis: "
#: The dash characters the statement separates its clauses with. Built with `chr` on purpose: the
#: package sweep in `tests/test_outputs_no_em_dashes.py` reads every string literal in this package,
#: so the characters themselves may not appear as literals here either.
_DASH_CHARS = f"{chr(0x2014)}{chr(0x2013)}{chr(0x2015)}-"

#: `DerivedAssumption.report_label` -> kind. A contract with `gridq.schemas.derived`, which is the
#: authority on the sentence each class gets; the strings are pinned equal by
#: `tests/test_assumptions_page.py::test_report_labels_match_the_schema` rather than imported, so
#: this module keeps reading the record's own words and cannot drift from them either.
_KIND_BY_LABEL: dict[str, str] = {
    "assumed, confirmation required": "derived_input",
    "requires customer consent": "pending_permission",
    "does not apply to this site, evidenced": "not_applicable",
}

#: The marker a record writes into its own ``basis`` when it states which way it moves the result.
#: Read first, before any policy below: the record's own account of its direction always wins.
_DIRECTION_MARKER = re.compile(r"DIRECTION OF EFFECT[:\s]+([A-Z]+)")
_DIRECTION_WORDS: dict[str, tuple[str, str]] = {
    "UP": ("up", "stated on the record: DIRECTION OF EFFECT UP"),
    "DOWN": ("down", "stated on the record: DIRECTION OF EFFECT DOWN"),
    "NEUTRAL": ("neutral", "stated on the record: DIRECTION OF EFFECT NEUTRAL"),
    "CONSERVATIVE": (
        "down",
        "stated on the record as CONSERVATIVE, which holds the modelled result down",
    ),
}

#: Direction-of-effect policy, applied ONLY where the record states none itself. Longest matching
#: key prefix wins. Each entry carries the reasoning that justifies it and the page prints that
#: reasoning beside the direction, so a reader can disagree with the call rather than take it.
_DIRECTION_POLICY: tuple[tuple[str, str, str], ...] = (
    (
        "tariff_stack.windowed_levies",
        "up",
        "a windowed levy is an avoided cost, so a higher rate raises modelled revenue",
    ),
    (
        "tariff_stack.duos",
        "up",
        "a network unit rate is an avoided cost, so a higher rate raises modelled revenue",
    ),
    (
        "tariff_stack.levies",
        "up",
        "a levy is an avoided cost, so a higher rate raises modelled revenue",
    ),
    (
        "tariff_stack.delivered_cost_reconciliation",
        "not established",
        "the record states the direction of any error here is unknown, so it is not called either "
        "way",
    ),
    (
        "local_flex_awards",
        "up",
        "an assumed award adds an availability stream and reserves headroom for it, so it raises "
        "modelled revenue",
    ),
    (
        "connection.mic_kw",
        "down",
        "a derived connection limit caps the system the sizing search may consider, so it holds "
        "the modelled result down",
    ),
    (
        "connection",
        "not established",
        "a connection fact moves the result in whichever direction the site's real capacity "
        "differs from the modelled one, and the record does not say which",
    ),
    (
        "load_data",
        "not established",
        "a load basis moves the result in whichever direction the site's real shape differs from "
        "the modelled one, and the record does not say which",
    ),
    (
        "stream_consents",
        "up",
        "the stream is modelled and priced ahead of the permission, so the result is at least as "
        "high as it would be without it",
    ),
)

#: Fallback by class, applied only when neither the record nor the key policy speaks.
_DIRECTION_BY_KIND: dict[str, tuple[str, str]] = {
    "pending_permission": (
        "up",
        "the stream is modelled and priced ahead of the permission, so the result is at least as "
        "high as it would be without it",
    ),
    "not_applicable": (
        "neutral",
        "the component is evidenced absent for this site, so no value enters the model in either "
        "direction",
    ),
}
_DIRECTION_UNKNOWN = (
    "not established",
    "neither the record nor this page's published policy states which way this moves the result, "
    "so it is not called",
)

#: D56 published ranges, transcribed from the record's own ``basis`` and ``sources`` for that key.
#: Two rules keep this from becoming an authored number: an entry is only ever *shown beside* a
#: value the record supplies (the value itself is never taken from here), and a row appears only
#: when the key is actually on the record. `tests/test_assumptions_page.py` pins each string against
#: the checked-in site record that carries it, so the transcription cannot drift from its source.
_PUBLISHED_RANGES: dict[str, tuple[str, str]] = {
    "tariff_stack.windowed_levies.CM_peak": (
        "published range 5.313 to 31.382 p/kWh (npower 'Capacity Mechanism Peak Rate' monthly "
        "series, January 2021 to July 2026)",
        "April 2025 comparator 17.057 p/kWh (that month's initial billing rate)",
    ),
}
_RANGE_NOT_TRANSCRIBED = (
    "published range not transcribed on this page: see this key's basis and sources on the record"
)

_BASIS_NOT_ASSESSED = (
    "The pricing basis for this pack has not been assessed, because this page was built without a "
    "run record. Treat nothing here as an underwriting output."
)
_SITE_NOT_SUPPLIED = (
    "NO SITE RECORD WAS SUPPLIED to this render, so the classes below are sourced from the run's "
    "own warnings alone. Anything recorded on the site record and not restated in a warning cannot "
    "appear here. Read this page as incomplete, not as an empty register."
)
_SITE_SUPPLIED = (
    "Sourced from the site record supplied to this render and from the run's own warnings, so a "
    "gate-emitted assumption that never reaches the site record still appears here."
)
_LIBRARY_ABSENT = (
    "No per-key library register can be built for this run: either the record's streams cite no "
    "assumption keys, or the pinned release was not supplied to this render. That is a gap in "
    "traceability, not evidence that the run used nothing unconfirmed."
)
_PAGE_FOOTNOTE = (
    "Basis and citation text is abbreviated on this page where it runs long; the full text is on "
    "the run record at site.derived_assumptions and site.local_flex_awards. Confirmation asks are "
    "never abbreviated. Every value on this page is a display copy of the record: none is "
    "recomputed here."
)
_NO_ASK_RECORDED = "no confirmation ask is recorded against this row"

#: The blocks, in the order the page prints them. Optimistic selections lead because they are the
#: rows that raise the result and therefore the rows most in need of scrutiny.
_BLOCK_ORDER: tuple[tuple[str, str, str, str], ...] = (
    (
        "d57_assumed_approved",
        "Assumed-approved contracts, consents, awards and thresholds (D57)",
        "Every contract, consent, gate, award, registration, threshold and market rule this run "
        "ASSUMES APPROVED for the site and for the battery size evaluated, on the owner's standing "
        "instruction (D57, Olly, 2026-08-18). Each row is a statement the engine wrote onto the "
        "stream it applies to, quoted verbatim, so the assumption is read beside the pound it "
        "produced. NONE OF THESE IS GRANTED: the record says assumed approved, which is true, "
        "never "
        "granted, which would be a claim about a third party (D45). These rows are the reason this "
        "pack is an ACADEMIC VALIDATION RESULT and not an underwriting output, and they raise the "
        "modelled result, so they are the rows most in need of scrutiny.",
        "no stream on this run rests on a D57 assumed approval.",
    ),
    (
        "optimistic",
        "Optimistic selections (D56)",
        "Where a sourced range exists, the favourable end is taken, on the owner's standing "
        "instruction (D56, Olly, 2026-08-18). Optimistic is not unbounded: every value here is "
        "cited, and what the instruction changes is which end of the cited range is used, never "
        "whether it is cited. The published range is printed beside the value so the choice is "
        "visible. These rows raise the modelled result, so they are the rows most in need of "
        "scrutiny.",
        "no assumption on this run declares, in its own basis, that it raises the result.",
    ),
    (
        "attestation",
        "Owner attestations and asserted commitments (D54, D55)",
        "Commitments the case rests on that nobody here verified, with the party asserting each "
        "one named on its row. Where that party is the site owner the row is an attestation: it "
        "prices into the case, and it is NOT verified against any DNO register, Piclo feed or CMZ "
        "list. An attestation is never evidence of a register match, and this page never presents "
        "one as an award granted.",
        "the site record carries no asserted commitment.",
    ),
    (
        "derived_input",
        "Derived inputs (D45): facts about the site the model derived",
        "Facts about the site that were absent from the evidence and were derived instead. Each "
        "reads assumed, confirmation required, and each carries the single piece of evidence that "
        "would settle it. A derived input is never written as a consent granted.",
        "no site fact was derived on this run.",
    ),
    (
        "pending_permission",
        "Pending permissions: requires customer consent",
        "Permissions that cannot be derived from data at all: a consent, an export MPAN, a G99 "
        "agreement, a contract reference. Each is modelled and its value shown, and each reads "
        "requires customer consent. NONE OF THESE IS GRANTED, and nothing on this page may be "
        "read as saying that it is.",
        "this run rests on no pending permission.",
    ),
    (
        "not_applicable",
        "Evidenced non-applicable components",
        "Components that do not exist for this site, where the emptiness is evidenced rather than "
        "unfilled. No value enters the model for these and no data ask is made against them: "
        "asking a client for a rate no supplier could ever issue them is a defect, not diligence.",
        "no component was found evidenced absent on this site.",
    ),
)

_ATTESTATION_ASK = (
    "The DNO or DSO contract or trade reference for this award, with the awarded volume, the "
    "service windows and the tenor as contracted. Until that arrives this row is an assertion, "
    "not an award on the record."
)
_NO_ASSERTER = "THE RECORD DOES NOT NAME WHO ASSERTS THIS COMMITMENT"
_NO_ATTESTATION_BASIS = "the record states no basis for this commitment"
_ATTESTATION_LABEL = "asserted, not verified"

#: Where each block's rows come from, printed under the block so a reader can go and check.
_BLOCK_SOURCE: dict[str, str] = {
    "d57_assumed_approved": (
        "quote.optimisation_result.value_streams[].warnings + gated_upside[].warnings, filtered to "
        "the statements that name D57 - i.e. the engine's own words on the stream they priced"
    ),
    "optimistic": (
        "site.derived_assumptions + quote.warnings, filtered to the records whose own basis "
        "declares that they raise the result"
    ),
    "attestation": "site.local_flex_awards",
    "derived_input": "site.derived_assumptions + quote.warnings",
    "pending_permission": "site.derived_assumptions + quote.warnings",
    "not_applicable": "site.derived_assumptions + quote.warnings",
}


def _clip(text: str, limit: int) -> str:
    """One long record string, shortened for a table cell with the truncation left visible.

    `textwrap.shorten` rather than a slice: it collapses whitespace, breaks on a word boundary and
    needs no index arithmetic, which keeps this module free of the operators
    `tests/test_provenance_claim.py` audits. A confirmation ask is never passed through here.
    """
    return shorten(text, width=limit, placeholder=" ...")


def _display_number(value: float) -> str:
    """A record number as text, full precision, no arithmetic. Never rounded into a new figure."""
    text = f"{value:,.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _display_value(value: float | str | None, kind: str) -> str:
    """The record's own value, or the reason its class has none. Never a blank and never a zero."""
    if isinstance(value, bool):  # pragma: no cover - a bool value is not a modelling input
        return str(value)
    if isinstance(value, (int, float)):
        return _display_number(float(value))
    if value is not None:
        return _clip(str(value), 140)
    if kind == "pending_permission":
        return "no value used: a permission is not a value"
    if kind == "not_applicable":
        return "no value applies: the component is evidenced absent"
    return "no value derived"


def _record_direction(basis: str) -> tuple[str, str] | None:
    """The direction the record states about ITSELF, or ``None`` when it states none.

    Read before any policy: the record's own account of which way it moves the result always wins,
    and a record that declares nothing is never assigned a direction by inference here.
    """
    match = _DIRECTION_MARKER.search(basis)
    if match is None:
        return None
    word = match.group(1)
    known = _DIRECTION_WORDS.get(word)
    return known if known is not None else (word.lower(), f"stated on the record as {word}")


def _policy_direction(key: str) -> tuple[str, str] | None:
    """This page's published direction policy for ``key``, longest matching prefix wins."""
    best: tuple[str, str] | None = None
    best_len = -1
    for prefix, direction, why in _DIRECTION_POLICY:
        # A plain prefix test, not a dotted-segment one: the keys this policy speaks about are
        # sibling fields with a shared stem (`tariff_stack.duos_red_rate`,
        # `duos_amber_rate`, `duos_capacity_charge`), not children of a dotted parent.
        if key.startswith(prefix) and len(prefix) > best_len:
            best = (direction, f"{why} (this page's published policy for {prefix})")
            best_len = len(prefix)
    return best


def _direction_for(key: str, kind: str, basis: str) -> tuple[str, str]:
    """Direction of effect and the account of where it came from, in three tiers, never guessed."""
    return (
        _record_direction(basis)
        or _policy_direction(key)
        or _DIRECTION_BY_KIND.get(kind)
        or _DIRECTION_UNKNOWN
    )


def _published_range(key: str) -> str:
    """The transcribed published range for ``key``, or "" where none is transcribed here."""
    entry = _PUBLISHED_RANGES.get(key)
    return "" if entry is None else f"{entry[0]}. {entry[1]}."


def _block_for(kind: str, basis: str) -> str:
    """Which block a row lands in: optimistic when the RECORD says it raises the result.

    The row keeps its own ``kind`` and ``label`` either way, so a reader in the optimistic block
    still sees which class the row belongs to. The classes are never merged, only re-grouped.
    """
    stated = _record_direction(basis)
    return "optimistic" if stated is not None and stated[0] == "up" else kind


def _row_from_record(record: DerivedAssumption) -> tuple[str, AssumptionPageRow]:
    """One page row from a structured `DerivedAssumption` on the site record."""
    direction, direction_basis = _direction_for(record.key, record.kind, record.basis)
    sources = "; ".join(record.sources) if record.sources else "NO CITATION RECORDED"
    return (
        _block_for(record.kind, record.basis),
        AssumptionPageRow(
            kind=record.kind,
            key=record.key,
            value=_display_value(record.value, record.kind),
            unit=record.unit or "",
            label=record.report_label,
            published_range=_published_range(record.key) or _range_absent(record.kind),
            source=f"Sources: {_clip(sources, 420)} Basis: {_clip(record.basis, 320)}",
            status=(
                f"confidence capped at {record.confidence_ceiling.value.upper()}; "
                f"{record.derivation_method}, by {_clip(record.derived_by, 120)}"
            ),
            direction=direction,
            direction_basis=direction_basis,
            confirmation_ask=record.confirmation_ask,
        ),
    )


def _range_absent(kind: str) -> str:
    """What to print in the range column for a row that has no transcribed range."""
    if kind == "pending_permission":
        return "not a range: a permission has no published value"
    if kind == "not_applicable":
        return "not a range: no value applies to this site"
    return _RANGE_NOT_TRANSCRIBED


def _parse_assumption_warning(warning: str) -> tuple[str, AssumptionPageRow] | None:
    """One page row from a run warning, for the records that never reach the site record.

    ``None`` when the warning is not a derived-assumption disclosure. A disclosure that cannot be
    split into columns still returns a row, carrying the whole statement in ``verbatim``: a row on
    this page may be ugly, but it may never be missing.

    Split with `str.rpartition`, from the tail inwards, and never with index arithmetic. The basis
    prose in the middle of the statement contains colons, full stops and the word "confirmation",
    so anything anchored from the left mis-parses; the fixed tail labels are the reliable seams,
    and taking the LAST occurrence of each is what makes the basis prose harmless.
    """
    if not warning.startswith(_ASSUMPTION_WARNING_PREFIX):
        return None
    label = warning.partition("[")[2].partition("]")[0]
    kind = _KIND_BY_LABEL.get(label, "derived_input")
    body = warning.partition("]")[2].lstrip().lstrip(_DASH_CHARS).lstrip()
    key = body.partition(":")[0].strip()
    head, ask_seam, ask = body.rpartition(_ASK_SEAM)
    head, cap_seam, ceiling = head.rpartition(_CEILING_SEAM)
    head, basis_seam, basis = head.rpartition(_BASIS_SEAM)
    _, used_seam, used = head.rpartition(f"{label}. ")
    if not (key and ask_seam and cap_seam and basis_seam):
        direction, direction_basis = _direction_for(key, kind, warning)
        return (
            _block_for(kind, warning),
            AssumptionPageRow(
                kind=kind,
                key=key or "(the key could not be read from this disclosure)",
                value="see the full statement in this row",
                unit="",
                label=label or "(no class label on this disclosure)",
                published_range=_published_range(key) or _range_absent(kind),
                source="the run's own warning, reproduced in full below because it could not be "
                "split into columns",
                status="recorded in the run warnings, not on the site record",
                direction=direction,
                direction_basis=direction_basis,
                confirmation_ask=_NO_ASK_RECORDED,
                verbatim=warning,
            ),
        )
    value = used.strip().removeprefix("used ") if used_seam else ""
    direction, direction_basis = _direction_for(key, kind, basis)
    return (
        _block_for(kind, basis),
        AssumptionPageRow(
            kind=kind,
            key=key,
            value=_clip(value, 140) or "the statement records no value for this row",
            unit="",
            label=label,
            published_range=_published_range(key) or _range_absent(kind),
            source=f"Basis: {_clip(basis, 520)}",
            status=(
                f"confidence capped at {ceiling.strip().upper()}; recorded in the run warnings, "
                "not on the site record"
            ),
            direction=direction,
            direction_basis=direction_basis,
            confirmation_ask=ask.strip() or _NO_ASK_RECORDED,
        ),
    )


def _window_label(window: AvailabilityWindow) -> str:
    times = f"{window.start_time} to {window.end_time}"
    return f"{window.label} ({times})" if window.label else times


def _attestation_rows(site: SiteModel | None) -> tuple[AssumptionPageRow, ...]:
    """One row per commitment the site record asserts, with the asserter named on the row.

    The asserter travels with the assertion (D54): a commitment the owner attests and one
    transcribed from a DNO award are the same shape on the record and must never read the same way
    on the page, so ``confirmed_by`` is printed verbatim and is never defaulted to a system id.
    """
    if site is None or not site.local_flex_awards:
        return ()
    rows: list[AssumptionPageRow] = []
    for commitment in site.local_flex_awards:
        basis = commitment.basis or _NO_ATTESTATION_BASIS
        direction, direction_basis = _direction_for("local_flex_awards", "attestation", basis)
        bits = [f"{_display_number(commitment.committed_power_kw)} kW committed"]
        if commitment.held_energy_kwh is not None:
            bits.append(f"{_display_number(commitment.held_energy_kwh)} kWh held")
        if commitment.tenor_years is not None:
            bits.append(f"tenor {_display_number(commitment.tenor_years)} yr")
        bits.append(f"stream {commitment.stream.value}")
        windows = "; ".join(_window_label(w) for w in commitment.windows)
        rows.append(
            AssumptionPageRow(
                kind="attestation",
                key=f"local_flex_awards[{commitment.commitment_id}]",
                value=", ".join(bits),
                unit="",
                label=_ATTESTATION_LABEL,
                published_range="not a range: an assertion has no published range",
                source=(
                    f"Windows: {windows} | Counterparty: "
                    f"{commitment.counterparty or 'not named on the record'} | "
                    f"Basis: {_clip(basis, 420)}"
                ),
                status=f"reference on the record: {commitment.reference or 'NONE'}",
                direction=direction,
                direction_basis=direction_basis,
                confirmation_ask=_ATTESTATION_ASK,
                asserted_by=commitment.confirmed_by or _NO_ASSERTER,
            )
        )
    return tuple(rows)


#: The token every D57 assumed-approval statement carries. The engine writes these onto the stream
#: they apply to, so the page harvests them from the run record rather than requiring each one to be
#: mirrored onto the site as a `DerivedAssumption` - which would be a second, drifting copy of a
#: sentence the stream already owns.
_D57_TOKEN = "D57"


def _d57_rows(quote: QuoteRecord) -> list[AssumptionPageRow]:
    """One row per D57 assumed approval, quoted verbatim from the stream it was written on.

    **Why this exists.** D57 limb 4 requires that every assumed approval is STATED on a dedicated
    assumptions page - it is the mechanism that makes limbs 1-3 honest, because the optimism lives
    in
    the value and the candour lives in the label, and a reader has to see both in one place. The
    engine writes each approval onto the `ValueStreamResult` it applies to, and this page previously
    read only the site record's `DerivedAssumption` list and ``quote.warnings``, so NONE of them
    reached it: the stacking waiver, the DFS registration assumption, the Capacity Market
    commitment assumption and the agreed-capacity attestation were all stated nowhere a reader
    looks.

    Rows are deterministic: streams in record order, statements in the order the engine wrote them,
    de-duplicated on the exact sentence so one waiver repeated across five streams renders once with
    every stream that carries it named.
    """
    result = getattr(quote, "optimisation_result", None)
    if result is None:
        return []
    streams_by_statement: dict[str, list[str]] = {}
    for entry in (*result.value_streams, *(result.gated_upside or ())):
        for warning in entry.warnings:
            if _D57_TOKEN not in warning:
                continue
            streams_by_statement.setdefault(warning, []).append(entry.stream.value)
    rows: list[AssumptionPageRow] = []
    for statement, streams in streams_by_statement.items():
        rows.append(
            AssumptionPageRow(
                kind="d57_assumed_approved",
                key=", ".join(sorted(set(streams))),
                value="assumed approved",
                unit="",
                label="ASSUMED APPROVED under D57, never granted (D45)",
                published_range="",
                source="the engine's own statement on this stream, quoted verbatim below",
                status="assumed",
                direction="up",
                direction_basis=(
                    "a stream that would otherwise be zeroed, reduced, deferred or held "
                    "conditional is priced in full"
                ),
                confirmation_ask=(
                    "the contract, consent, award, registration or threshold clearance this row "
                    "assumes, as held by the site"
                ),
                verbatim=statement,
            )
        )
    return rows


def _assumptions_page(
    quote: QuoteRecord,
    site: SiteModel | None,
    library_rows: tuple[AssumptionSourceRow, ...],
    basis: StudyBasisView,
) -> AssumptionsPageView:
    """The consolidated assumptions page (D56 limb 2), one block per class, none merged.

    Three sources, in this order of authority: the site record's structured
    `DerivedAssumption` list; the run's own disclosure warnings, for the gate-emitted records that
    are deliberately never written back onto the site; and the site's asserted commitments. A key
    the site record already covers is not re-read from a warning, because the structured record is
    the richer of the two, never because the warning is redundant.
    """
    rows_by_block: dict[str, list[AssumptionPageRow]] = {bid: [] for bid, _, _, _ in _BLOCK_ORDER}
    covered: set[str] = set()
    for record in () if site is None else site.assumptions_requiring_confirmation:
        block, row = _row_from_record(record)
        covered.add(record.key)
        rows_by_block[block].append(row)
    for warning in quote.warnings:
        parsed = _parse_assumption_warning(warning)
        if parsed is None:
            continue
        block, row = parsed
        if row.key in covered:
            continue
        covered.add(row.key)
        rows_by_block[block].append(row)
    rows_by_block["attestation"].extend(_attestation_rows(site))
    rows_by_block["d57_assumed_approved"].extend(_d57_rows(quote))

    attested = len(rows_by_block["attestation"])
    # `len` over a flattened list rather than a sum of two lens: every render-time computation in
    # this module is audited by `tests/test_provenance_claim.py`, which reads the AST, and a row
    # count on a disclosure page is not a figure anyone should have to look up to trust.
    optimistic = len([*rows_by_block["optimistic"], *rows_by_block["d57_assumed_approved"]])
    if basis.present:
        # Keyed on `present`, never on the note. A record can name its basis by ``study_basis_id``
        # and carry no prose note at all (`detect_study_basis`), and on such a record keying the
        # row off the note silently dropped the D56 basis statement from all four assumptions pages
        # while every banner above it stated the basis correctly. The record's own words are quoted
        # where they exist and the label carries the statement where they do not; nothing here
        # promises a verbatim reason that the record cannot supply.
        detail = (
            f"The record's own words: {basis.note}"
            if basis.note is not None
            else (
                f"The record names the basis as {basis.label}, recorded basis id "
                f"{basis.basis_id or 'not recorded'}, and carries no prose statement of it: the "
                "basis was read from the record's machine-readable basis id."
            )
        )
        basis_statement = (
            "STUDY BASIS: NOT AN UNDERWRITING OUTPUT. This run was priced on a named study basis, "
            f"so no figure in this pack may be presented as an underwriting result. {detail}"
        )
    elif attested or optimistic:
        basis_statement = (
            "NOT AN UNDERWRITING OUTPUT. No named study basis is stamped on this record, and the "
            "run does not stand on an unqualified evidence base either: it rests on "
            f"{attested} asserted commitment(s) and {optimistic} optimistic selection(s), both "
            "enumerated on this page. A pack on that footing is in-house feasibility work. It must "
            "not be presented as an underwriting output, and the confirmation asks below are what "
            "would turn it into one."
        )
    else:
        basis_statement = (
            "UNDERWRITING BASIS. No named study basis is stamped on this record, and no asserted "
            "commitment or optimistic selection is recorded against this run."
        )
    release = quote.assumption_version
    library_note = (
        (
            f"Release {release}: every entry the run's own value streams cite, with the status, "
            "confidence and source published in that release."
            if release
            else "No assumption release is stamped on this record, so these entries cannot be "
            "repriced against the library that produced them."
        )
        if library_rows
        else _LIBRARY_ABSENT
    )
    return AssumptionsPageView(
        basis_statement=basis_statement,
        site_supplied=site is not None,
        site_note=_SITE_SUPPLIED if site is not None else _SITE_NOT_SUPPLIED,
        blocks=tuple(
            AssumptionsBlock(
                title=title,
                lead=lead,
                rows=tuple(rows_by_block[bid]),
                empty_note=empty,
                source=f"Run record {quote.quote_id} · {_BLOCK_SOURCE[bid]}",
            )
            for bid, title, lead, empty in _BLOCK_ORDER
        ),
        library_rows=library_rows,
        library_note=library_note,
        footnote=_PAGE_FOOTNOTE,
    )


def _provenance(quote: QuoteRecord) -> ProvenanceView:
    return ProvenanceView(
        quote_id=quote.quote_id,
        quote_mode=quote.quote_mode.value,
        site_id=quote.site_id,
        data_tier=quote.data_tier.value,
        input_hash=quote.input_hash,
        assumption_version=quote.assumption_version,
        code_version=quote.code_version,
        schema_version=quote.schema_version,
        expiry_date=quote.expiry_date,
        created_at=quote.created_at,
    )


def build_view_model(
    quote: QuoteRecord,
    site: SiteModel | None = None,
    *,
    sensitivities: tuple[SensitivityRow, ...] = (),
    assumptions: object | None = None,
) -> OutputViewModel:
    """Extract the render-ready view model from a stamped `QuoteRecord` (+ optional `SiteModel`).

    Robust to a minimal record (``optimisation_result``/``financial_case`` may be ``None``, as in
    ``examples/quote_record_example.json``): missing pieces become *unavailable* views, never an
    exception and never an invented value. ``sensitivities`` are an optional caller override; when
    empty (the default), the Low/Central/High rows persisted on the record
    (``quote.scenario_sensitivities``) are used, so the rendered table traces to the record.
    """
    result = quote.optimisation_result
    cashflows = _cashflows(quote)
    dscr = _dscr(quote)

    # Bankable deliverable floor (System Brief acceptance test 1): P50/P90 and DSCR are mandatory;
    # point estimates are not deliverables. An artifact may never present as bankable without all
    # three — if the record claims bankable but a mandatory figure is absent, downgrade to an
    # honest not-bankable and say why (we never fabricate the missing figure to keep the badge).
    claimed_bankable = (
        result is not None and result.bankable_status == BankableStatus.BANKABLE_CANDIDATE
    )
    has_mandatory_deliverables = (
        cashflows.year0_p50 is not None
        and cashflows.year0_p90 is not None
        and dscr.min_dscr is not None
    )
    # A quote is valid only against the assumption version that produced it (X.1): a record
    # with no version stamp can never present as bankable — there is nothing to reprice
    # against, so the number is unverifiable.
    has_assumption_version = quote.assumption_version is not None
    bankable = claimed_bankable and has_mandatory_deliverables and has_assumption_version
    bankable_status = result.bankable_status.value if result is not None else "not_bankable"

    warnings = list(quote.warnings)
    if claimed_bankable and not has_mandatory_deliverables:
        warnings.append(
            "record is marked bankable but is missing mandatory P50/P90 and/or DSCR: presented "
            "as not bankable (point estimates are not deliverables)."
        )
    if claimed_bankable and not has_assumption_version:
        warnings.append(
            "record is marked bankable but carries no assumption version stamp: presented as "
            "not bankable (a quote is valid only against the assumption version that priced it)."
        )

    # The pricing basis: `QuoteRecord.study_basis_id` first, the record's own labelled warning as
    # the legacy fallback. `quote.warnings` is read rather than the locally-extended list because a
    # basis is a fact about the run, never a presentation downgrade. See `detect_study_basis` for
    # why both sources are consulted and why `study_basis_id is None` is not evidence of an
    # underwriting run.
    basis = detect_study_basis(quote)

    # Built once and used twice: the Evidence register and the dedicated assumptions page show the
    # same rows, so they can never disagree about what the run cited.
    assumption_sources = _assumption_sources(quote, assumptions)

    # Hoisted out of the constructor call because the per-stream register reads its numbered stream
    # notes: the two must cite the same numbers, so they must come from the same split.
    split = _split(quote)

    # Sensitivities: an explicit caller-supplied set overrides; otherwise use the scenario
    # sensitivities persisted on the record (so the artifact's figures trace to the record).
    effective_sensitivities = tuple(sensitivities) or tuple(
        SensitivityRow(
            label=s.label,
            min_dscr=s.min_dscr,
            irr_pct=s.irr_pct,
            cod_delay_months=s.cod_delay_months,
            note=s.note,
        )
        for s in quote.scenario_sensitivities
    )

    return OutputViewModel(
        bankable=bankable,
        bankable_status=bankable_status,
        quote_mode=quote.quote_mode.value,
        data_tier=quote.data_tier.value,
        site_supplied=site is not None,
        spec=_spec(quote),
        cashflows=cashflows,
        dscr=dscr,
        sensitivities=effective_sensitivities,
        split=split,
        price=_price(quote),
        debt=_debt(quote),
        returns=_returns(quote),
        counterparty=_counterparty(site),
        consents_gates=_consents_gates(site),
        bill_savings=_bill_savings(quote),
        provenance=_provenance(quote),
        assumption_sources=assumption_sources,
        # D56 rule 4: the dedicated assumptions page, built once here so all four artifacts render
        # the same rows. It reads the site's structured records, the run's own disclosure warnings
        # (the only carrier for the gate-emitted records) and the site's asserted commitments.
        assumptions_page=_assumptions_page(quote, site, assumption_sources, basis),
        # The locally-extended list: quote.warnings plus the presentation-layer downgrades
        # (missing P50/P90/DSCR deliverables; missing assumption-version stamp — F-010).
        warnings=tuple(warnings),
        study_basis=basis.present,
        study_basis_note=basis.note,
        study_basis_label=basis.label,
        study_basis_id=basis.basis_id,
        best_candidate=_best_candidate(quote),
        # D59: the per-stream register and the methodology view, built here for the same reason the
        # assumptions page is: one set of rows, four artifacts, no drift.
        stream_assumptions=_stream_assumptions(split, assumption_sources),
        methodology=_methodology(quote),
        lifecycle=quote.financial_case.lifecycle if quote.financial_case is not None else None,
    )


# Re-exported sentinel kept importable for callers that want the standing seam copy.
__all__ = [
    "BEST_CANDIDATE_CAVEAT",
    "BEST_CANDIDATE_HEADING",
    "BEST_CANDIDATE_HELD_OUT",
    "BEST_CANDIDATE_IN_FLOOR",
    "FIGURE_BASIS_DECIDED",
    "FIGURE_BASIS_GATED",
    "FIGURE_BASIS_UNMEASURED",
    "NO_CANDIDATE_COST_REASON",
    "NO_FINANCIAL_CASE_REASON",
    "BestCandidateStreamRow",
    "BestCandidateView",
    "StreamRow",
    "StreamNote",
    "StreamAssumptionRow",
    "MethodologyView",
    "ContractedMerchantSplit",
    "SystemSpecView",
    "CashflowView",
    "DscrView",
    "SensitivityRow",
    "PriceView",
    "DebtView",
    "ReturnsView",
    "CounterpartyView",
    "ConsentsGatesView",
    "BillSavingsView",
    "ProvenanceView",
    "OutputViewModel",
    "build_view_model",
]
