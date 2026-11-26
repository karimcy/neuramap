"""Quote as a first-class, immutable record (L07 · M1 schemas).

System Brief: "A quote is a first-class object — an immutable record per state: inputs hash, data
tier, assumption + code versions, outputs, history." These tests pin that whole contract in one
place: the record *declares* every listed field, a real run *populates* them, and advancing the
state records the prior state immutably while preserving the provenance stamp.
"""

from __future__ import annotations

import gridq.schemas as s
from gridq.quotes import transition_quote

#: The fields the System Brief names as constituting the first-class quote record. Pinned as a set
#: so dropping/renaming one on `QuoteRecord` fails this test.
_FIRST_CLASS_FIELDS = frozenset(
    {
        "input_hash",  # inputs hash
        "data_tier",  # data tier
        "assumption_version",  # assumption version
        "code_version",  # code version
        "optimisation_result",  # outputs (engine)
        "financial_case",  # outputs (finance)
        "status_history",  # history
    }
)


def test_quote_record_declares_and_populates_every_first_class_field(rich_quote) -> None:
    quote = rich_quote.quote
    assert _FIRST_CLASS_FIELDS <= set(type(quote).model_fields)  # declared on the schema

    # …and a real (populated) run actually carries them — not just declared as optional.
    assert quote.input_hash and quote.input_hash.startswith("sha256:")
    assert quote.data_tier is not None
    assert quote.assumption_version
    assert quote.code_version
    assert quote.optimisation_result is not None
    assert quote.financial_case is not None
    assert isinstance(quote.status_history, list)


def test_state_transition_is_recorded_immutably_with_stamp_preserved(rich_quote) -> None:
    quote = rich_quote.quote  # INDICATIVE, empty history
    assert quote.quote_mode == s.QuoteMode.INDICATIVE
    assert quote.status_history == []

    promoted = transition_quote(quote, s.QuoteMode.UNDERWRITTEN, note="promote")

    # The new state is recorded; the prior state is appended to history (a state transition logged).
    assert promoted.quote_mode == s.QuoteMode.UNDERWRITTEN
    assert [e.quote_mode for e in promoted.status_history] == [s.QuoteMode.INDICATIVE]
    # The original snapshot is untouched (immutable record per state).
    assert quote.status_history == []
    # The provenance stamp survives the transition (same identity, new state).
    assert promoted.input_hash == quote.input_hash
    assert promoted.assumption_version == quote.assumption_version
    assert promoted.code_version == quote.code_version
    assert promoted.created_at == quote.created_at
