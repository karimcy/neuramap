"""Render the customer-facing proposal (M8, issue 012) as plain-language Markdown.

The proposal is the document the installer channel sells with. It says, in plain language: the
system sized for the site, the commercial structure and term, the bill before/after and the
savings. Every number is injected from the same run record behind the underwriting pack, so the
two can never drift apart.

As with the memo, this renderer **authors no number** — it only places view-model values and
formats them via `formatting`, printing an explicit *unavailable* / *pending* label where a figure
does not yet exist (notably the before/after bill, which has no model in v1). Conditional and
gated upside is always labelled as such and never folded into the headline. Prose is number-free.
"""

from __future__ import annotations

from gridq.outputs import formatting as fmt
from gridq.outputs.prose import ProseDrafter, default_prose_drafter
from gridq.outputs.viewmodel import OutputViewModel
from gridq.schemas import SizingGrid


def render_customer_proposal(
    vm: OutputViewModel,
    *,
    drafter: ProseDrafter = default_prose_drafter,
    sizing_grid: SizingGrid | None = None,
) -> str:
    """Render the customer proposal as a plain-language Markdown document from the view model.

    ``sizing_grid`` is the persisted engine projection the full sizing surface is drawn from (D59).
    A customer gets it for the same reason a lender does: it is the evidence that the size in
    front of them was chosen out of a real search rather than picked. Without it the section states
    its own absence rather than being dropped.
    """
    has_priced_floor = vm.split.floor_total_gbp > 0
    parts: list[str] = []
    parts.append("# Your battery storage proposal")
    # The basis, before anything else a customer reads. A study-basis run prices export revenue
    # into the case, which the underwriting basis forbids, and this document carried no word of it.
    if vm.study_basis:
        # Plain English, and deliberately not stream-specific: this used to say only that the case
        # "counts revenue from exporting power to the grid", which is the whole story on the D46
        # export-only basis and roughly a sixth of it on the D55 all-streams basis — where the case
        # also counts Balancing Mechanism, response, Capacity Market and DFS revenue, every one of
        # which assumes a market award nobody has been given. Understating that to a customer is the
        # exact failure the basis labelling exists to prevent.
        parts.append(
            "> **Feasibility study, not an offer.** This document was produced on Neura's "
            "internal feasibility basis. It counts revenue from selling power and flexibility into "
            "energy markets, which needs grid connections, market registrations and permissions "
            "this site does not have and you have not agreed to. It is not an underwriting output "
            "and must not be read as one."
        )
    run_name = "feasibility study run" if vm.study_basis else "underwriting run"
    if not vm.bankable:
        parts.append(
            "> **Indicative: not yet a firm offer.** The figures below come straight from the "
            f"{run_name}; some are still to be confirmed and are labelled as such."
        )
    parts.append(
        drafter.proposal_intro(
            bankable=vm.bankable,
            quote_mode=vm.quote_mode,
            has_priced_floor=has_priced_floor,
            system_selected=_system_selected(vm),
            study_basis=vm.study_basis,
        )
    )
    # THE OWNER'S ORDER (D59, Olly, 2026-08-18, verbatim): "starting with the IRR and Revenue
    # breakdown and graphs, then the methodology and then the assumptions register for each site".
    # The banners and the intro stay above it because they are safety labels, not evidence: a
    # customer must not meet a study-basis return before being told this is not an offer.
    #
    # 1. The return, then the value broken down by stream, then what it does to the bill.
    parts.append(_the_return(vm))
    parts.append(_value_today(vm))
    parts.append(fmt.stream_assumptions_markdown(vm.stream_assumptions))
    # The numbered notes the register above cites. The other three documents render these under
    # the revenue register; this document rendered none, so a citation pointed out of the
    # document rather than into it. Stated once, with every stream each note covers named.
    parts.append(fmt.stream_notes_markdown(vm.split.stream_notes))
    parts.append(_your_bill(vm))
    # 2. The system that earns it, the offer, and the whole space it was chosen out of.
    parts.append(_your_system(vm))
    parts.append(_your_offer(vm))
    parts.append(fmt.sizing_surface_markdown(sizing_grid))
    # 3. Then the methodology.
    parts.append(fmt.methodology_markdown(vm.methodology))
    # 4. Then the assumptions register. D56 rule 4: the customer document gets the same page as the
    # capital-facing one, deliberately: the optimism lives in the value and the candour lives in
    # the label, and a customer is entitled to both.
    parts.append(fmt.assumptions_page_markdown(vm.assumptions_page))
    parts.append(
        "## Next steps\n\n"
        + drafter.next_steps(
            bankable=vm.bankable,
            site_supplied=vm.site_supplied,
            has_consented_streams=vm.split.has_consented,
        )
    )
    parts.append(_footer(vm))
    # The finished document, with em/en dashes rewritten out of every string on the way to the
    # reader (record text included). See `formatting.no_dashes`: the record itself is untouched.
    return fmt.no_dashes("\n\n".join(p for p in parts if p) + "\n")


def _system_selected(vm: OutputViewModel) -> bool:
    """Whether the run selected a system. A recorded outcome, read once and passed as a fact."""
    return vm.spec.selection_status != "no_viable_configuration"


#: The one thing this section must never be read as. A project IRR is Neura's modelled return on the
#: whole project, not a saving the customer receives and not an offer, and on a feasibility-study
#: basis it counts revenue that needs permissions nobody has granted. Stated in the customer's own
#: language, above the figure, every time.
_RETURN_CAVEAT = (
    "These are the numbers for the project as a whole, on our modelling. They are not money you "
    "receive and they are not an offer: your side of this is the bill comparison and the price "
    "further down. We show them because you are entitled to see what the project is being asked to "
    "earn, and because every figure here comes from the same modelling run as the rest of this "
    "document."
)


def _the_return(vm: OutputViewModel) -> str:
    """The project's modelled return, first, with the caveat that says what it is not.

    Section one of the owner's order (D59): "the project IRR first". It appears in the customer
    document for the same reason it appears in the other three, and it is labelled for what it is.
    """
    r = vm.returns
    cf = vm.cashflows
    rows = [
        (
            "Project return (IRR, before any borrowing)",
            fmt.pct(r.irr_pct)
            if r.irr_pct is not None
            else fmt.unavailable(r.irr_unavailable_reason),
        ),
        (
            "How long it takes to pay back",
            fmt.years(r.payback_years)
            if r.payback_years is not None
            else fmt.unavailable(r.payback_unavailable_reason),
        ),
        (
            "Modelling horizon",
            fmt.years(cf.operating_years)
            if cf.operating_years is not None
            else fmt.unavailable("term not yet set"),
        ),
    ]
    return f"## The project's return\n\n{_RETURN_CAVEAT}\n\n{_kv_table(rows)}"


def _your_system(vm: OutputViewModel) -> str:
    s = vm.spec
    if s.selection_status == "no_viable_configuration":
        # "None of them earns enough to justify its cost" is the same unevidenced comparison the
        # consulting pack stopped making: on these runs no return figure was persisted for any
        # candidate, so nothing was weighed against its cost. The recommendation survives, because
        # "we did not find a system we would stand behind" is true either way; the arithmetic
        # claim behind it does not, so it is gone.
        #
        # A reader who gets no further than this section must not leave believing nothing was
        # measured. The figures themselves are further down (the customer would not thank us for the
        # same paragraph twice), so this points at them and says what they are and are not.
        measured = (
            ""
            if vm.best_candidate is None
            else (
                " We did measure real value at candidate sizes, and those figures are set out "
                "further down, under what is secured versus what is upside. They are the reason "
                "for this recommendation, not a system we are offering you."
            )
        )
        return (
            "## Your system\n\n"
            "**Our honest recommendation: we are not proposing a battery at this site today.**\n\n"
            "We modelled battery systems across a wide range of sizes against your tariff and "
            "usage, and the modelling did not produce one we would stand behind. Rather than "
            "recommend equipment we cannot yet show pays for itself, we are telling you plainly: "
            f"do not proceed on the current numbers.{measured} What could change this: a tariff "
            "with time-of-use or distribution-band exposure, export permission (G99) opening "
            "market revenues, or site changes that shift usage into peak-priced periods. The data "
            "requests below would also sharpen the picture."
        )
    rows = [
        (
            "Battery power",
            fmt.kw(s.selected_power_kw)
            if s.selected_power_kw is not None
            else fmt.unavailable("not yet sized"),
        ),
        (
            "Battery energy",
            fmt.kwh(s.selected_energy_kwh)
            if s.selected_energy_kwh is not None
            else fmt.unavailable("not yet sized"),
        ),
        (
            "Duration",
            fmt.hours(s.duration_hours)
            if s.duration_hours is not None
            else fmt.unavailable("not yet sized"),
        ),
    ]
    return "## Your system\n\n" + _kv_table(rows)


def _your_offer(vm: OutputViewModel) -> str:
    p = vm.price
    cf = vm.cashflows
    term = (
        fmt.years(cf.operating_years)
        if cf.operating_years is not None
        else fmt.unavailable("term not yet set")
    )
    price = (
        fmt.gbp_per_kwh(p.value_gbp_per_kwh)
        if p.value_gbp_per_kwh is not None
        else fmt.unavailable("price-to-target not yet computed")
    )
    rows = [
        ("Commercial structure", fmt.structure_label(p.structure)),
        ("Price", price),
        ("Term (modelled)", term),
        # The Brief requires term, indexation and the buyout schedule to be explicit. The
        # contractual values are set with the commercial terms (PPA structure) at signature, so
        # they are named here as confirmed-at-signature rather than invented or silently omitted.
        ("Indexation", fmt.unavailable("set with the commercial terms at signature")),
        ("Buyout schedule", fmt.unavailable("set with the commercial terms at signature")),
        (
            "Quote valid until",
            fmt.date_str(vm.provenance.expiry_date)
            if vm.provenance.expiry_date
            else fmt.unavailable("no expiry set"),
        ),
    ]
    note = (
        "\n\n_The term shown is the modelling horizon used for the financial case, "
        "labelled as such. The contracted term, indexation and buyout schedule are confirmed "
        "with the commercial terms at signature._"
    )
    if p.exceeds_counterfactual:
        counterfactual = (
            fmt.gbp_per_kwh(p.counterfactual_import_gbp_per_kwh)
            if p.counterfactual_import_gbp_per_kwh is not None
            else "your current rate"
        )
        note += (
            "\n\n**A straight answer on price:** for this project to pay its way at our "
            f"target return, the energy price would need to sit above what you already pay "
            f"({counterfactual}). That is not an offer we would put in front of you; the "
            "figure is shown so you can see exactly how far the numbers are from working."
        )
    return "## Your offer\n\n" + _kv_table(rows) + note


def _your_bill(vm: OutputViewModel) -> str:
    """Three states: a priced comparison, the BEFORE side alone, or no bill at all.

    The middle state used to fall through to the "not yet computed" seam, which threw away a real,
    evidenced figure (the customer's own cost today, which needs no battery to compute) and told
    the reader it had not been computed. It had. Only the after side does not exist.
    """
    b = vm.bill_savings
    if b.available and b.after_side_priced and b.annual_savings_gbp is not None:
        rows = [
            (
                "Your bill today",
                fmt.gbp(b.bill_before_gbp)
                if b.bill_before_gbp is not None
                else fmt.unavailable("not provided"),
            ),
            (
                "Your bill with the battery",
                fmt.gbp(b.bill_after_gbp)
                if b.bill_after_gbp is not None
                else fmt.unavailable("not provided"),
            ),
            ("Estimated annual saving", fmt.gbp(b.annual_savings_gbp)),
        ]
        if b.peak_before_kw is not None and b.peak_after_kw is not None:
            rows.append(
                (
                    "Peak import (measured)",
                    f"{fmt.kw(b.peak_before_kw)} → {fmt.kw(b.peak_after_kw)}",
                )
            )
        # The bill's honesty label always travels with the headline (what is NOT modelled).
        basis_note = f"\n\n_{b.basis}_" if b.basis else ""
        peak_note = (
            "\n\n_Peak import is informational: the dispatch optimises your bill, not your "
            "peak. Capacity-charge value is not priced until the peak-protection objective "
            "lands (C029)._"
            if b.peak_before_kw is not None and b.peak_after_kw is not None
            else ""
        )
        return "## Your bill: before & after\n\n" + _kv_table(rows) + basis_note + peak_note
    if b.available and b.bill_before_gbp is not None:
        rows = [("Your bill today", fmt.gbp(b.bill_before_gbp))]
        if b.peak_before_kw is not None:
            rows.append(("Your peak import today (measured)", fmt.kw(b.peak_before_kw)))
        basis_note = f"\n\n_{b.basis}_" if b.basis else ""
        return (
            "## Your bill: before & after\n\n"
            + _kv_table(rows)
            + "\n\n**No system is recommended on this run, so there is no with-battery bill and "
            "no saving to show you.** The figure above is what your site costs today on your own "
            "tariff and your own metered load. It does not depend on a battery, which is why we "
            "can show it either way." + basis_note
        )
    # No bill on the record at all. Surface the seam honestly, never an estimate.
    return (
        "## Your bill: before & after\n\n"
        + fmt.unavailable("not yet computed")
        + f"\n\n{b.pending_reason}\n\n**What we need from you:** {b.data_ask}"
    )


def _value_today(vm: OutputViewModel) -> str:
    split = vm.split
    parts = ["## What's secured vs what's upside"]
    # A total the record marks "value unavailable (not invented)" is a sum of absences. Printing it
    # as "£0.00 / year ... the firm and contracted revenue underwriting relies on" told a customer
    # that a measurement had been taken and had come to nothing. It had not been taken at all.
    if split.floor and not split.floor_value_measured:
        parts.append(
            "**Contracted value (counted in your case): "
            + fmt.unavailable("no system was selected, so nothing is counted in your case")
            + ".**"
        )
        # This paragraph said "no value has been calculated for any of these streams", which the run
        # record contradicts: the sizing grid holds measured revenue for every candidate the search
        # priced, and on the 2026-08-15 campaign the best of them earned real money from a dispatch
        # that discharged. The claim was the defect in its worst place, the document the customer
        # reads. The measurement replaces it and the do-not-proceed verdict is unchanged: the
        # measured figures are what make the verdict a finding instead of an absence.
        if vm.best_candidate is not None:
            parts.append(
                "Nothing is counted in your case because no system was selected, so there is no "
                f"system to price these streams for ({_stream_names(split.floor)})."
            )
            parts.append(vm.best_candidate.customer_paragraph(fmt.gbp))
        else:
            parts.append(
                "No system was selected, so there is no dispatch to price and no value has been "
                "calculated for any of these streams "
                f"({_stream_names(split.floor)}). That is different from a calculation that came "
                "to nothing, and we will not show you a zero we did not measure."
            )
    else:
        parts.append(
            "**Contracted value (counted in your case): "
            + fmt.gbp(split.floor_total_gbp)
            + " / year.**"
        )
        if split.floor:
            streams = _stream_names(split.floor)
            parts.append(
                f"This is the firm and contracted revenue underwriting relies on ({streams})."
            )
        else:
            parts.append(
                "No firm or contracted revenue is priced in this run yet, so nothing is promised "
                "on that basis. As the underwriting firms up, contracted value appears here."
            )
    if split.upside and not split.upside_value_measured:
        parts.append(
            "**Potential upside (not promised): "
            + fmt.unavailable("not calculated on this run")
            + ".** "
            f"The same applies to the conditional streams ({_stream_names(split.upside)}): with "
            "no dispatch, none of them has been valued either."
        )
    elif split.upside:
        upside_streams = _stream_names(split.upside)
        parts.append(
            "**Potential upside (not promised): " + fmt.gbp(split.upside_total_gbp) + " / year.** "
            f"Conditional / market-dependent streams ({upside_streams}) could add value once their "
            "unlock conditions are met. They are deliberately kept out of your headline."
        )
    if split.has_consented:
        parts.append(
            "_“Consent given” marks a stream you have already authorised. Consent is your "
            "permission, not a promise of revenue: a consented stream is counted on a contracted "
            "basis (with its downside haircut applied), never as guaranteed income._"
        )
    return "\n\n".join(parts)


def _stream_names(rows: tuple) -> str:  # type: ignore[type-arg]
    """Comma-joined stream labels, annotating any the customer consented to with "(consent
    given)". A row without consent renders exactly as before (no change to unconsented output)."""
    return ", ".join(
        f"{fmt.stream_label(r.stream)} (consent given)"
        if r.consent_status == "given"
        else fmt.stream_label(r.stream)
        for r in rows
    )


def _footer(vm: OutputViewModel) -> str:
    p = vm.provenance
    return (
        "---\n\n"
        f"_Quote `{p.quote_id}` · priced against assumption release "
        f"`{p.assumption_version or 'unstamped'}` · every figure traces to the "
        f"{'feasibility study run' if vm.study_basis else 'underwriting run'} behind this "
        "document._"
    )


def _kv_table(rows: list[tuple[str, str]]) -> str:
    out = ["| | |", "|:--|:--|"]
    out.extend(f"| {key} | {value} |" for key, value in rows)
    return "\n".join(out)
