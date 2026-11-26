"""Theoretical-upside project: a real engine run with an illustrative route-to-market overlay.

The dispatch, sizing, priced floor and Capacity Market figures are the **real engine output**
(DemoEngine, the actual MILP). The route-to-market streams (wholesale/P415, Balancing Mechanism,
ancillary, local flexibility) cannot be priced by the engine here because their gates flow through a
``GateContext`` the app does not control (decision D12, owned by the optimisation engine). For the
Theoretical Upside Project only, this module supplies an explicit **illustrative** value for those
streams (a ballpark £/MW/yr times the engine-sized power), shown in the value stack against each
stream and badged illustrative, plus an aggregated-portfolio view. These figures are clearly
labelled illustrative, are never written into the deterministic ``QuoteRecord``, and never bankable.
"""

from __future__ import annotations

from typing import Any

#: Marker carried on the run's warnings so the results page knows to show the illustrative upside.
UPSIDE_LABEL = (
    "THEORETICAL UPSIDE (illustrative): this project assumes every route-to-market is contracted "
    "(export plus a route-to-market partner plus portfolio aggregation). The route-to-market "
    "revenues are illustrative ceilings, not engine-computed and not bankable. The dispatch, the "
    "priced floor and the Capacity Market figures are the real engine output."
)

#: MarketStream id -> illustrative gated value (GB ballpark £/MW/yr). NOT sourced, NOT bankable.
ILLUSTRATIVE_BY_ID: dict[str, float] = {
    "WHOLESALE_P415": 55000.0,
    "BALANCING_MECHANISM": 30000.0,
    "ANCILLARY_DC_DM_DR": 45000.0,
    "LOCAL_FLEX_PICLO": 12000.0,
}
#: Aggregated portfolio scale for the "contract many sites" view (MW).
_PORTFOLIO_MW = 10.0


def illustrative_stream_gbp(power_kw: float | None) -> dict[str, float]:
    """Per-stream illustrative annual £ (rate x sized MW), keyed by MarketStream id."""
    mw = (power_kw or 0.0) / 1000.0
    if mw <= 0:
        return {}
    return {sid: rate * mw for sid, rate in ILLUSTRATIVE_BY_ID.items()}


def portfolio_view(
    power_kw: float | None,
    floor_total_gbp: float | None,
    engine_upside_gbp: float | None,
) -> dict[str, Any]:
    """Theoretical totals: per-site (floor + CM + illustrative gated) and aggregated portfolio."""
    mw = (power_kw or 0.0) / 1000.0
    if mw <= 0:
        return {"available": False}
    gated_site = sum(ILLUSTRATIVE_BY_ID.values()) * mw
    per_site_total = (floor_total_gbp or 0.0) + (engine_upside_gbp or 0.0) + gated_site
    sites = _PORTFOLIO_MW / mw
    return {
        "available": True,
        "gated_site": gated_site,
        "per_site_total": per_site_total,
        "portfolio_mw": _PORTFOLIO_MW,
        "portfolio_sites": sites,
        "portfolio_gated": gated_site * sites,
        "portfolio_total": per_site_total * sites,
    }


def build_upside_project(assumption_version: str) -> str:
    """Run the curated demo through the real engine, named for Projects; return its quote id.

    Reuses the illustrative demo dataset (so the dispatch, floor and Capacity Market are the real
    engine output) and tags it with ``UPSIDE_LABEL`` so the results page shows the illustrative
    route-to-market values. Deterministic, idempotent (same inputs -> same quote id).
    """
    from gridq.app import store
    from gridq.app.demo import DEMO_DEBT_TERMS, DEMO_LABEL, DemoEngine, load_demo_site
    from gridq.assumptions import load_assumptions
    from gridq.quotes import generate_quote
    from gridq.schemas import QuoteMode

    site = load_demo_site().model_copy(
        update={"site_id": "theoretical_upside", "site_name": "Theoretical Upside Project"}
    )
    run = generate_quote(
        site,
        load_assumptions(assumption_version),
        engine=DemoEngine(),
        debt_terms=DEMO_DEBT_TERMS,
        quote_mode=QuoteMode.INDICATIVE,
        extra_warnings=[DEMO_LABEL, UPSIDE_LABEL],
    )
    # The curated demo's data under another server-chosen id: stored on the demo's attestation.
    from gridq.governance.dpa_guard import authorise_store

    store.save_run(run.quote, run.site, run.telemetry, dpa=authorise_store(run.quote.site_id))
    return run.quote.quote_id
