"""Render the capital-facing underwriting pack (M8, issue 011) as Markdown.

Assembles the memo entirely from the `OutputViewModel` (which was extracted from the run record):
P50/P90 cashflows, DSCR + covenant + sensitivities, the contracted-vs-merchant split, the price
to target, counterparty credit, consents and gate status, the provenance spine and the warnings.

This renderer **authors no number**: it only places values already on the view model and turns
them into strings via `formatting`. Where a figure is absent it prints an explicit *unavailable*
label naming why — it never substitutes a 0 or a guess. Prose comes from the injected
`ProseDrafter` (number-free). The result is deterministic given the record.
"""

from __future__ import annotations

from gridq.outputs import formatting as fmt
from gridq.outputs.prose import ProseDrafter, default_prose_drafter
from gridq.outputs.viewmodel import (
    BEST_CANDIDATE_CAVEAT,
    BEST_CANDIDATE_HEADING,
    BEST_CANDIDATE_HELD_OUT,
    BEST_CANDIDATE_IN_FLOOR,
    NO_CANDIDATE_COST_REASON,
    NO_FINANCIAL_CASE_REASON,
    BestCandidateView,
    ContractedMerchantSplit,
    CounterpartyView,
    DscrView,
    OutputViewModel,
    StreamNote,
    StreamRow,
)
from gridq.schemas import SizingGrid


def render_underwriting_memo(
    vm: OutputViewModel,
    *,
    drafter: ProseDrafter = default_prose_drafter,
    sizing_grid: SizingGrid | None = None,
) -> str:
    """Render the underwriting pack as a Markdown document from the view model.

    ``sizing_grid`` is the one persisted engine projection the view model does not expose, and it is
    what the full sizing surface is drawn from (D59). Without it the surface section states its own
    absence rather than being omitted: a lender must be able to tell "the search is not on this
    record" from "this pack does not show it".
    """
    parts: list[str] = []
    parts.append("# GridQ: Underwriting pack")
    parts.append(_banner(vm, drafter))
    parts.append(
        drafter.memo_summary(
            bankable=vm.bankable,
            quote_mode=vm.quote_mode,
            data_tier=vm.data_tier,
            has_warnings=bool(vm.warnings),
        )
    )
    # THE OWNER'S ORDER (D59, Olly, 2026-08-18, verbatim): "starting with the IRR and Revenue
    # breakdown and graphs, then the methodology and then the assumptions register for each site".
    # The banner and the summary stay above it because they are safety labels, not evidence: a
    # reader
    # must not meet a study-basis IRR before being told which basis produced it.
    #
    # 1. The IRR and what returns it.
    parts.append(_price_and_returns(vm))
    parts.append(_cashflows(vm))
    parts.append(_dscr(vm.dscr, vm.sensitivities))
    # 2. Revenue per stream, then the basis of each stream's own figure.
    parts.append(_split(vm.split, drafter, vm.best_candidate))
    parts.append(fmt.stream_assumptions_markdown(vm.stream_assumptions))
    # 3. The system that earns it, and the whole space it was chosen out of. This is the memo's half
    # of the owner's "clearly shows the full sizing surface": a lender gets every evaluated
    # candidate
    # as a row, with the selection basis and the record's own selection reason verbatim beneath.
    parts.append(_system_spec(vm))
    # Immediately after the specification section, so the verdict and the measured candidate figures
    # behind it are read together. Empty string on a run that selected a system (dropped by the
    # `if p` filter below).
    parts.append(_best_candidate(vm))
    parts.append(fmt.sizing_surface_markdown(sizing_grid))
    # 4. Then the methodology.
    parts.append(fmt.methodology_markdown(vm.methodology))
    # 5. Then the assumptions register: D56 rule 4's dedicated page, on a page of its own,
    # enumerating every assumption the run rests on with its direction of effect and its exact
    # confirmation ask. Rendered from `vm.assumptions_page`, the same rows the other three artifacts
    # print, so no two packs can disclose a different set.
    parts.append(fmt.assumptions_page_markdown(vm.assumptions_page))
    # 6. The evidence sections. They moved DOWN, not out.
    parts.append(_debt(vm))
    parts.append(_counterparty(vm.counterparty))
    parts.append(_consents_gates(vm))
    parts.append(_provenance(vm))
    parts.append(_assumption_sources_section(vm))
    # WP-UW-17 (D131): every lifecycle flow as a row, modelled or excluded, before the
    # warnings that state each in prose.
    parts.append(_lifecycle_ledger(vm))
    parts.append(_warnings(vm))
    # Scrubbed of em and en dashes at the document seam, like the other three artifacts.
    #
    # **This reverses a prior decision, deliberately, and the reason is on the record.** The memo
    # used
    # to be exempt so that `quote.warnings` appeared byte-for-byte as the engine wrote it. The owner
    # has now instructed three times that no report may contain an em dash, and that instruction
    # outranks a typographic exemption: a lender reading this pack is not served by a dash the owner
    # has banned. `no_dashes` rewrites nothing but the two dash characters, the RECORD itself is
    # untouched, and every warning still appears in full and in the engine's own words. The
    # verbatim-warning test now compares the scrubbed form, which is the same guarantee held to the
    # same rule as the consulting pack.
    return fmt.no_dashes("\n\n".join(p for p in parts if p) + "\n")


# --- sections --------------------------------------------------------------


#: The basis banner's own words, in the pack's voice. A study-basis run prices normally-gated
#: revenue into the priced floor, which the underwriting basis forbids, so a pack titled
#: "Underwriting pack" must say up front that it is not one.
#:
#: **It names no basis and no stream, deliberately.** It used to hard-code "the D46
#: feasibility-study basis. Wholesale export is priced INTO the priced floor" — which on a D55
#: all-streams run would have understated the basis by five streams (BM, DC/DM/DR, CM, DFS and the
#: aggregator route) while sounding authoritative. Which streams are in the floor is the *record's*
#: statement to make, so the label comes from `OutputViewModel.study_basis_label` and the full
#: reason is reproduced verbatim from the record's own warning below.
_STUDY_BASIS_BANNER_BODY = (
    "This run was priced on a named feasibility-study basis: revenue the underwriting basis "
    "treats as gated or conditional is priced INTO the priced floor and therefore into P50, P90, "
    "the DSCR and both IRRs. No figure in this pack may be presented as an underwriting result."
)
#: Appended only when the record actually carries the basis's own prose statement. A record that
#: names its basis by ``study_basis_id`` alone carries no such warning, and pointing a lender at a
#: verbatim reason that is not in the document is the same class of false claim as dropping the
#: banner: precise, confident and wrong. `OutputViewModel.study_basis_note` is the field that says
#: which case holds.
_STUDY_BASIS_VERBATIM_POINTER = (
    " The record's own statement of the basis, naming every stream it prices and every award it "
    "assumes, is reproduced verbatim in the warnings."
)
#: Said instead, when the record names its basis but states no reason. It names the recorded basis
#: id inline rather than pointing at a section, because there is nowhere in the pack to point: the
#: whole content of the record's statement is the id itself.
_STUDY_BASIS_NO_VERBATIM = (
    " The record names the basis by its recorded basis id `{basis_id}` and carries no prose "
    "statement of it, so none is reproduced below."
)


def _study_basis_banner(vm: OutputViewModel) -> str:
    """The banner for whichever study basis priced this run, in the basis's own words.

    The label is scrubbed of em dashes at the point of use (`formatting.no_dashes`), the same
    arrangement as `formatting.assumptions_page_markdown`: every `StudyBasis.LABEL` joins its
    phrases with em dashes, this document is deliberately not scrubbed whole (the warnings section
    must stay byte-identical to the record), and house style admits no em dash in a rendered
    deliverable. Scrubbing here and not at document level satisfies both.
    """
    label = fmt.no_dashes(vm.study_basis_label or "")
    if vm.study_basis_note is not None:
        pointer = _STUDY_BASIS_VERBATIM_POINTER
    else:
        pointer = _STUDY_BASIS_NO_VERBATIM.format(basis_id=vm.study_basis_id or "not recorded")
    return f"> **{label}:** {_STUDY_BASIS_BANNER_BODY}{pointer}"


def _banner(vm: OutputViewModel, drafter: ProseDrafter) -> str:
    status = fmt.titleise(vm.bankable_status)
    banner = f"> **{drafter.banner_line(bankable=vm.bankable)}**  \n> Status on record: `{status}`"
    if vm.study_basis:
        return f"{_study_basis_banner(vm)}\n\n{banner}"
    return banner


def _system_spec(vm: OutputViewModel) -> str:
    s = vm.spec
    if s.selection_status == "no_viable_configuration":
        reason = s.selection_verdict_reason or (
            "the run record states no reason for the verdict beyond the status itself."
        )
        # THAT nothing was selected is a recorded outcome; WHY is a claim. This block used to assert
        # "none produces a positive return on the priced value floor" on records where no return was
        # computed for ANY candidate (every project_irr_pct null), which is a comparison the record
        # cannot support. The outcome is stated; the reason is the engine's, attributed.
        return (
            "## System specification\n\n"
            "> **Verdict: NO SYSTEM WAS SELECTED.** The sizing search ran over the candidate space "
            "(dispatch solved and priced at each) and selected none. That is the recorded outcome; "
            "the engine's own statement of the reason follows, reproduced rather than restated, "
            "and the evaluated surface is preserved on the record as evidence.\n>\n"
            f"> Engine statement: {reason}"
        )
    rows = [
        (
            "Power",
            fmt.kw(s.selected_power_kw)
            if s.selected_power_kw is not None
            else fmt.unavailable("no sizing on the result"),
        ),
        (
            "Energy",
            fmt.kwh(s.selected_energy_kwh)
            if s.selected_energy_kwh is not None
            else fmt.unavailable("no sizing on the result"),
        ),
        (
            "Duration",
            fmt.hours(s.duration_hours)
            if s.duration_hours is not None
            else fmt.unavailable("no sizing on the result"),
        ),
        (
            "Engine",
            fmt.titleise(s.implementation_level)
            if s.implementation_level
            else fmt.unavailable("no optimisation result"),
        ),
        (
            "Solver status",
            fmt.titleise(s.solver_status)
            if s.solver_status
            else fmt.unavailable("no optimisation result"),
        ),
    ]
    return "## System specification\n\n" + _kv_table(rows)


def _best_candidate(vm: OutputViewModel) -> str:
    """What the best size the search evaluated MEASURED, on a run that specified no system.

    See `viewmodel.BestCandidateView`. On a no-spec pack every stream on the top-level record reads
    0.00, which is true of the record and false as an impression: the persisted sizing grid holds
    the per-candidate figures, and on the 2026-08-15 campaign they were non-zero on a dispatch that
    discharged. This section states them under a heading that says in the same breath that they
    recommend nothing.

    Power and duration are printed and the energy is not: this document cannot raise the precision
    until all three multiply (`report_charts.spec_triple` is not importable here without dragging
    the whole chart module into the memo), and a triple a reader's calculator rejects is how the
    evaluated-points table shipped nine wrong rows. Two of the three cannot disagree.
    """
    bc = vm.best_candidate
    if bc is None:
        return ""
    absent = fmt.unavailable(NO_CANDIDATE_COST_REASON)
    rows = [
        ("Candidate power", fmt.kw(bc.power_kw)),
        ("Candidate duration", fmt.hours(bc.duration_hours)),
        ("Priced-floor revenue measured", fmt.gbp(bc.annual_revenue_gbp)),
        (
            "Recorded operating cost",
            absent
            if bc.annual_operating_cost_gbp is None
            else fmt.gbp(bc.annual_operating_cost_gbp),
        ),
        (
            "Recorded dispatch cost",
            absent if bc.annual_dispatch_cost_gbp is None else fmt.gbp(bc.annual_dispatch_cost_gbp),
        ),
        (
            "Revenue less recorded cost",
            absent if bc.net_annual_gbp is None else fmt.gbp(bc.net_annual_gbp),
        ),
        ("Confirmed candidates measured", str(bc.confirmed_candidate_count)),
    ]
    parts = [
        f"## {BEST_CANDIDATE_HEADING}",
        f"> **{bc.headline(fmt.gbp)}**",
        BEST_CANDIDATE_CAVEAT,
        _kv_table(rows),
    ]
    stream_rows = [*bc.floor_streams, *bc.held_out_streams]
    if stream_rows:
        lines = [
            "| Stream | Annual revenue | Counted in | Firmness | Gate |",
            "|:--|---:|:--|:--|:--|",
        ]
        for r in stream_rows:
            counted = BEST_CANDIDATE_IN_FLOOR if r.in_priced_floor else BEST_CANDIDATE_HELD_OUT
            lines.append(
                f"| {fmt.stream_label(r.stream)} | {fmt.gbp(r.annual_revenue_gbp)} | {counted} | "
                f"{fmt.titleise(r.firmness)} | {r.gate_status or 'n/a'} |"
            )
        parts.append("\n".join(lines))
    else:
        parts.append(
            "_This candidate reported no value streams on the grid, which is silence rather than a "
            "set of zeros._"
        )
    return "\n\n".join(parts)


def _cashflows(vm: OutputViewModel) -> str:
    cf = vm.cashflows
    if not cf.available:
        if not vm.returns.case_available:
            # No `financial_case` at all: nothing was evaluated, so this is an absence and not a
            # case that was run and came out short. The two must not read the same to a lender.
            return "## P50 / P90 cashflows\n\n" + fmt.unavailable(NO_FINANCIAL_CASE_REASON)
        return "## P50 / P90 cashflows\n\n" + fmt.unavailable(
            "no financial case on this quote: no positive sizing to price, or no finance run"
        )
    header = "| Year | P50 free cashflow | P90 free cashflow |\n|---:|---:|---:|"
    lines = [header]
    for year, (p50, p90) in enumerate(zip(cf.p50, cf.p90, strict=False)):
        label = "0 (capex)" if year == 0 else str(year)
        lines.append(f"| {label} | {fmt.gbp(p50)} | {fmt.gbp(p90)} |")
    note = (
        "\n\n_P90 is the bankable basis (the P50→P90 revenue haircut is the headline downside "
        "sensitivity). Year 0 is the installed capex; later years are free cashflow before debt._"
    )
    return "## P50 / P90 cashflows\n\n" + "\n".join(lines) + note


def _dscr(d: DscrView, sensitivities: tuple) -> str:  # type: ignore[type-arg]
    parts = ["## DSCR & covenant"]
    if not d.available:
        parts.append(fmt.unavailable(d.unavailable_reason or "DSCR not computed"))
    else:
        rows = [
            (
                "Minimum DSCR",
                fmt.ratio(d.min_dscr)
                if d.min_dscr is not None
                else fmt.unavailable("not measured"),
            ),
            ("Basis", d.basis),
            (
                "Covenant (min P90 DSCR)",
                fmt.ratio(d.covenant)
                if d.covenant is not None
                else fmt.unavailable("no covenant on record"),
            ),
            ("Covenant verdict", fmt.pass_fail(d.covenant_passes)),
        ]
        # WP-UW-03 (D76): the lock-up tier and the thresholds' source, when the record has them.
        if d.lock_up is not None:
            rows.append(
                (
                    "Lock-up threshold",
                    f"{fmt.ratio(d.lock_up)} ({fmt.pass_fail(d.lock_up_passes)})",
                )
            )
        if d.covenant_passes is None and d.covenant_not_evaluated_reason:
            rows.append(("Covenant not evaluated", d.covenant_not_evaluated_reason))
        if d.covenant_source == "lender_terms":
            rows.append(("Covenant source", "the supplied lender terms"))
        parts.append(_kv_table(rows))
        if d.by_year:
            traj = " · ".join(fmt.ratio(x) for x in d.by_year)
            parts.append(f"**DSCR by year (sensitivity over the debt tenor):** {traj}")
    # Covenant value can be present (placeholder) even when DSCR is unavailable — show it honestly.
    if not d.available and d.covenant is not None:
        parts.append(
            f"_Covenant on record: {fmt.ratio(d.covenant)} (placeholder assumption: not bankable)._"
        )
    if d.lender_terms_label:
        lead = (
            "SYNTHETIC lender terms (not a lender's offer)"
            if d.lender_terms_synthetic
            else ("Lender terms")
        )
        parts.append(f"_{lead}: {d.lender_terms_label}._")
    parts.append(_sensitivities(sensitivities))
    return "\n\n".join(p for p in parts if p)


def _sensitivities(sensitivities: tuple) -> str:  # type: ignore[type-arg]
    if not sensitivities:
        return (
            "_Scenario DSCR/return sensitivities (Low / Central / High) are not attached to this "
            "run; they activate when the scenario engine (M6) is run over the case. Not shown "
            "rather than guessed._"
        )
    header = "| Scenario | Time to energisation | Min DSCR | IRR |\n|:--|:--|---:|---:|"
    lines = [header]
    for row in sensitivities:
        min_dscr = fmt.ratio(row.min_dscr) if row.min_dscr is not None else fmt.unavailable("n/a")
        irr = fmt.pct(row.irr_pct) if row.irr_pct is not None else fmt.unavailable("n/a")
        cod = "on time" if row.cod_delay_months == 0 else f"+{row.cod_delay_months} mo (COD delay)"
        lines.append(f"| {row.label} | {cod} | {min_dscr} | {irr} |")
    return "**Sensitivities**\n\n" + "\n".join(lines)


def _split(
    split: ContractedMerchantSplit,
    drafter: ProseDrafter,
    best_candidate: BestCandidateView | None = None,
) -> str:
    parts = [
        "## Contracted vs merchant",
        drafter.firmness_explainer(has_consented_streams=split.has_consented),
    ]
    # Above the tables, at the point of misreading: every row below a no-spec run reads 0.00 while
    # the grid beside it holds measured candidate figures. Authored once on the view model.
    if best_candidate is not None:
        parts.append(best_candidate.register_lead(fmt.gbp))

    parts.append("**Contracted floor (priced)**: total " + fmt.gbp(split.floor_total_gbp))
    parts.append(
        _stream_table(split.floor) if split.floor else "_No firm/contracted streams in this run._"
    )

    parts.append(
        "**Merchant upside (not priced into the floor)**: total " + fmt.gbp(split.upside_total_gbp)
    )
    parts.append(
        _stream_table(split.upside)
        if split.upside
        else "_No conditional/gated streams in this run._"
    )

    if split.excluded:
        excluded = ", ".join(fmt.stream_label(r.stream) for r in split.excluded)
        parts.append(f"_Excluded (not priced): {excluded}._")
    notes = _stream_notes(split.stream_notes)
    if notes:
        parts.append(notes)
    return "\n\n".join(parts)


def _stream_table(rows: tuple[StreamRow, ...]) -> str:
    header = (
        "| Stream | Annual revenue | Firmness | Gate | Unlock condition | Basis of figure |\n"
        "|:--|---:|:--|:--|:--|:--|"
    )
    lines = [header]
    for r in rows:
        lines.append(
            f"| {fmt.stream_label(r.stream)} | {fmt.gbp(r.annual_revenue_gbp)} | "
            f"{fmt.titleise(r.firmness)} | {r.gate_status or 'n/a'} | {_unlock_cell(r)} | "
            f"{r.figure_basis_cell} |"
        )
    return "\n".join(lines)


#: See `html_report._STREAM_NOTES_LEAD`: same fact, same reason, this document's voice.
_STREAM_NOTES_LEAD = (
    "Each note is the run record's own statement against the streams named in it, reproduced in "
    "full and stated once however many streams carry it. The Basis of figure column cites them by "
    "number. A revenue of zero is not one fact: the optimiser may have priced the stream and "
    "declined it, a gate may have held it out of the priced floor, or no dispatch may have been "
    "solved to measure it at all."
)


def _stream_notes(notes: tuple[StreamNote, ...]) -> str:
    """The de-duplicated per-stream notes, each naming the streams it applies to.

    De-duplicated because a run that selects no system writes the same paragraph of dispatch
    arithmetic onto every stream: printed per row it is one 250-word paragraph repeated eight
    times, which is how a pack gets returned as bloated without disclosing anything more.

    `formatting.no_dashes` is applied to the record text here even though this document is
    deliberately not scrubbed at document level (see `render_underwriting_memo`). The verbatim
    guarantee that exemption protects is over ``quote.warnings``, which the Warnings section still
    reproduces byte-for-byte; nothing pins stream-level warnings, and this is the first record text
    to reach the memo through a table. The record keeps its own strings either way.
    """
    if not notes:
        return ""
    lines = ["### Notes on the value streams", _STREAM_NOTES_LEAD]
    for note in notes:
        streams = fmt.no_dashes(", ".join(fmt.stream_label(s) for s in note.streams))
        lines.append(f"{note.number}. **{streams}.** {fmt.no_dashes(note.text)}")
    return "\n\n".join(lines)


def _unlock_cell(r: StreamRow) -> str:
    """The unlock-condition cell. When the customer consented to the stream, say so — reserving
    the conditional/unlock wording for rows without consent. "Consent given" is authorisation,
    not a revenue guarantee: the row's firmness bar (contracted, never firm for a gated stream)
    and any residual unlock condition still travel alongside it."""
    if r.consent_status == "given":
        if r.unlock_condition:
            return f"Consent given · {r.unlock_condition}"
        return "Consent given"
    return r.unlock_condition or "n/a"


def _price_and_returns(vm: OutputViewModel) -> str:
    p = vm.price
    r = vm.returns
    price = (
        fmt.gbp_per_kwh(p.value_gbp_per_kwh)
        if p.value_gbp_per_kwh is not None
        else fmt.unavailable("needs dispatch energy volume (M3) + a confirmed target return")
    )
    hits = (
        fmt.pass_fail(p.hits_target_return) if p.hits_target_return is not None else "not asserted"
    )
    # The IRR leads, on the owner's instruction (D59): "the project IRR first". The price rows
    # follow it. They used to lead, which answered a question the owner had not asked first.
    rows = [
        (
            "Project IRR (P50)",
            fmt.pct(r.irr_pct)
            if r.irr_pct is not None
            else fmt.unavailable(r.irr_unavailable_reason),
        ),
        (
            "Simple payback",
            fmt.years(r.payback_years)
            if r.payback_years is not None
            else fmt.unavailable(r.payback_unavailable_reason),
        ),
        ("Structure", fmt.structure_label(p.structure)),
        ("Price to target", price),
        ("Hits target return", hits),
    ]
    return "## Project IRR, returns and price to target\n\n" + _kv_table(rows)


def _debt(vm: OutputViewModel) -> str:
    d = vm.debt
    if not d.available:
        return "## Debt assumptions\n\n" + fmt.unavailable(
            d.unavailable_reason
            or (
                "no per-site debt structure on record: gearing / rate / tenor come from the M9 "
                "credit mapping (B001); never guessed"
            )
        )
    rows = [
        (
            "Gearing",
            fmt.pct(d.gearing_pct) if d.gearing_pct is not None else fmt.unavailable("absent"),
        ),
        (
            "Interest rate",
            fmt.pct(d.rate_pct) if d.rate_pct is not None else fmt.unavailable("absent"),
        ),
        (
            "Tenor",
            fmt.years(d.tenor_years) if d.tenor_years is not None else fmt.unavailable("absent"),
        ),
        ("Sculpting basis", d.sculpting_basis or "n/a"),
    ]
    if d.principal_gbp is not None:
        sized = f" (sized by {d.sized_by.replace('_', ' ')})" if d.sized_by else ""
        rows.append(("Principal", f"{fmt.gbp(d.principal_gbp)}{sized}"))
    if d.lender_terms_source is not None:
        rows.append(("Terms source", d.lender_terms_source.replace("_", " ")))
    return "## Debt assumptions\n\n" + _kv_table(rows)


def _counterparty(cp: CounterpartyView) -> str:
    if not cp.available:
        return "## Counterparty credit\n\n" + fmt.unavailable(
            "site context not supplied to this render, so counterparty identity/credit is not "
            "assumed"
        )
    rows = [
        ("Legal name", cp.legal_name or "n/a"),
        ("Company number", cp.company_number or "n/a"),
        ("Sector", cp.sector or "n/a"),
        ("Credit standing", fmt.titleise(cp.credit_status) if cp.credit_status else "n/a"),
        (
            "Expected tenure",
            fmt.years(cp.expected_tenure_years) if cp.expected_tenure_years is not None else "n/a",
        ),
    ]
    out = "## Counterparty credit\n\n" + _kv_table(rows)
    out += f"\n\n_{cp.rate_tenor_note}_"
    if cp.notes:
        out += f"\n\n_Note: {cp.notes}_"
    return out


def _consents_gates(vm: OutputViewModel) -> str:
    cg = vm.consents_gates
    if not cg.available:
        return "## Consents & physical gates\n\n" + fmt.unavailable(
            "site context not supplied to this render, so consent/gate status is not assumed"
        )
    consents = _kv_table([(label, fmt.titleise(status)) for label, status in cg.consents])
    gates = _kv_table([(label, status) for label, status in cg.gates])
    out = (
        "## Consents & physical gates\n\n**Consents**\n\n"
        + consents
        + "\n\n**Physical gates**\n\n"
        + gates
    )
    consented = [r for r in (*vm.split.floor, *vm.split.upside) if r.consent_status == "given"]
    if consented:
        labels = ", ".join(fmt.stream_label(r.stream) for r in consented)
        out += (
            f"\n\n_Stream consents given (customer-authorised, priced on a contracted basis): "
            f"{labels}._"
        )
    if cg.gate_notes:
        out += f"\n\n_Note: {cg.gate_notes}_"
    return out


def _provenance(vm: OutputViewModel) -> str:
    p = vm.provenance
    rows = [
        ("Quote id", f"`{p.quote_id}`"),
        ("Lifecycle mode", fmt.titleise(p.quote_mode)),
        ("Site id", f"`{p.site_id}`"),
        ("Data tier", p.data_tier),
        ("Input hash", f"`{p.input_hash}`" if p.input_hash else fmt.unavailable("unstamped")),
        (
            "Assumption version",
            f"`{p.assumption_version}`" if p.assumption_version else fmt.unavailable("unstamped"),
        ),
        ("Code version", f"`{p.code_version}`" if p.code_version else fmt.unavailable("unstamped")),
        ("Schema version", f"`{p.schema_version}`"),
        ("Quote expiry", fmt.date_str(p.expiry_date) if p.expiry_date else "n/a"),
        ("Generated from record stamped", fmt.date_str(p.created_at)),
    ]
    return (
        "## Provenance\n\n"
        + _kv_table(rows)
        + "\n\n_Every figure above ties to this input hash + assumption/code version; an identical "
        "record regenerates identical figures._"
    )


def _lifecycle_ledger(vm: OutputViewModel) -> str:
    """The "Lifecycle capital flows" table (D131): one row per flow, modelled or excluded,
    plus the construction and indexation basis the run used. Every figure is off the record."""
    record = vm.lifecycle
    if record is None:
        return "## Lifecycle capital flows\n\n" + fmt.unavailable(
            "no lifecycle record on this quote (no financial case, or a record that predates it)"
        )
    lines = [
        "| Flow | Status | Total P50 | Total P90 | Basis | In CFADS | Source / data request |",
        "|:--|:--|---:|---:|:--|:--|:--|",
    ]
    for row in record.flows:
        modelled = row.status.value == "modelled"
        p50 = fmt.gbp(round(sum(row.amounts_by_year_p50_gbp), 2)) if modelled else "excluded"
        p90 = fmt.gbp(round(sum(row.amounts_by_year_p90_gbp), 2)) if modelled else "excluded"
        source = row.provenance if modelled else (row.data_request or row.provenance or "")
        lines.append(
            f"| {row.flow.value} | {row.status.value} | {p50} | {p90} | {row.basis_label} | "
            f"{'yes' if row.in_cfads else 'no'} | {source} |"
        )
    c = record.construction
    construction = (
        f"Construction: {c.status.value}; return basis {c.return_basis.value}; COD delay "
        f"{c.cod_delay_months} month(s)"
        + (f"; IDC {fmt.gbp(c.idc_gbp)} at {c.idc_rate_source}" if c.idc_gbp is not None else "")
        + "."
    )
    ix = record.indexation
    indexation = f"Indexation: CPI key {ix.cpi_state}" + (
        f" ({ix.cpi_pct_per_year:g}%/yr)" if ix.cpi_pct_per_year is not None else ""
    )
    if ix.real_project_irr_pct is not None:
        indexation += f"; real project IRR {ix.real_project_irr_pct:.2f}% ({ix.real_return_basis})"
    if ix.cm_auction_type is not None:
        indexation += f"; CM {ix.cm_auction_type}, year-1 uplift {ix.cm_year1_uplift:.4f}"
    return (
        "## Lifecycle capital flows\n\n"
        + "\n".join(lines)
        + f"\n\n_{construction} {indexation}. Each excluded flow is stated in the warnings with "
        "its data request._"
    )


def _warnings(vm: OutputViewModel) -> str:
    if not vm.warnings:
        return "## Warnings\n\n_No warnings recorded on this quote._"
    lines = "\n".join(f"- {w}" for w in vm.warnings)
    return "## Warnings\n\n" + lines


# --- small markdown helpers (string assembly only; no arithmetic) ----------


def _kv_table(rows: list[tuple[str, str]]) -> str:
    """A two-column key/value Markdown table."""
    out = ["| Field | Value |", "|:--|:--|"]
    out.extend(f"| {key} | {value} |" for key, value in rows)
    return "\n".join(out)


def _assumption_sources_section(vm: OutputViewModel) -> str:
    """The per-assumption source table (C020/C023) — the traceability a lender diligences.

    Every row is copied off the versioned library entry the run's streams cite; empty when the
    release library was not supplied at generation (the run-level assumption version above
    always applies) — noted, never guessed.
    """
    rows = vm.assumption_sources
    if not rows:
        return (
            "## Assumptions & sources\n\n"
            "_Per-assumption sources render when the release library is supplied at generation "
            f"(this run is pinned to assumption version `{vm.provenance.assumption_version}`, "
            "shown in Provenance above). Not shown rather than guessed._"
        )
    header = "| Assumption | Value | Status | Confidence | Source |\n|:--|:--|:--|:--|:--|"
    lines = [header]
    for r in rows:
        value = f"{r.value} {r.unit}".strip()
        lines.append(f"| `{r.key}` | {value} | {r.status} | {r.confidence} | {r.source} |")
    return (
        "## Assumptions & sources\n\n"
        f"Every stream-cited assumption in this pack, from release "
        f"`{vm.provenance.assumption_version}`, with status and source shown per value; nothing "
        "below `confirmed` is bankable.\n\n" + "\n".join(lines)
    )
