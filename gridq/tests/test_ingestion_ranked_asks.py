"""Ranked data ask is impact-ordered and stable (L08 · M0 ingestion).

Indicative mode "returns the ranked list of data that would most tighten the number". These tests
pin that the ranking is *principled* — it follows the documented impact priority (data tier → the
priced floor → the gates → credit → gated upside), assigns contiguous ranks, and is *stable*
(deterministic, with a documented tie-break) so the same site always yields the same precise ask.
"""

from __future__ import annotations

from gridq.ingestion import build_ranked_data_asks, rank_missing_data
from gridq.ingestion.missing_data import (
    PRIORITY_CONNECTION,
    PRIORITY_COUNTERPARTY,
    PRIORITY_DNO_REGION,
    PRIORITY_EXPORT_CONSENTS,
    PRIORITY_HALF_HOURLY,
    PRIORITY_PHYSICAL_GATES,
    PRIORITY_SUPPLIER_CONSENT,
    PRIORITY_TARIFF,
    PRIORITY_WHOLESALE_FORECAST,
    MissingDataItem,
)

# The documented impact order (missing_data.py constants → docs/parallel_development_plan.md §5):
# data tier first, then the priced floor, then the gates, then credit, then gated upside.
_DOCUMENTED_ORDER = [
    "hh_12_months",
    "tariff_stack",
    "connection_mic_mec_asc",
    "dno_region",
    "export_consents",
    "supplier_consent",
    "physical_gates",
    "counterparty_credit_status",
    "wholesale_forecast_file",
]


def test_documented_priority_constants_are_strictly_increasing() -> None:
    # The documented impact priority itself: each step is strictly lower-impact than the last.
    assert (
        PRIORITY_HALF_HOURLY
        < PRIORITY_TARIFF
        < PRIORITY_CONNECTION
        < PRIORITY_DNO_REGION
        < PRIORITY_EXPORT_CONSENTS
        < PRIORITY_SUPPLIER_CONSENT
        < PRIORITY_PHYSICAL_GATES
        < PRIORITY_COUNTERPARTY
        < PRIORITY_WHOLESALE_FORECAST
    )


def test_ranking_matches_documented_impact_priority(make_site) -> None:
    # A maximally-incomplete T0 site triggers every ask, exercising the full order.
    site = make_site(with_max_import=False)
    asks = build_ranked_data_asks(site, wholesale_requested=True)

    assert [a.rank for a in asks] == list(range(1, len(asks) + 1))  # contiguous
    assert [a.ask_id for a in asks] == _DOCUMENTED_ORDER  # impact-ordered


def test_ranking_is_stable_for_the_same_site(make_site) -> None:
    site = make_site(with_max_import=False)
    first = build_ranked_data_asks(site, wholesale_requested=True)
    second = build_ranked_data_asks(site, wholesale_requested=True)
    assert first == second


def test_equal_priority_ties_break_on_ask_id() -> None:
    # Stability with a documented tie-break: same priority → ordered by ask_id, input order
    # notwithstanding.
    def item(ask_id: str) -> MissingDataItem:
        return MissingDataItem(ask_id=ask_id, title=ask_id, why="x", category="load", priority=5)

    ranked = rank_missing_data([item("zebra"), item("alpha"), item("mike")])
    assert [a.ask_id for a in ranked] == ["alpha", "mike", "zebra"]
    assert [a.rank for a in ranked] == [1, 2, 3]


def test_missing_mic_ask_suggests_observed_peak_floor(make_site) -> None:  # type: ignore[no-untyped-def]
    # D5 (issue 018): when MIC reads 0 but the meter has an observed peak, the connection
    # data-ask suggests that peak as a FLOOR - never auto-populating MIC (a DNO fact).
    from gridq.ingestion.missing_data import detect_missing_data

    site = make_site(with_max_import=True)  # load_data.max_import_kw = 420
    site = site.model_copy(
        update={"connection": site.connection.model_copy(update={"mic_kw": 0.0})}
    )
    conn = next(i for i in detect_missing_data(site) if i.ask_id == "connection_mic_mec_asc")
    assert "observed peak 420 kW" in conn.detail
    assert "never auto-filled" in conn.detail
    assert site.connection.mic_kw == 0.0  # suggestion only; MIC is never auto-filled
