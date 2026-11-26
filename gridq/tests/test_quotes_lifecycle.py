"""Tests for `gridq.quotes.lifecycle` — the append-only quote_mode state machine.

Covers: the legal forward chain; illegal jumps raise; lapse from any live state and lapsed is
terminal; status_history is append-only (prior snapshots preserved, current not duplicated); and
— the regression that proves transitions go through `model_validate`, not `model_copy` — the
"underwritten-from-a-T0-site needs the acknowledgement" schema guard re-fires on transition.

The chain is no longer the only gate (N20/N21): `underwritten` and beyond additionally require the
record to carry the evidence for the claim (a priced case, a sized system, a real engine, a
non-refusal verdict, a recorded half-hourly basis). The fixtures below therefore build an
`_underwritable_quote` for the forward-chain tests; `underwriting_blockers` and the refusal cases
are pinned in `tests/test_truth_in_labelling.py`.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.quotes import (
    InvalidTransitionError,
    generate_quote,
    history_modes,
    legal_transitions,
    transition_quote,
)
from gridq.schemas import (
    T0_UNDERWRITING_ACKNOWLEDGEMENT,
    DataTier,
    QuoteMode,
    QuoteRecord,
    StatusHistoryEntry,
)


def _draft_quote(
    *, data_tier: DataTier = DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY, warnings=None
) -> QuoteRecord:
    return QuoteRecord(
        quote_id="quote_test",
        quote_mode=QuoteMode.DRAFT,
        site_id="site_test",
        data_tier=data_tier,
        warnings=warnings if warnings is not None else ["baseline: not bankable"],
        status_history=[],
    )


def _underwritable_quote(rich_quote, *, mode: QuoteMode = QuoteMode.DRAFT) -> QuoteRecord:
    """A record that can legitimately carry the `underwritten` label (N20/N21).

    A priced case, a sized system from a non-baseline engine and a recorded METERED half-hourly
    basis — everything `lifecycle.underwriting_blockers` looks for. The bare `_draft_quote` above
    has none of it, which is exactly why it may no longer be walked past `indicative`.
    """
    return rich_quote.quote.model_copy(update={"quote_mode": mode, "status_history": []})


def test_legal_forward_chain_and_history_growth(rich_quote):
    q = _underwritable_quote(rich_quote)
    for nxt in (
        QuoteMode.INDICATIVE,
        QuoteMode.UNDERWRITTEN,
        QuoteMode.CONTRACTED,
        QuoteMode.OPERATING,
    ):
        before = len(q.status_history)
        q = transition_quote(q, nxt, note=f"->{nxt.value}")
        assert q.quote_mode == nxt
        assert len(q.status_history) == before + 1

    assert history_modes(q) == [
        QuoteMode.DRAFT,
        QuoteMode.INDICATIVE,
        QuoteMode.UNDERWRITTEN,
        QuoteMode.CONTRACTED,
    ]
    # Current state is NOT duplicated in history.
    assert q.quote_mode not in history_modes(q)
    # Notes record the transition that left each state.
    assert [e.note for e in q.status_history] == [
        "->indicative",
        "->underwritten",
        "->contracted",
        "->operating",
    ]


@pytest.mark.parametrize(
    "target", [QuoteMode.UNDERWRITTEN, QuoteMode.CONTRACTED, QuoteMode.OPERATING]
)
def test_illegal_forward_jump_raises(target: QuoteMode):
    with pytest.raises(InvalidTransitionError):
        transition_quote(_draft_quote(), target)


def test_lapse_from_live_then_terminal():
    lapsed = transition_quote(_draft_quote(), QuoteMode.LAPSED, note="dropped")
    assert lapsed.quote_mode == QuoteMode.LAPSED
    assert history_modes(lapsed) == [QuoteMode.DRAFT]
    assert legal_transitions(QuoteMode.LAPSED) == set()
    with pytest.raises(InvalidTransitionError):
        transition_quote(lapsed, QuoteMode.INDICATIVE)
    with pytest.raises(InvalidTransitionError):
        transition_quote(lapsed, QuoteMode.LAPSED)


def test_append_only_does_not_mutate_prior_snapshots(rich_quote):
    q0 = _underwritable_quote(rich_quote)
    q1 = transition_quote(q0, QuoteMode.INDICATIVE, note="a")
    q2 = transition_quote(q1, QuoteMode.UNDERWRITTEN, note="b")

    assert q0.status_history == []  # untouched
    assert len(q1.status_history) == 1 and q1.quote_mode == QuoteMode.INDICATIVE
    assert len(q2.status_history) == 2 and q2.quote_mode == QuoteMode.UNDERWRITTEN
    assert [e.quote_mode for e in q2.status_history] == [QuoteMode.DRAFT, QuoteMode.INDICATIVE]


def test_t0_underwritten_guard_fires_on_transition(rich_quote):
    # INDICATIVE + T0 is allowed; the guard only fires for UNDERWRITTEN. The transition
    # re-validates, so promoting to UNDERWRITTEN must raise (a model_copy would not). N19: the
    # record carries warnings — it always does — and that no longer satisfies the guard; only the
    # specific acknowledgement does.
    q = _underwritable_quote(rich_quote, mode=QuoteMode.INDICATIVE).model_copy(
        update={
            "data_tier": DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
            "status_history": [StatusHistoryEntry(quote_mode=QuoteMode.DRAFT)],
        }
    )
    assert q.warnings  # a non-empty list used to be enough to pass the guard
    with pytest.raises(ValidationError):
        transition_quote(q, QuoteMode.UNDERWRITTEN)

    acknowledged = q.model_copy(update={"warnings": [*q.warnings, T0_UNDERWRITING_ACKNOWLEDGEMENT]})
    assert transition_quote(acknowledged, QuoteMode.UNDERWRITTEN).quote_mode == (
        QuoteMode.UNDERWRITTEN
    )


def test_t2_underwritten_is_allowed(rich_quote):
    q = _underwritable_quote(rich_quote, mode=QuoteMode.INDICATIVE).model_copy(
        update={
            "data_tier": DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
            "status_history": [StatusHistoryEntry(quote_mode=QuoteMode.DRAFT)],
        }
    )
    out = transition_quote(q, QuoteMode.UNDERWRITTEN)
    assert out.quote_mode == QuoteMode.UNDERWRITTEN


def test_transition_preserves_populated_quote_and_stamp(make_site: Callable[..., s.SiteModel]):
    """A real quote (nested optimisation_result + financial_case) survives the model_validate
    round-trip on transition with its stamp and created_at intact — the transition advances the
    lifecycle only, never re-stamps or drops the run record."""
    run = generate_quote(make_site(), load_assumptions("gb_v0_1"))
    quote = run.quote  # DRAFT, fully populated
    assert quote.optimisation_result is not None and quote.financial_case is not None

    promoted = transition_quote(quote, QuoteMode.INDICATIVE, note="promote")

    assert promoted.quote_mode == QuoteMode.INDICATIVE
    assert promoted.optimisation_result == quote.optimisation_result  # nested survived round-trip
    assert promoted.financial_case == quote.financial_case
    assert promoted.input_hash == quote.input_hash  # provenance stamp preserved
    assert promoted.code_version == quote.code_version
    assert promoted.created_at == quote.created_at  # not reset by the transition
    assert history_modes(promoted) == [QuoteMode.DRAFT]
