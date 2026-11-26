"""Prose drafting for the output artifacts (M8) — the *only* narrative seam.

The hard M8 rule: **the LLM (if any) drafts prose only; it never originates or alters a number.**
This module makes that mechanically true. A `ProseDrafter` produces the connective narrative
(framing, plain-language explanation, next steps) from **categorical facts only** — booleans and
enum-ish strings such as the lifecycle mode or data tier. No method accepts a `FinancialCase`
figure, a cashflow, a DSCR, or any £/%/ratio, so prose *cannot* carry a record number; every
figure is injected separately by the renderers via `formatting`.

v1 ships the `DeterministicProseDrafter` — fixed, audited copy, so output is fully deterministic
and LLM-free on the whole path. The Protocol is the seam: a perimeter LLM drafter (schema-checked,
human-signed) can be injected later for richer phrasing **without ever touching a number**.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ProseDrafter(Protocol):
    """Draft narrative blocks from categorical facts only (never a number).

    Implementations must not accept or emit any financial figure — the renderers inject numbers.
    """

    def banner_line(self, *, bankable: bool) -> str:
        """The one-line bankability banner caption."""
        ...

    def memo_summary(
        self, *, bankable: bool, quote_mode: str, data_tier: str, has_warnings: bool
    ) -> str:
        """The underwriting memo's opening framing paragraph."""
        ...

    def firmness_explainer(self, *, has_consented_streams: bool = False) -> str:
        """Explain the contracted (priced floor) vs merchant (conditional/gated) split.

        ``has_consented_streams`` is a categorical fact (not a number): when True the copy adds a
        clause explaining the "consent given" label; the default keeps the copy identical to the
        no-consent path.
        """
        ...

    def proposal_intro(
        self,
        *,
        bankable: bool,
        quote_mode: str,
        has_priced_floor: bool,
        system_selected: bool = True,
        study_basis: bool = False,
    ) -> str:
        """The customer proposal's plain-language opening.

        ``system_selected`` and ``study_basis`` are categorical facts about the run, not figures.
        The opening asserted a sized system unconditionally and called every run "the underwriting
        run", so a study-basis run that selected nothing opened by telling the customer the
        opposite of both. The defaults keep the copy identical for a solved underwriting run.
        """
        ...

    def next_steps(
        self, *, bankable: bool, site_supplied: bool, has_consented_streams: bool = False
    ) -> str:
        """Plain-language next steps for the reader."""
        ...


class DeterministicProseDrafter:
    """The v1 drafter: fixed, number-free copy keyed off categorical facts. No LLM."""

    def banner_line(self, *, bankable: bool) -> str:
        if bankable:
            return (
                "BANKABLE CANDIDATE: fit for a lender's review, subject to the listed conditions."
            )
        return (
            "NOT BANKABLE: this run prices no revenue and/or rests on placeholder assumptions. "
            "Do not present to capital as bankable."
        )

    def memo_summary(
        self, *, bankable: bool, quote_mode: str, data_tier: str, has_warnings: bool
    ) -> str:
        lead = (
            "This underwriting pack is drawn entirely from the stamped run record; every figure "
            "below is injected from that record and tied to the assumption and code versions noted "
            "in the provenance block."
        )
        if bankable:
            standing = (
                f"The quote is a {quote_mode} case on {_tier_phrase(data_tier)} data and is a "
                "bankable candidate, firm subject to the listed consents, gates and credit."
            )
        else:
            standing = (
                f"The quote is a {quote_mode} case on {_tier_phrase(data_tier)} data and is "
                "explicitly NOT bankable: it is produced by the integration/baseline path and/or "
                "rests on placeholder assumptions, so no revenue is priced into the floor."
            )
        tail = (
            " The warnings section lists, by name, every placeholder and unavailable input behind "
            "these numbers."
            if has_warnings
            else ""
        )
        return f"{lead} {standing}{tail}"

    def firmness_explainer(self, *, has_consented_streams: bool = False) -> str:
        base = (
            "The contracted floor counts only firmness that underwriting can rely on: firm and "
            "contracted streams. Conditional and gated-upside streams (wholesale, balancing "
            "mechanism, ancillary services) are shown separately as merchant upside with their "
            "unlock conditions; they are never priced into the bankable floor."
        )
        if has_consented_streams:
            base += (
                " Streams marked “Consent given” are ones the customer has explicitly authorised "
                "at intake; consent is authorisation, not a guarantee of revenue. A consented "
                "stream is still shown at its own firmness (contracted, not firm) with its "
                "haircut applied, never elevated by the consent alone."
            )
        return base

    def proposal_intro(
        self,
        *,
        bankable: bool,
        quote_mode: str,
        has_priced_floor: bool,
        system_selected: bool = True,
        study_basis: bool = False,
    ) -> str:
        # Two claims used to be unconditional here and both were false on every run that reached a
        # customer: that a system had been sized, and that the run behind the document was an
        # underwriting run. The first is now keyed off the record's selection, the second off the
        # record's own basis label.
        run_name = "feasibility study run" if study_basis else "underwriting run"
        if system_selected:
            opening = (
                "This proposal sets out a battery energy storage system sized for your site, with "
                "the commercial terms in plain language. Every figure comes from the same "
                f"{run_name} behind it, so what you see here cannot drift from the financial case."
            )
        else:
            opening = (
                "This document sets out what we found when we modelled battery storage at your "
                "site. No system is proposed, because the modelling did not select one. Every "
                f"figure comes from the same {run_name} behind it, so what you see here cannot "
                "drift from the modelling."
            )
        # The basis is NOT restated here. Each document that renders this opening carries the
        # full statement of the basis in its own banner, immediately above, and saying it twice in
        # four lines reads as boilerplate rather than as a warning.
        if not has_priced_floor:
            # `has_priced_floor` says one thing only: the floor totals nothing. It said three,
            # and two of them stopped being true. The bill clause is gone because a run with an
            # empty floor is exactly the run that now carries a computed BEFORE bill, which this
            # same document prints a few sections below (`proposal._your_bill`); and the priced
            # revenue clause is gone because on such a run the revenue WAS computed and came to
            # zero, which is a result, not a pending calculation.
            opening += (
                " This is an early-stage estimate: no revenue is counted in your case, and the "
                "figures below show what is and is not yet available."
            )
        if not bankable:
            opening += (
                f" It is a {quote_mode} quote and is not yet a firm, bankable offer. See the "
                "next steps for what would make it one."
            )
        return opening

    def next_steps(
        self, *, bankable: bool, site_supplied: bool, has_consented_streams: bool = False
    ) -> str:
        if bankable:
            base = (
                "To progress: confirm the listed consents and physical gates, complete the "
                "counterparty credit check, and sign within the validity window."
            )
        else:
            base = (
                "To progress toward a firm, bankable quote: supply the ranked data the warnings "
                "call out (validated 12-month half-hourly load, confirmed connection capacities, "
                "export consents), and reprice against a confirmed assumption release."
            )
        if has_consented_streams:
            base += (
                " The streams you have already given consent for are carried into the case on a "
                "contracted basis and labelled “Consent given”; the remaining conditions above "
                "still apply before they are firm."
            )
        if not site_supplied:
            base += (
                " (Counterparty, consent and gate detail were not supplied to this render and are "
                "shown as such, not assumed.)"
            )
        return base


def _tier_phrase(data_tier: str) -> str:
    """A short, number-free phrase for the data tier (categorical, not a figure)."""
    mapping = {
        "T0_ANNUAL_KWH_TARIFF_POSTCODE": "annual-kWh-only (T0)",
        "T1_PARTIAL_HALF_HOURLY": "partial half-hourly (T1)",
        "T2_VALIDATED_12_MONTH_HALF_HOURLY": "validated half-hourly (T2)",
    }
    return mapping.get(data_tier, data_tier)


#: The default drafter used everywhere in v1 (LLM-free).
default_prose_drafter = DeterministicProseDrafter()

__all__ = ["ProseDrafter", "DeterministicProseDrafter", "default_prose_drafter"]
