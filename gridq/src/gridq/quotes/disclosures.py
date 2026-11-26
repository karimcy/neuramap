"""Disclosures a record owes its reader — conflicts, blocking asks, unevidenced claims.

A run record can hold two accounts of the same fact, or a blocking ask, or the ingredients for a
verdict sentence it cannot actually evidence. When a pack shows one of those and buries the other,
the reader is misled by omission rather than by a wrong number, which is harder to catch and worse.

This module turns those conditions into `Disclosure` objects that `gridq.quotes.assembly` writes
verbatim into ``QuoteRecord.warnings`` (reproduced unfiltered in the pack) and that
`gridq.quotes.confidence` reads as a ceiling on the band (so the serious ones also land in the
strip printed beside the verdict). Nothing is chosen between and nothing is deleted from the record:
a conflict is stated **as a conflict**, with the evidence for which account the pricing path used.

## The conditions, and the reviews that found them

**Record conflict (Colnbrook).** ``site.connection.anm_or_curtailment_notes`` asserted "CONSEQUENCE:
import_headroom_kw = 0, so sizing is capacity-blocked — a DATA-BLOCKED result, not an economic one",
while the same record's ``sizing_grid.selection_reason`` gave an economic account. The pack led with
the economic verdict and reproduced the capacity note only inside a chart caption a third of the way
down. The note is **stale**, not wrong-headed: ``import_headroom_kw`` is read nowhere on the pricing
path, and measured charge headroom at these sites is ample
(`docs/grundon_harwell/07_sizing_diagnosis.md` §§1-2). The resolution is therefore to surface both
accounts, say they conflict, and record which is superseded and on what evidence — never to quietly
drop the inconvenient field.

**Sizing verdict never surfaced (Colnbrook).** ``sizing_grid.selection_reason`` appeared nowhere in
the pack — the report derives its verdict prose from ``optimisation_result.warnings`` instead, so
the sizing search's own recorded reason was simply absent. It is now stated verbatim.

**Blocking ask silently dropped, and two ask lists shown as one (Tanhouse).** ``site.missing_data``
held eight hand-authored asks, one of them marked ``BLOCKING`` (the 13.13958 p/kWh non-commodity
residual — the largest avoidable-cost line at the site); the record's warnings carried only the
*count*, so the blocking ask never reached the reader at all. Separately, the pack's ranked-ask
exhibit shows seven **recomputed** generic asks — a different list with different provenance,
presented as though it were the same one.

**Unevidenced return comparison (Tanhouse).** A no-viable verdict spoke of candidates' returns while
every persisted candidate recorded ``project_irr_pct = null`` and ``levered_return_pct = null``. No
return was computed for any of them, so "no candidate produced a positive return" is not a
comparison the record can support. The measured cause is recorded in §7 of the diagnosis: every
annual cashflow is negative, so there is no IRR to report. The claim is not corrected here (this
module authors no verdict) — it is disclosed as unevidenced, and what the record *does* evidence is
stated beside it.

**"This site earns nothing" (all eight campaign sites, 2026-08-17).** A no-spec record's streams all
read ``0.00`` because it reports no candidate, and the persisted sizing grid carried no revenue
field of any kind — so the only reading a pack offered was that the site earned nothing, on runs
whose own stream note recorded the dispatch DISCHARGING 30,133.0 kWh/yr. The grid now persists each
candidate's priced floor, its recorded cost stack and its per-stream breakdown (contract ``0.2.0``),
and `candidate_revenue_disclosure` states the best confirmed candidate's measured figures. Both
readings are negatives where the site does not clear its costs; only the measured one is evidence.

## Rules this module keeps

- **No number is invented.** Every count comes from the record being described; the measured
  headroom and cashflow evidence is *cited* to the diagnosis document rather than copied into code,
  because a site-specific constant hard-coded into a generic detector would be exactly the kind of
  unsourced figure this work exists to remove.
- **Detection is on structured fields, never on prose.** Free text is quoted, not parsed, so a
  reworded note cannot switch a disclosure off. The one exception is the ``BLOCKING`` marker, which
  *is* the record's structured convention for that field.
- **Narrow firing.** A disclosure that fired on healthy runs would be noise, and noise is how a real
  disclosure gets skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Where the capacity-vs-economic question was actually measured. Cited, never paraphrased into
#: numbers here: §§1-2 hold the per-site measured charge headroom, §7 the cashflow cause of the
#: absent IRR, §9 the probe showing tripled import capacity still yields no viable size.
DIAGNOSIS_REF = "docs/grundon_harwell/07_sizing_diagnosis.md"

#: The marker `site.missing_data` uses for an ask that must be answered before the figures it bears
#: on are underwriting evidence. It is the field's own convention, so matching it is reading
#: structure, not guessing at prose.
BLOCKING_MARKER = "BLOCKING"


@dataclass(frozen=True)
class Disclosure:
    """One thing the record owes its reader, and how loudly.

    ``caps_confidence`` marks the disclosures that are *evidence* problems rather than commentary:
    they cap the confidence band, which is what carries them into the strip printed beside the
    verdict instead of leaving them in the warnings further down.
    """

    code: str
    text: str
    caps_confidence: bool = False


def _full_series_points(sizing_grid: Any) -> list[Any]:
    """The candidates priced on the full series — the only ones that are evidence.

    Proxy points are typical-period scoring solves that guide the search and were never reported as
    a priced result, so a claim about "the candidates" must not be counted against them.
    """
    points = list(getattr(sizing_grid, "points", ()) or ())
    return [p for p in points if not getattr(p, "proxy", False)]


def _selected_a_system(result: Any) -> bool:
    """Whether the run put a system on the record. Absent/zero power means it did not."""
    return bool(getattr(result, "selected_power_kw", None))


def sizing_verdict_disclosure(result: Any, sizing_grid: Any) -> Disclosure | None:
    """State ``sizing_grid.selection_reason`` verbatim when no system was specified.

    Scoped to no-spec runs on purpose: on a run that selected a system the reason is a note about a
    choice the pack already presents in full, and repeating it in the warnings would be noise. On a
    no-spec run it is the sizing search's own account of why the reader is holding a pack with no
    specification in it, and it was previously nowhere in the document.
    """
    if sizing_grid is None or _selected_a_system(result):
        return None
    reason = getattr(sizing_grid, "selection_reason", None)
    if not reason:
        return None
    basis = getattr(sizing_grid, "selection_basis", None) or "not recorded"
    size = getattr(sizing_grid, "search_space_size", None)
    evaluated = "an unrecorded number of" if size is None else f"{size}"
    return Disclosure(
        code="SIZING_VERDICT",
        text=(
            "SIZING VERDICT — no system was specified, and this is the sizing search's own "
            f'recorded reason, verbatim from sizing_grid.selection_reason: "{reason}" '
            f"(sizing_grid.selection_basis: {basis}; {evaluated} candidate(s) in the evaluated "
            "space). It is stated here because it is the account the pricing path acted on, and "
            "because a reader is entitled to the record's own words for it rather than a summary."
        ),
    )


def capacity_conflict_disclosure(site: Any, result: Any, sizing_grid: Any) -> Disclosure | None:
    """Disclose a site note claiming a capacity block that the run's own record refutes.

    Detection is structural — ``connection.import_headroom_kw == 0`` plus candidates the record
    shows were dispatch-solved and priced — so it cannot be turned off by rewording the note. The
    note itself is quoted in full and never edited: it stays on the record, marked superseded, with
    the reason it is superseded and where that was measured.
    """
    connection = getattr(site, "connection", None)
    if connection is None or getattr(connection, "import_headroom_kw", None) != 0:
        return None
    priced = _full_series_points(sizing_grid)
    if not priced:
        # Nothing in the record contradicts the note, so there is no conflict to disclose. Saying
        # "superseded" here would be an assertion about a search that never happened.
        return None
    note = getattr(connection, "anm_or_curtailment_notes", None)
    reason = getattr(sizing_grid, "selection_reason", None)
    if not note or not reason:
        return None
    priceable = sum(1 for p in priced if getattr(p, "priceable", False))
    return Disclosure(
        code="RECORD_CONFLICT",
        caps_confidence=True,
        text=(
            "RECORD CONFLICT — this record carries two incompatible accounts of why no "
            "specification was produced. Both are stated here in full; neither is chosen for the "
            "reader. "
            "(1) The SITE record, site.connection.anm_or_curtailment_notes, verbatim: "
            f'"{note}" '
            "(2) The RUN's own sizing verdict, sizing_grid.selection_reason, verbatim: "
            f'"{reason}" '
            "Account (1) says the cause is a data/capacity block; account (2) says it is economic. "
            "The run's own figures refute (1): "
            f"{len(priced)} candidate(s) were dispatch-solved and priced on the full series "
            f"({priceable} recorded priceable), which a capacity-blocked search cannot do; and "
            "connection.import_headroom_kw is not read anywhere on the pricing path — in this "
            "codebase it is referenced only by an ingestion range check "
            "(gridq/ingestion/validators.py) and by the chart caption that reproduces this very "
            "note (gridq/outputs/report_charts.py), so setting it to 0.0 constrains nothing that "
            f"was priced. Measured charge headroom at these sites is recorded in {DIAGNOSIS_REF} "
            "(§§1-2), and §9 records that tripling the import capacity still yields no viable "
            "size. Account (1) is therefore SUPERSEDED. It is left on the record rather than "
            "deleted, because deleting a field that disagrees with the result is how a record "
            "stops being evidence."
        ),
    )


def blocking_ask_disclosures(site: Any) -> list[Disclosure]:
    """Reproduce every ``BLOCKING`` entry of ``site.missing_data`` verbatim, one per disclosure.

    Previously the record carried only "N ranked data ask(s) outstanding", so a blocking ask — the
    kind that decides whether a figure is evidence at all — was compressed into a digit and never
    reached the reader. A count can be skimmed past; the sentence cannot.
    """
    asks = list(getattr(site, "missing_data", ()) or ())
    out: list[Disclosure] = []
    for index, ask in enumerate(asks):
        if BLOCKING_MARKER not in str(ask):
            continue
        out.append(
            Disclosure(
                code="BLOCKING_ASK",
                caps_confidence=True,
                text=(
                    f"BLOCKING DATA ASK — recorded on this site as blocking, verbatim from "
                    f'site.missing_data[{index}]: "{ask}" '
                    "It is stated in full rather than counted: an ask the site record marks "
                    "BLOCKING is the condition on which the figures it bears upon are evidence at "
                    "all, so it cannot be represented by a number in a tally."
                ),
            )
        )
    return out


def ask_list_disclosure(site: Any, recomputed_count: int | None) -> Disclosure | None:
    """Say that the authored and recomputed ask lists are two lists, when they differ.

    The pack's ranked-ask exhibit is recomputed from the site's own gaps
    (`gridq.ingestion.build_ranked_data_asks`); ``site.missing_data`` is hand-authored onto the
    record. They have different provenance, different wording and — at Tanhouse — different lengths
    (8 authored vs 7 recomputed), and were presented as if one list. Neither supersedes the other,
    so they are separated rather than reconciled into a single count that would be true of neither.
    """
    authored = list(getattr(site, "missing_data", ()) or ())
    if not authored or recomputed_count is None or recomputed_count == len(authored):
        return None
    return Disclosure(
        code="ASK_LISTS_DIFFER",
        text=(
            "TWO DATA-ASK LISTS, and they are not the same list. site.missing_data holds "
            f"{len(authored)} ask(s) hand-authored onto the site record, reproduced in these "
            f"warnings; gridq.ingestion.build_ranked_data_asks recomputes {recomputed_count} "
            "generic ask(s) from the record's own gaps, and that recomputed list is what the "
            "pack's ranked-ask exhibit shows. Different provenance, different wording, different "
            "length — neither supersedes the other, so they are stated separately. A single "
            "combined count would be true of neither list."
        ),
    )


def unevidenced_return_disclosure(result: Any, sizing_grid: Any) -> Disclosure | None:
    """Disclose a no-viable verdict whose return comparison no persisted figure supports.

    The check is structural, not a parse of the verdict prose: if the run specified no system and
    **every** full-series candidate on the persisted grid records a null project IRR and a null
    levered return, then the record holds no return figure at all, and any sentence comparing
    candidate returns to zero is unevidenced whatever words it uses.

    This module never edits the verdict. It states what the record can and cannot support, so a
    reader can see the difference between "no candidate earned a positive return" (a comparison) and
    "no candidate produced a computable return" (what happened).
    """
    if sizing_grid is None or _selected_a_system(result):
        return None
    priced = _full_series_points(sizing_grid)
    if not priced:
        return None
    has_return = any(
        getattr(p, "project_irr_pct", None) is not None
        or getattr(p, "levered_return_pct", None) is not None
        for p in priced
    )
    if has_return:
        return None
    priceable = sum(1 for p in priced if getattr(p, "priceable", False))
    return Disclosure(
        code="UNEVIDENCED_RETURN",
        caps_confidence=True,
        text=(
            "UNEVIDENCED RETURN COMPARISON — no system was specified, and the persisted sizing "
            f"grid carries no return figure for any candidate: all {len(priced)} full-series "
            "candidate(s) record project_irr_pct = null and levered_return_pct = null. No return "
            "was computed for any of them, so any statement in this pack that no candidate "
            "produced a POSITIVE return is not a comparison this record evidences — nothing was "
            "compared against zero. What the record does evidence: "
            f"{len(priced)} candidate(s) dispatch-solved and priced ({priceable} recorded "
            "priceable), and none selected, i.e. no candidate produced a COMPUTABLE return. The "
            f"measured cause is recorded in {DIAGNOSIS_REF} (§7): every annual cashflow is "
            "negative, so there is no IRR to report. That is a stronger negative result than a "
            "failed return threshold, and it should be read as one."
        ),
    )


def candidate_revenue_disclosure(result: Any, sizing_grid: Any) -> Disclosure | None:
    """State what the best evaluated candidate MEASURED on a run that specified no system.

    A no-spec pack reports no candidate, so every stream on its top-level record reads ``0.00``. Up
    to grid contract ``0.2.0`` the persisted grid could not correct that impression, because it held
    every ratio the objective ranks on and **no revenue field at all**: the only reading available
    to a reader was "this site earns nothing". On the 2026-08-15 campaign that reading was false —
    the record's own stream note said the dispatch had DISCHARGED 30,133.0 kWh/yr on the same run —
    and `optimisation.valuation` was already telling readers "the per-candidate figures are in the
    sizing grid" about figures the grid did not carry.

    This states the measured ones. ``the best candidate earns £X/yr against £Y of recorded cost`` is
    a result; ``this site earns nothing`` is an inference from an absent field. Both are negatives
    when the site does not clear its costs; only the first is evidenced, and only the first can be
    checked.

    Rules it keeps:

    - **Non-proxy points only** (`_full_series_points`). A proxy point is a typical-period scoring
      solve scaled to a year, and it reads HIGH: on the demo site the best proxy floor is £66,651.91
      against £61,889.20 confirmed on the full series. Quoting the proxy would overstate the site by
      8% in the one sentence written to stop a site being understated.
    - **Nothing is computed.** The £ are copied from the point; the only arithmetic is the
      revenue-vs-cost comparison, and it is stated as the subtraction it is so a reader can redo it.
    - **Silence is never a zero.** A point whose ``annual_revenue_gbp`` is ``None`` never had a
      cashflow model built, so it is excluded from the "best" search rather than ranked as £0. If no
      point carries a figure the disclosure does not fire — there is nothing measured to state, and
      saying so is `unevidenced_return_disclosure`'s job, not this one's.
    - It does **not** cap the band. It is evidence the record now carries, not a weakness in it.
    """
    if sizing_grid is None or _selected_a_system(result):
        return None
    measured = [
        p
        for p in _full_series_points(sizing_grid)
        if getattr(p, "annual_revenue_gbp", None) is not None
    ]
    if not measured:
        return None
    best = max(measured, key=lambda p: (p.annual_revenue_gbp, -p.power_kw, -p.duration_hours))
    floor = best.annual_revenue_gbp
    opex = getattr(best, "annual_operating_cost_gbp", None)
    dispatch_cost = getattr(best, "annual_dispatch_cost_gbp", None)
    return Disclosure(
        code="CANDIDATE_REVENUE",
        text=(
            "WHAT THE BEST EVALUATED CANDIDATE ACTUALLY EARNED — no system was specified, so every "
            "value-stream line on this record reads 0.00 because it reports NO CANDIDATE, not "
            "because the dispatch earned nothing. The persisted sizing grid carries the "
            f"per-candidate figures. Measured on the full series, the best of {len(measured)} "
            f"confirmed candidate(s) is {best.power_kw:g} kW x {best.duration_hours:g} h, with a "
            f"PRICED-FLOOR revenue of £{floor:,.2f}/yr"
            + _cost_clause(floor, opex, dispatch_cost)
            + ". "
            + _floor_breakdown(best)
            + " This site therefore does not earn nothing; read the verdict as an economic result "
            "about the best size the search could find, which is a stronger and more checkable "
            "statement than an absence."
        ),
    )


def _cost_clause(floor: float, opex: float | None, dispatch_cost: float | None) -> str:
    """The recorded cost stack beside the floor, and the subtraction, or an honest silence.

    Both cost lines are reported because EBITDA nets both, and separately because `CashflowModel`
    keeps them separate. Where a line was not recorded it is named as unrecorded rather than treated
    as zero, and the comparison is then withheld — a "covers its costs" claim resting on a cost line
    that does not exist would be the same null-reads-as-zero defect in a new place. Neither figure
    includes capex (year-1 operating basis), which the sentence says so a reader cannot mistake the
    comparison for a return.
    """
    if opex is None or dispatch_cost is None:
        missing = ", ".join(
            name
            for name, value in (("operating cost", opex), ("dispatch cost", dispatch_cost))
            if value is None
        )
        return (
            f" (its {missing} was not recorded on the grid, so no revenue-vs-cost comparison is "
            "made here — an unrecorded cost line is not a cost of zero)"
        )
    total = opex + dispatch_cost
    verdict = "COVERS" if floor > total else "does NOT cover"
    return (
        f" against £{total:,.2f}/yr of recorded year-1 cost (£{opex:,.2f} operating + "
        f"£{dispatch_cost:,.2f} dispatch), which it {verdict}: "
        f"£{floor:,.2f} - £{total:,.2f} = £{floor - total:,.2f}/yr before capex, debt service and "
        "tax, none of which is in this comparison"
    )


def _floor_breakdown(point: Any) -> str:
    """The candidate's per-stream £, with the floor and the held-out streams stated apart.

    The split is read off the record's own ``in_priced_floor`` flag (stamped from
    `schemas.market.is_priced_floor`, the predicate the finance model filters the floor on), never
    from the £: a floor stream at ``0.00`` was priced and came out at nothing, while a held-out
    stream at ``0.00`` was never in the floor to begin with, and those are two different facts that
    a bare zero collapses into one (D45). Held-out streams are named with their gate state and their
    £ is deliberately NOT added to anything.
    """
    streams = list(getattr(point, "revenue_streams", ()) or ())
    if not streams:
        return (
            "Its dispatch recorded no value streams at all on the grid, so there is no per-stream "
            "breakdown to state — which is silence, not a set of zeros."
        )
    floor = [s for s in streams if s.in_priced_floor]
    held = [s for s in streams if not s.in_priced_floor]
    parts = [
        "Priced floor, per stream: "
        + "; ".join(f"{s.stream} £{s.annual_revenue_gbp:,.2f}" for s in floor)
        + " (a floor stream at £0.00 was priced and the optimiser declined it — a measured result, "
        "not a missing input)."
        if floor
        else "No stream on this candidate is in the priced floor."
    ]
    if held:
        parts.append(
            "Held OUT of the floor and excluded from every figure above: "
            + "; ".join(
                f"{s.stream} (£{s.annual_revenue_gbp:,.2f}, {s.firmness}"
                + (f", gate {s.gate_status}" if s.gate_status else "")
                + ")"
                for s in held
            )
            + "."
        )
    return " ".join(parts)


def record_disclosures(
    site: Any,
    result: Any,
    sizing_grid: Any = None,
    *,
    recomputed_ask_count: int | None = None,
) -> list[Disclosure]:
    """Every disclosure this record owes its reader, worst first.

    Ordered so the evidence problems precede the commentary: a conflict between two accounts of the
    verdict, then a verdict claim the record cannot support, then blocking asks, then the sizing
    verdict itself, then the ask-list separation. Duplicate texts are impossible (each detector
    fires at most once, except blocking asks, which are per-entry).
    """
    out: list[Disclosure] = []
    conflict = capacity_conflict_disclosure(site, result, sizing_grid)
    if conflict is not None:
        out.append(conflict)
    unevidenced = unevidenced_return_disclosure(result, sizing_grid)
    if unevidenced is not None:
        out.append(unevidenced)
    # Immediately after the unevidenced-return disclosure, because it is the record's own answer to
    # the question that one raises: having said which comparison the record cannot support, this
    # says which figures it CAN. Placing it later would leave the pack's strongest reading of a
    # no-spec run ("this site earns nothing") standing unanswered above it.
    candidate_revenue = candidate_revenue_disclosure(result, sizing_grid)
    if candidate_revenue is not None:
        out.append(candidate_revenue)
    out.extend(blocking_ask_disclosures(site))
    verdict = sizing_verdict_disclosure(result, sizing_grid)
    if verdict is not None:
        out.append(verdict)
    ask_lists = ask_list_disclosure(site, recomputed_ask_count)
    if ask_lists is not None:
        out.append(ask_lists)
    return out


def confidence_capping_disclosures(disclosures: list[Disclosure]) -> list[Disclosure]:
    """The subset that caps the confidence band (and so reaches the strip beside the verdict)."""
    return [d for d in disclosures if d.caps_confidence]


__all__ = [
    "BLOCKING_MARKER",
    "DIAGNOSIS_REF",
    "Disclosure",
    "ask_list_disclosure",
    "blocking_ask_disclosures",
    "candidate_revenue_disclosure",
    "capacity_conflict_disclosure",
    "confidence_capping_disclosures",
    "record_disclosures",
    "sizing_verdict_disclosure",
    "unevidenced_return_disclosure",
]
