"""Quote lifecycle — the append-only ``quote_mode`` state machine.

A quote moves forward through ``draft → indicative → underwritten → contracted → operating``;
from any live state it may ``lapse``. ``lapsed`` is terminal. Every transition returns a **new**
`QuoteRecord` (records are treated as immutable snapshots): the outgoing mode is appended to
``status_history`` and ``quote_mode`` is advanced, so the full history is preserved and never
mutated in place.

Convention (matches `StatusHistoryEntry`'s "one prior lifecycle state" docstring and
``examples/quote_record_example.json``): ``status_history`` holds only **prior** states; the
**current** state is ``quote_mode`` and is not duplicated in the history.

Transitions go through `QuoteRecord.model_validate` (not `model_copy`) so the schema's
validators re-run — in particular the "underwritten-from-a-T0-site needs the acknowledgement"
guard, which a `model_copy` would silently skip.

**The chain is not the only gate.** Advancing to ``underwritten`` (or beyond) also requires the
record to carry the evidence for the claim that word makes — see `underwriting_blockers`. Before
N20/N21 the chain WAS the only gate, so a quote with no financial case at all, from the
integration-only engine, on a modelled load shape, could be walked to ``underwritten`` in four
calls and nothing anywhere would object.
"""

from __future__ import annotations

from datetime import datetime

from gridq.schemas import (
    ConfidenceBand,
    HalfHourlyBasis,
    ImplementationLevel,
    QuoteMode,
    QuoteRecord,
    SelectionStatus,
    StatusHistoryEntry,
)

#: The single forward step allowed from each live state (the chain is strictly sequential).
_FORWARD: dict[QuoteMode, QuoteMode] = {
    QuoteMode.DRAFT: QuoteMode.INDICATIVE,
    QuoteMode.INDICATIVE: QuoteMode.UNDERWRITTEN,
    QuoteMode.UNDERWRITTEN: QuoteMode.CONTRACTED,
    QuoteMode.CONTRACTED: QuoteMode.OPERATING,
}

#: States with no outgoing transition.
_TERMINAL: frozenset[QuoteMode] = frozenset({QuoteMode.LAPSED})

#: The states that assert the quote has been underwritten. Everything from UNDERWRITTEN onward
#: inherits the claim, so the preconditions are checked for all three — not only at the boundary.
_UNDERWRITTEN_OR_BEYOND: frozenset[QuoteMode] = frozenset(
    {QuoteMode.UNDERWRITTEN, QuoteMode.CONTRACTED, QuoteMode.OPERATING}
)


class InvalidTransitionError(ValueError):
    """Raised when a requested ``quote_mode`` transition is not allowed."""


def underwriting_blockers(quote: QuoteRecord) -> list[str]:
    """Every reason ``quote`` may not be called underwritten. Empty list = it may (N20, N21).

    `transition_quote` used to gate on the `_FORWARD` chain alone, so a record with
    ``financial_case=None`` — exactly what the `InsufficientInputsError` path produces, and it
    still assembles a record — could be walked to ``underwritten``. "Underwritten" is the word the
    pack uses to tell a lender the case was tested; nothing was checking that anything had been.

    Each check is a *structural* fact of the record, never a commercial judgement:

    - a financial case exists at all (there is otherwise nothing that was underwritten);
    - a system was actually sized (a quote with no kW is not a quote for anything);
    - the engine that produced it was not the integration-only `BaselineEngine`, whose numbers
      are plumbing and are documented as never real;
    - the run's own selection verdict is not a refusal — a run that reported
      ``no_viable_configuration`` cannot be underwritten into a recommendation;
    - the Gate-2 half-hourly basis is recorded, and a basis that cannot reach HIGH confidence
      (MODELLED/MIXED) needs an explicit `basis_override_note` (N21). This is the ceiling
      `pipeline` announces in prose; here it is the thing that actually stops the transition.

    **What is deliberately NOT a precondition: the covenant outcome.** ``underwritten`` says the
    case was TESTED, not that it passed; ``bankable_status`` and `CovenantStatus.passes` say
    whether it passed, and they are separately recorded and separately reported. A quote whose
    P90 DSCR sits below the covenant is a correct, useful, underwritten quote that says so — and
    per `docs/validation_requirements.md` §3 a high-confidence negative result is a **successful**
    validation. Blocking here would conflate the two words and would also refuse every unlevered
    case (no debt structure ⇒ ``passes=None``), turning a financing-data gap into a project
    verdict, which that same document explicitly forbids.
    """
    failures: list[str] = []
    case = quote.financial_case
    result = quote.optimisation_result

    if case is None:
        failures.append(
            "financial_case is None — no case was priced, so there is nothing underwritten "
            "(this is the shape the InsufficientInputsError path produces)"
        )

    if result is None:
        failures.append("optimisation_result is None — no engine run is attached to this quote")
    else:
        if not result.selected_power_kw:
            failures.append(
                "no system was sized (optimisation_result.selected_power_kw is unset or zero)"
            )
        if result.implementation_level is ImplementationLevel.BASELINE:
            failures.append(
                "the run used the integration-only BaselineEngine "
                "(implementation_level=baseline), whose numbers are plumbing, not prices"
            )
        # `selection_status` is additive and optional, so a historical record carries None; only
        # an explicitly recorded refusal blocks — an absent verdict is not read as a pass or a fail.
        if (
            result.selection_status is not None
            and result.selection_status is not SelectionStatus.SELECTED
        ):
            failures.append(
                f"the run's own verdict is {result.selection_status.value}, not selected — "
                "a refusal cannot be advanced into an underwritten recommendation"
            )

    if quote.half_hourly_basis is None:
        failures.append(
            "no half-hourly basis is recorded on the quote (validation Gate 2) — underwriting "
            "requires knowing whether the load shape was metered or modelled"
        )
    elif quote.half_hourly_basis is not HalfHourlyBasis.METERED and not quote.basis_override_note:
        ceiling = (quote.confidence_ceiling or ConfidenceBand.MEDIUM).value
        failures.append(
            f"the half-hourly basis is {quote.half_hourly_basis.value.upper()}, which caps "
            f"confidence at {ceiling.upper()} — it is evidence about a typical site of this type, "
            "not about this site. Advancing past `indicative` on it needs a deliberate, recorded "
            "decision in `basis_override_note`"
        )

    return failures


def legal_transitions(mode: QuoteMode) -> set[QuoteMode]:
    """Return the set of modes ``mode`` may transition to."""
    if mode in _TERMINAL:
        return set()
    nxt: set[QuoteMode] = {QuoteMode.LAPSED}
    forward = _FORWARD.get(mode)
    if forward is not None:
        nxt.add(forward)
    return nxt


def current_mode(quote: QuoteRecord) -> QuoteMode:
    """The quote's current lifecycle state."""
    return quote.quote_mode


def history_modes(quote: QuoteRecord) -> list[QuoteMode]:
    """The ordered prior states the quote has passed through (excludes the current state)."""
    return [entry.quote_mode for entry in quote.status_history]


def transition_quote(
    quote: QuoteRecord,
    new_mode: QuoteMode,
    *,
    note: str | None = None,
    timestamp: datetime | None = None,
) -> QuoteRecord:
    """Advance ``quote`` to ``new_mode``, returning a new, validated `QuoteRecord`.

    Appends the *outgoing* mode to ``status_history`` (append-only; prior entries are never
    touched) and sets ``quote_mode = new_mode``. Raises `InvalidTransitionError` if the
    transition is not allowed by the lifecycle, **or** if ``new_mode`` claims the quote was
    underwritten and the record cannot support that claim — see
    `underwriting_blockers` (N20, N21).
    """
    if new_mode not in legal_transitions(quote.quote_mode):
        allowed = (
            ", ".join(sorted(m.value for m in legal_transitions(quote.quote_mode))) or "(none)"
        )
        raise InvalidTransitionError(
            f"illegal quote transition {quote.quote_mode.value} -> {new_mode.value}; "
            f"allowed from {quote.quote_mode.value}: {allowed}."
        )

    if new_mode in _UNDERWRITTEN_OR_BEYOND:
        failures = underwriting_blockers(quote)
        if failures:
            raise InvalidTransitionError(
                f"quote {quote.quote_id} cannot advance to {new_mode.value}: "
                + "; ".join(failures)
                + ". `underwritten` asserts to a lender that the case was tested — the record "
                "must carry the evidence for that assertion before it may carry the label."
            )

    new_entry = StatusHistoryEntry(
        quote_mode=quote.quote_mode,
        timestamp=timestamp if timestamp is not None else datetime.now(),
        note=note,
    )

    dump = quote.model_dump(mode="json")
    dump["quote_mode"] = new_mode.value
    dump["status_history"] = [*dump["status_history"], new_entry.model_dump(mode="json")]
    # Re-validate so the schema invariants (e.g. underwritten-from-T0 needs a warning) re-fire.
    return QuoteRecord.model_validate(dump)
