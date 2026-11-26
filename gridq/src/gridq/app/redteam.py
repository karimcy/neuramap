"""Deterministic red-team review of a run: every issue, a clear fix, and who owns it.

This is the data behind the Review page. It is NOT an LLM: it compiles the run's own ranked
data-asks and warnings (the deterministic record) into an actionable checklist. Each issue carries
a plain-English problem, a short bullet to-do list, and the owner who needs to act. No em-dashes in
the copy. It invents nothing: the items are a re-presentation of what the engine and ingestion
already flagged, mapped to a fix playbook keyed on the stable ``ask_id`` / warning text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from gridq.app import store


@dataclass(frozen=True)
class ReviewItem:
    """One red-team finding: the problem, a bulleted fix, the owner, and a severity for ordering."""

    title: str
    severity: str  # blocker | assumption | condition | estimate | info
    problem: str
    todos: tuple[str, ...]
    owner: str


# Owners map to the System Brief ownership: Olly (the whole GridQ engine — ingestion, SiteModel,
# optimisation/revenue engine, financial-model wrapper, outputs, assumption library), Zara (growth:
# customer and installer relationship), Sim (finance: cost assumptions, credit policy), Karim
# (operations, partnerships and legal: DNO, consents, supply contract, route to market).
#: Per data-ask fix playbook, keyed on the stable ``DataAsk.ask_id``: (owner, severity, to-do list).
_ASK_PLAYBOOK: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "hh_12_months": (
        "Zara (growth), with the customer or installer",
        "blocker",
        (
            "Request a 12-month half-hourly export from the supplier or meter operator (MOP).",
            "Open the run and upload the file under Improve this quote to recalculate.",
        ),
    ),
    "tariff_stack": (
        "Zara (growth), from the customer's bill",
        "blocker",
        (
            "Collect the latest electricity bill or supply contract from the customer.",
            "Enter the standing charge, unit rates by time band, DUoS bands and levy lines.",
            "Replace the reference rates with the confirmed figures so the floor can bank.",
        ),
    ),
    "connection_mic_mec_asc": (
        "Karim (operations), with the DNO",
        "blocker",
        (
            "Read MIC, MEC and ASC from the DNO connection agreement.",
            "Confirm the export capacity (MEC) if the site will export.",
            "Enter the confirmed capacities so the dispatch headroom is bounded.",
        ),
    ),
    "dno_region": (
        "Olly (ingestion)",
        "condition",
        (
            "Confirm the distribution region from the bill or the postcode.",
            "Select the matching DNO so the correct DUoS bands apply.",
        ),
    ),
    "export_consents": (
        "Karim (operations and legal)",
        "condition",
        (
            "Confirm with the customer whether the site will export.",
            "If exporting, apply for G99 or G100 consent.",
            "Provide the export MPAN so export and wholesale streams can be firmed.",
        ),
    ),
    "supplier_consent": (
        "Karim (operations and legal)",
        "condition",
        (
            "Contact the electricity supplier about a behind-the-meter battery.",
            "Obtain written supplier consent for the supply-contract interaction.",
        ),
    ),
    "physical_gates": (
        "Karim (operations), with the installer",
        "condition",
        (
            "Commission a site survey with the installer.",
            "Confirm footprint, fire separation and access each pass or fail.",
            "Record each gate so the quote can move from conditional to firm.",
        ),
    ),
    "counterparty_identity": (
        "Zara (growth), from the customer",
        "blocker",
        (
            "Collect the registered legal entity name and company number from the customer.",
            "Sim then runs the deterministic credit mapping to set the rate and tenor.",
        ),
    ),
    "counterparty_credit_status": (
        "Sim (finance, credit policy)",
        "blocker",
        (
            "Run the counterparty credit check once the company number is supplied.",
            "Record the credit band so the financing rate and tenor can be set.",
        ),
    ),
    "wholesale_forecast_file": (
        "Karim (route to market), with Olly",
        "info",
        (
            "Karim sources a wholesale or balancing price forecast from the route-to-market partner.",  # noqa: E501
            "Olly wires the forecast into the dispatch model; until then wholesale stays gated upside.",  # noqa: E501
        ),
    ),
}

_SEVERITY_ORDER = {"blocker": 0, "assumption": 1, "condition": 2, "estimate": 3, "info": 4}
SEVERITY_LABEL = {
    "blocker": "Blocks bankable",
    "assumption": "Confirm assumption",
    "condition": "Underwriting condition",
    "estimate": "Estimate grade",
    "info": "For information",
}


def _assumption_name(warning: str) -> str | None:
    """Pull the dotted assumption key out of a placeholder warning, if present."""
    match = re.search(r"'([a-z0-9_]+\.[a-z0-9_]+)'", warning)
    return match.group(1) if match else None


_ENGINE_TOKENS = ("efficiency", "round_trip", "degradation", "cycle", "throughput", "dispatch")
_FINANCE_TOKENS = (
    "capex",
    "opex",
    "insurance",
    "finance",
    "debt",
    "rate",
    "wacc",
    "discount",
    "cost",
    "credit",
    "dscr",
    "tax",
)


def _assumption_owner(name: str) -> str:
    """Route a placeholder assumption to its owner per the ownership split."""
    key = name.lower()
    if any(token in key for token in _ENGINE_TOKENS):
        return "Olly (optimisation engine)"
    if any(token in key for token in _FINANCE_TOKENS):
        return "Sim (finance)"
    return "Olly (assumption library)"


def red_team(stored: store.StoredRun) -> dict[str, Any]:
    """Compile a run's data-asks and warnings into ordered, owned, actionable review items."""
    items: list[ReviewItem] = []

    if stored.site is not None:
        from gridq.ingestion import build_ranked_data_asks

        for ask in build_ranked_data_asks(stored.site):
            owner, severity, todos = _ASK_PLAYBOOK.get(
                ask.ask_id,
                ("Customer", "condition", ("Supply the listed input to sharpen the quote.",)),
            )
            items.append(
                ReviewItem(
                    title=ask.title, severity=severity, problem=ask.why, todos=todos, owner=owner
                )
            )

    warnings = list(stored.quote.warnings or [])

    placeholders = [
        name
        for warning in warnings
        if "placeholder" in warning.lower() and "assumption" in warning.lower()
        for name in [_assumption_name(warning)]
        if name
    ]
    if placeholders:
        # Group the placeholder assumptions by their owner so each person sees only their list.
        by_owner: dict[str, list[str]] = {}
        for name in placeholders:
            by_owner.setdefault(_assumption_owner(name), []).append(name)
        for owner, names in by_owner.items():
            items.append(
                ReviewItem(
                    title="Confirm placeholder assumptions",
                    severity="assumption",
                    problem=(
                        "These assumptions are placeholders, so the result cannot be bankable "
                        "until each is confirmed against a documented source."
                    ),
                    todos=tuple(f"Confirm '{name}' against a sourced value." for name in names),
                    owner=owner,
                )
            )

    if any("SYNTHETIC" in warning for warning in warnings):
        items.append(
            ReviewItem(
                title="Load profile is modelled, not metered",
                severity="estimate",
                problem=(
                    "Dispatch and sizing were computed on a synthetic load profile estimated from "
                    "annual usage and business type. It is an estimate and is not bankable."
                ),
                todos=(
                    "Request a 12-month half-hourly meter export.",
                    "Upload it under Improve this quote to recalculate on real metered data.",
                ),
                owner="Zara (growth), with the customer or installer",
            )
        )

    if any("standard reference rates" in warning for warning in warnings):
        items.append(
            ReviewItem(
                title="Tariff uses reference rates, not confirmed rates",
                severity="condition",
                problem=(
                    "The import floor was estimated on standard reference tariff rates for the "
                    "selected type, not the customer's confirmed tariff."
                ),
                todos=(
                    "Collect the latest electricity bill from the customer.",
                    "Enter the confirmed standing charge, unit rates and DUoS bands.",
                ),
                owner="Zara (growth), from the customer",
            )
        )

    if any("horizon defaulted" in warning.lower() for warning in warnings):
        items.append(
            ReviewItem(
                title="Confirm the projection horizon",
                severity="info",
                problem=(
                    "The projection horizon defaulted to a structural modelling value; confirm it "
                    "matches the intended contract length."
                ),
                todos=("Confirm the projection horizon in years for this case.",),
                owner="Olly (modelling)",
            )
        )

    items.sort(key=lambda i: _SEVERITY_ORDER.get(i.severity, 9))
    counts = {key: sum(1 for i in items if i.severity == key) for key in _SEVERITY_ORDER}
    # Combined "Conditions" stat (assumptions to confirm + underwriting conditions), precomputed
    # here so the template never does arithmetic on a displayed figure (W5-1 invariant).
    counts["conditions_combined"] = counts["assumption"] + counts["condition"]
    return {"items": items, "counts": counts, "total": len(items)}
