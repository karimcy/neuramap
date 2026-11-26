"""Scenario engine (M6) — deterministic Low/Central/High evaluation over a base case.

Takes a base ``FinancialCase`` (or any object exposing ``p50_cashflows`` / ``p90_cashflows``)
and a ``ScenarioSet``, and returns one ``ScenarioResult`` per scenario plus a
probability-weighted summary. It is pure and deterministic: no randomness, no Monte Carlo
(deferred to portfolio work behind this same interface — see docs/scenario_engine.md).

What it does, and deliberately does NOT do:
- Scales each scenario's cashflows by its *explicit* multipliers — ``cost_multiplier`` on
  the year-0 capex outflow, ``price_multiplier`` on operating-year cashflows (a first-order
  price/revenue sensitivity on net operating cashflow — honest coarseness, not a split it
  cannot justify from a net series).
- Shifts timing for whole-year COD delays; warns and leaves timing unchanged for sub-annual
  delays (v1 models COD at annual granularity only).
- Never invents a price spread or a forecast: bands come from explicit input or a
  hash-pinned ``ForecastReference``. It does not compute IRR/NPV/discounting — that is the
  finance module's job; here outputs are undiscounted cashflows + simple weighted totals.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import Field

from gridq.assumptions import AssumptionLibrary, AssumptionStatus
from gridq.scenarios.forecast_reference import (
    ForecastReference,
    _load_forecast_file,
    forecast_reference_from_market_forecast,
)
from gridq.scenarios.models import (
    DEFAULT_POLICY_BRANCH,
    Scenario,
    ScenarioName,
    ScenarioSet,
)
from gridq.scenarios.weights import normalise_weights
from gridq.schemas.enums import BankableStatus
from gridq.schemas.provenance import GridQModel

MONTHS_PER_YEAR = 12

_COARSE_NOTE = (
    "Scenario outputs are undiscounted cashflows + weighted totals only (no IRR/NPV here) "
    "and inherit the base case's bankability — they are not a valuation."
)


@runtime_checkable
class CashflowProvider(Protocol):
    """Minimal shape the engine needs from a base case (``FinancialCase`` satisfies it)."""

    p50_cashflows: list[float]
    p90_cashflows: list[float]


class ScenarioResult(GridQModel):
    """The evaluated outcome for one scenario."""

    scenario_id: str
    name: ScenarioName
    probability_weight: float
    applied_weight: float = Field(description="Weight after normalisation across the set.")
    price_multiplier: float
    cost_multiplier: float
    cod_delay_months: int
    policy_branch: str
    p50_cashflows: list[float]
    p90_cashflows: list[float]
    total_p50_gbp: float = Field(description="Undiscounted sum of P50 cashflows (coarse).")
    total_p90_gbp: float = Field(description="Undiscounted sum of P90 cashflows (coarse).")
    forecast_ref: ForecastReference | None = None
    warnings: list[str] = Field(default_factory=list)


class WeightedSummary(GridQModel):
    """Probability-weighted aggregate across all scenarios."""

    weighted_p50_cashflows: list[float]
    weighted_p90_cashflows: list[float]
    weighted_total_p50_gbp: float
    weighted_total_p90_gbp: float
    weights_normalised: bool
    original_weight_sum: float
    warnings: list[str] = Field(default_factory=list)


class ScenarioRunResult(GridQModel):
    """The full deterministic output of one scenario-engine run."""

    scenario_set_id: str
    assumption_version: str
    results: list[ScenarioResult]
    weighted_summary: WeightedSummary
    bankable_status: BankableStatus = Field(
        default=BankableStatus.NOT_BANKABLE,
        description=(
            "Machine-readable bankability of this run (never bankable_candidate from "
            "scenarios alone; NOT_BANKABLE on placeholder weights or an unverified forecast)."
        ),
    )
    warnings: list[str] = Field(default_factory=list)


def _apply_multipliers(
    cashflows: list[float], *, price_multiplier: float, cost_multiplier: float
) -> tuple[list[float], list[str]]:
    """Year 0 (capex) scales by ``cost_multiplier``; operating years by ``price_multiplier``."""
    warnings: list[str] = []
    if not cashflows:
        return [], ["base case has no cashflows; nothing to scale."]
    if any(not math.isfinite(cf) for cf in cashflows):
        warnings.append(
            "base case contains non-finite cashflows (inf/nan); scenario totals will be "
            "non-finite — fix the base case before relying on the output."
        )
    if cashflows[0] > 0:
        warnings.append(
            "year-0 cashflow is non-negative — expected a capex outflow; cost_multiplier "
            "still applied to year 0 (confirm the base case's cashflow convention)."
        )
    if len(cashflows) == 1 and price_multiplier != 1.0:
        warnings.append(
            "base case has no operating-year cashflows; price_multiplier had no effect."
        )
    out = [cf * (cost_multiplier if i == 0 else price_multiplier) for i, cf in enumerate(cashflows)]
    return out, warnings


def _shift_for_cod(cashflows: list[float], cod_delay_months: int) -> tuple[list[float], list[str]]:
    """Shift operating cashflows later for a whole-year COD delay (timing only); else warn."""
    if cod_delay_months == 0:
        return list(cashflows), []
    if cod_delay_months % MONTHS_PER_YEAR != 0:
        return list(cashflows), [
            f"cod_delay_months={cod_delay_months} is not a whole number of years; v1 models "
            f"COD timing at annual granularity only — operating cashflows left un-shifted "
            f"(flag as an approximation until sub-annual timing is modelled)."
        ]
    if len(cashflows) <= 1:
        return list(cashflows), []
    delay_years = cod_delay_months // MONTHS_PER_YEAR
    capex = cashflows[0]
    operating = cashflows[1:]
    shifted = ([0.0] * delay_years + operating)[: len(operating)]
    return [capex, *shifted], [
        f"COD delayed {delay_years} year(s): operating cashflows shifted later and revenue "
        f"beyond the modelled horizon is dropped (deferred, not created)."
    ]


def _evaluate_scenario(
    base_p50: list[float], base_p90: list[float], scenario: Scenario, *, applied_weight: float
) -> ScenarioResult:
    """Apply one scenario to the base cashflows, deterministically, collecting warnings."""
    warnings: list[str] = list(scenario.warnings)

    p50, w1 = _apply_multipliers(
        base_p50,
        price_multiplier=scenario.price_multiplier,
        cost_multiplier=scenario.cost_multiplier,
    )
    p90, _ = _apply_multipliers(
        base_p90,
        price_multiplier=scenario.price_multiplier,
        cost_multiplier=scenario.cost_multiplier,
    )
    warnings.extend(w1)

    p50, c1 = _shift_for_cod(p50, scenario.cod_delay_months)
    p90, _ = _shift_for_cod(p90, scenario.cod_delay_months)
    warnings.extend(c1)

    if scenario.policy_branch != DEFAULT_POLICY_BRANCH:
        warnings.append(
            f"policy_branch {scenario.policy_branch!r} is descriptive only in v1 — it changes "
            f"no number by itself; any policy effect must be carried by the explicit "
            f"multipliers / COD delay, which must be sourced (no fake precision from the label)."
        )

    if scenario.forecast_ref is not None and not scenario.forecast_ref.verified:
        warnings.append(
            f"forecast_ref {scenario.forecast_ref.forecast_version!r} is unverified — its hash "
            f"was not confirmed against the real file; its prices are not bankable-backing "
            f"until verify_forecast_reference passes."
        )

    return ScenarioResult(
        scenario_id=scenario.scenario_id,
        name=scenario.name,
        probability_weight=scenario.probability_weight,
        applied_weight=applied_weight,
        price_multiplier=scenario.price_multiplier,
        cost_multiplier=scenario.cost_multiplier,
        cod_delay_months=scenario.cod_delay_months,
        policy_branch=scenario.policy_branch,
        p50_cashflows=p50,
        p90_cashflows=p90,
        total_p50_gbp=sum(p50),
        total_p90_gbp=sum(p90),
        forecast_ref=scenario.forecast_ref,
        warnings=warnings,
    )


def _weighted_series(series: list[list[float]], weights: tuple[float, ...]) -> list[float]:
    """Element-wise probability-weighted sum of equal-or-ragged cashflow series (zero-padded)."""
    if not series:
        return []
    if len(weights) < len(series):
        raise ValueError(
            f"need a weight per series: got {len(weights)} weights for {len(series)} series."
        )
    horizon = max(len(s) for s in series)
    return [
        sum(weights[i] * (series[i][t] if t < len(series[i]) else 0.0) for i in range(len(series)))
        for t in range(horizon)
    ]


@runtime_checkable
class ScenarioEvaluator(Protocol):
    """The scenario seam: evaluate a base case over a set, return a `ScenarioRunResult`.

    This is the interface the System Brief reserves for Monte Carlo: a portfolio MC evaluator
    (sampling distributions rather than discrete Low/Central/High cases) drops in by implementing
    this one method and returning the same `ScenarioRunResult`, so callers — which depend on this
    protocol and that result, not on `ScenarioEngine` — are unchanged. `ScenarioEngine` (the v1
    discrete evaluator) satisfies it structurally.
    """

    def run(self, base_case: CashflowProvider, scenario_set: ScenarioSet) -> ScenarioRunResult: ...


class ScenarioEngine:
    """Deterministic discrete-scenario evaluator. No randomness, no Monte Carlo.

    The v1 implementation of the `ScenarioEvaluator` seam (Monte Carlo is deferred to portfolio
    work behind that same interface).
    """

    def run(self, base_case: CashflowProvider, scenario_set: ScenarioSet) -> ScenarioRunResult:
        """Evaluate every scenario against ``base_case`` and weight the results."""
        if not hasattr(base_case, "p50_cashflows") or not hasattr(base_case, "p90_cashflows"):
            raise TypeError(
                "base_case must expose p50_cashflows and p90_cashflows (e.g. a FinancialCase)."
            )
        base_p50 = list(base_case.p50_cashflows)
        base_p90 = list(base_case.p90_cashflows)

        norm = normalise_weights([s.probability_weight for s in scenario_set.scenarios])
        results = [
            _evaluate_scenario(base_p50, base_p90, scenario, applied_weight=norm.weights[i])
            for i, scenario in enumerate(scenario_set.scenarios)
        ]

        weighted_p50 = _weighted_series([r.p50_cashflows for r in results], norm.weights)
        weighted_p90 = _weighted_series([r.p90_cashflows for r in results], norm.weights)
        summary = WeightedSummary(
            weighted_p50_cashflows=weighted_p50,
            weighted_p90_cashflows=weighted_p90,
            weighted_total_p50_gbp=sum(weighted_p50),
            weighted_total_p90_gbp=sum(weighted_p90),
            weights_normalised=norm.normalised,
            original_weight_sum=norm.original_sum,
            warnings=list(norm.warnings),
        )

        # Bankability: scenarios never assert bankable_candidate; downgrade to NOT_BANKABLE
        # whenever a scenario carries an unverified forecast reference.
        status = scenario_set.bankable_status
        if status == BankableStatus.BANKABLE_CANDIDATE:
            status = BankableStatus.REVIEW_REQUIRED
        if any(r.forecast_ref is not None and not r.forecast_ref.verified for r in results):
            status = BankableStatus.NOT_BANKABLE

        run_warnings = [_COARSE_NOTE, *scenario_set.validation_warnings]
        return ScenarioRunResult(
            scenario_set_id=scenario_set.scenario_set_id,
            assumption_version=scenario_set.assumption_version,
            results=results,
            weighted_summary=summary,
            bankable_status=status,
            warnings=run_warnings,
        )


def run_scenario_set(base_case: CashflowProvider, scenario_set: ScenarioSet) -> ScenarioRunResult:
    """Functional entry point — equivalent to ``ScenarioEngine().run(...)``."""
    return ScenarioEngine().run(base_case, scenario_set)


def build_standard_scenario_set(
    assumptions: AssumptionLibrary,
    *,
    scenario_set_id: str = "low_central_high",
    low_price_multiplier: float | None = None,
    high_price_multiplier: float | None = None,
    low_cost_multiplier: float | None = None,
    high_cost_multiplier: float | None = None,
    forecast_ref: ForecastReference | None = None,
    policy_branch: str = DEFAULT_POLICY_BRANCH,
    cod_delay_months: int | None = None,
) -> ScenarioSet:
    """Build a Low/Central/High set: weights FROM the assumption library, bands from input.

    ``cod_delay_months`` (WP-UW-17, D130) adds a fourth, ZERO-WEIGHT ``cod_delay`` row
    (CUSTOM, identity multipliers) that stresses the stated delay without moving the weighted
    case; ``None`` adds no row and no delay is assumed.

    - Probability weights come from ``scenario_weights.central_low_high`` in ``assumptions``.
    - Central is the base case (identity multipliers 1.0).
    - Low/High price & cost multipliers are *explicit input*. When a band is not supplied it
      defaults to identity (1.0) and the scenario carries a warning — a spread is **never**
      fabricated (supply a real band or a hash-pinned ``forecast_ref`` instead).
    - If the weights assumption is a placeholder/needs_review, the set is flagged not-bankable.
    """
    try:
        weights_assumption = assumptions.scenario_weights["central_low_high"]
    except KeyError as exc:
        raise ValueError(
            "assumption library is missing scenario_weights.central_low_high — cannot build the "
            "standard Low/Central/High set; add the weights assumption or pass a ScenarioSet "
            "explicitly."
        ) from exc
    weight_values = weights_assumption.value
    if not isinstance(weight_values, dict):
        raise ValueError(
            "scenario_weights.central_low_high.value must be a mapping of low/central/high."
        )
    for key in ("low", "central", "high"):
        if key not in weight_values:
            raise ValueError(f"scenario_weights.central_low_high is missing the {key!r} weight.")

    is_placeholder = weights_assumption.status in (
        AssumptionStatus.PLACEHOLDER,
        AssumptionStatus.NEEDS_REVIEW,
    )
    bankable_status = (
        BankableStatus.NOT_BANKABLE if is_placeholder else BankableStatus.REVIEW_REQUIRED
    )
    set_warnings: list[str] = []
    if is_placeholder:
        set_warnings.append(
            f"scenario weights come from a {weights_assumption.status.value} assumption "
            f"(confidence {weights_assumption.confidence.value}) — not bankable until reviewed."
        )

    def _band_warnings(band: str, price_m: float | None, cost_m: float | None) -> list[str]:
        """Per-dimension data-ask warnings — only call it 'equals Central' when BOTH default."""
        if price_m is None and cost_m is None:
            return [
                f"no {band} sensitivity supplied — price and cost both held at identity (1.0), "
                f"so this case equals Central. Supply a real {band} band or a hash-pinned "
                f"forecast reference (ranked data ask — a spread is not fabricated here)."
            ]
        notes: list[str] = []
        if price_m is None:
            notes.append(
                f"no {band} PRICE band supplied — price held at identity (1.0); confirm there "
                f"is no price sensitivity or supply a real band / forecast reference."
            )
        if cost_m is None:
            notes.append(
                f"no {band} COST band supplied — cost held at identity (1.0); confirm there is "
                f"no cost sensitivity or supply a real band."
            )
        return notes

    low_price = low_price_multiplier if low_price_multiplier is not None else 1.0
    high_price = high_price_multiplier if high_price_multiplier is not None else 1.0
    low_cost = low_cost_multiplier if low_cost_multiplier is not None else 1.0
    high_cost = high_cost_multiplier if high_cost_multiplier is not None else 1.0

    low_warnings = _band_warnings("low", low_price_multiplier, low_cost_multiplier)
    high_warnings = _band_warnings("high", high_price_multiplier, high_cost_multiplier)

    scenarios = [
        Scenario(
            scenario_id="low",
            name=ScenarioName.LOW,
            probability_weight=float(weight_values["low"]),
            price_multiplier=low_price,
            cost_multiplier=low_cost,
            policy_branch=policy_branch,
            forecast_ref=forecast_ref,
            warnings=low_warnings,
        ),
        Scenario(
            scenario_id="central",
            name=ScenarioName.CENTRAL,
            probability_weight=float(weight_values["central"]),
            price_multiplier=1.0,
            cost_multiplier=1.0,
            policy_branch=policy_branch,
            forecast_ref=forecast_ref,
        ),
        Scenario(
            scenario_id="high",
            name=ScenarioName.HIGH,
            probability_weight=float(weight_values["high"]),
            price_multiplier=high_price,
            cost_multiplier=high_cost,
            policy_branch=policy_branch,
            forecast_ref=forecast_ref,
            warnings=high_warnings,
        ),
    ]

    if cod_delay_months:
        scenarios.append(
            Scenario(
                scenario_id="cod_delay",
                name=ScenarioName.CUSTOM,
                probability_weight=0.0,
                cod_delay_months=cod_delay_months,
                policy_branch=policy_branch,
                forecast_ref=forecast_ref,
                notes=f"COD delay +{cod_delay_months}m",
            )
        )

    return ScenarioSet(
        scenario_set_id=scenario_set_id,
        assumption_version=assumptions.metadata.version,
        scenarios=scenarios,
        bankable_status=bankable_status,
        validation_warnings=set_warnings,
    )


def build_forecast_backed_scenario_set(
    assumptions: AssumptionLibrary,
    *,
    forecast_file: str | Path | None = None,
    source: str | None = None,
    forecast_version: str | None = None,
    created_at: datetime | None = None,
    coverage_start: date | None = None,
    coverage_end: date | None = None,
    price_unit: str = "GBP/MWh",
    scenario_set_id: str = "low_central_high",
    low_price_multiplier: float | None = None,
    high_price_multiplier: float | None = None,
    low_cost_multiplier: float | None = None,
    high_cost_multiplier: float | None = None,
    policy_branch: str = DEFAULT_POLICY_BRANCH,
) -> ScenarioSet:
    """Build a Low/Central/High set backed by a REAL, hash-pinned Modo/wholesale forecast file.

    Loads ``forecast_file`` through the ingestion loaders, pins it as a ``ForecastReference``
    (content + file hash, recomputed from the curve — never trusted verbatim), and attaches that
    reference to every band so each scenario is traceable to the exact forecast. The per-scenario
    price *series* is then resolvable for the dispatch engine via
    :func:`gridq.scenarios.forecast_prices.scenario_price_series`.

    The forecast pins provenance and feeds the dispatch price series; it does **not** set the
    ``price_multiplier`` — Low/High multipliers stay explicit, sourced caller input (a spread is
    never derived from, nor invented for, the file; decision D20). Weights come from the assumption
    library exactly as in :func:`build_standard_scenario_set`.

    Degrades honestly: a missing/``None``/unreadable ``forecast_file`` returns a normal identity
    set carrying **no** ``forecast_ref`` plus a ranked data-ask warning — never a raise, never a
    guessed price reference.
    """
    base_kwargs: dict[str, object] = {
        "scenario_set_id": scenario_set_id,
        "low_price_multiplier": low_price_multiplier,
        "high_price_multiplier": high_price_multiplier,
        "low_cost_multiplier": low_cost_multiplier,
        "high_cost_multiplier": high_cost_multiplier,
        "policy_branch": policy_branch,
    }

    if forecast_file is None:
        scn = build_standard_scenario_set(assumptions, **base_kwargs)  # type: ignore[arg-type]
        scn.validation_warnings.append(
            "no Modo/wholesale forecast file supplied — scenarios carry no hash-pinned price "
            "reference and the dispatch engine has no forecast price series. ranked data ask: "
            "supply a real forecast file to pin one; price multipliers stay explicit and no "
            "spread is fabricated."
        )
        return scn

    from gridq.ingestion.market_forecasts import MarketForecastError

    path = Path(forecast_file)
    try:
        forecast = _load_forecast_file(
            path, source=source, forecast_version=forecast_version, created_at=created_at
        )
        forecast_ref = forecast_reference_from_market_forecast(
            forecast,
            price_unit=price_unit,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )
    except (FileNotFoundError, MarketForecastError, ValueError) as exc:
        scn = build_standard_scenario_set(assumptions, **base_kwargs)  # type: ignore[arg-type]
        scn.validation_warnings.append(
            f"forecast file {path} could not be loaded/pinned ({exc}); scenarios carry no price "
            f"reference. ranked data ask: supply a valid Modo forecast file (no spread fabricated)."
        )
        return scn

    return build_standard_scenario_set(
        assumptions,
        forecast_ref=forecast_ref,
        **base_kwargs,  # type: ignore[arg-type]
    )
