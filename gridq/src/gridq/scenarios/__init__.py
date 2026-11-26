"""Scenario engine (M6).

Discrete, probability-weighted Low/Central/High price cases × policy branches, with
time-to-energisation (COD delay) as a timing axis. Deterministic and pure — no Monte
Carlo, no randomness. Scenario weights live in the assumption library; price/cost bands
are explicit input or come from a hash-pinned external forecast reference (e.g. Modo
Energy). Monte Carlo is deferred to portfolio work behind this same interface.

See docs/scenario_engine.md and docs/01_project_spec.md (M6).
"""

from __future__ import annotations

from gridq.scenarios.engine import (
    CashflowProvider,
    ScenarioEngine,
    ScenarioEvaluator,
    ScenarioResult,
    ScenarioRunResult,
    WeightedSummary,
    build_forecast_backed_scenario_set,
    build_standard_scenario_set,
    run_scenario_set,
)
from gridq.scenarios.forecast_prices import (
    ForecastPricePoint,
    ScenarioPriceSeries,
    price_series_for_set,
    scenario_price_series,
)
from gridq.scenarios.forecast_reference import (
    ForecastReference,
    build_forecast_reference,
    forecast_reference_from_market_forecast,
    require_forecast_reference,
    verify_forecast_reference,
)
from gridq.scenarios.models import (
    DEFAULT_POLICY_BRANCH,
    Scenario,
    ScenarioName,
    ScenarioSet,
)
from gridq.scenarios.weights import (
    WEIGHT_SUM_TOL,
    NormalisedWeights,
    normalise_weights,
    weights_sum_ok,
)

__all__ = [
    # models
    "Scenario",
    "ScenarioName",
    "ScenarioSet",
    "DEFAULT_POLICY_BRANCH",
    # forecast reference
    "ForecastReference",
    "build_forecast_reference",
    "forecast_reference_from_market_forecast",
    "verify_forecast_reference",
    "require_forecast_reference",
    # forecast price series (the dispatch-feed seam)
    "ForecastPricePoint",
    "ScenarioPriceSeries",
    "scenario_price_series",
    "price_series_for_set",
    # weights
    "normalise_weights",
    "weights_sum_ok",
    "NormalisedWeights",
    "WEIGHT_SUM_TOL",
    # engine
    "ScenarioEngine",
    "ScenarioEvaluator",
    "run_scenario_set",
    "build_standard_scenario_set",
    "build_forecast_backed_scenario_set",
    "ScenarioResult",
    "WeightedSummary",
    "ScenarioRunResult",
    "CashflowProvider",
]
