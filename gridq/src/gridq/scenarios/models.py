"""Scenario domain models — Scenario, ScenarioSet, and the Low/Central/High names.

Discrete, deterministic scenarios (no Monte Carlo). Every parameter here is either an
explicit input or sourced from the assumption library by the engine — nothing is invented.
Reuses ``GridQModel`` (``extra='forbid'`` + ``schema_version``). The external-forecast
model lives in ``forecast_reference.py``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from gridq.scenarios.forecast_reference import ForecastReference
from gridq.scenarios.weights import WEIGHT_SUM_TOL
from gridq.schemas.enums import BankableStatus
from gridq.schemas.provenance import GridQModel

# Descriptive default policy axis: no calibrated policy shift is asserted. In v1 the policy
# branch is a label only — its numeric effect, if any, must be carried by the explicit
# multipliers / COD delay, so the label can never smuggle in uncalibrated precision.
DEFAULT_POLICY_BRANCH = "unspecified"


class ScenarioName(StrEnum):
    """Canonical scenario names. ``custom`` covers anything outside the Low/Central/High set."""

    LOW = "low"
    CENTRAL = "central"
    HIGH = "high"
    CUSTOM = "custom"


class Scenario(GridQModel):
    """One discrete scenario: a named, weighted case with explicit, sourced parameters."""

    scenario_id: str
    name: ScenarioName
    probability_weight: float = Field(
        ge=0.0, le=1.0, description="Discrete probability of this case (set should sum to 1.0)."
    )
    price_multiplier: float = Field(
        default=1.0,
        gt=0.0,
        allow_inf_nan=False,
        description="Scales revenue/price-driven (operating) cashflows.",
    )
    cost_multiplier: float = Field(
        default=1.0,
        gt=0.0,
        allow_inf_nan=False,
        description="Scales capex/cost (year-0) cashflows.",
    )
    cod_delay_months: int = Field(
        default=0, ge=0, description="Months COD is delayed; affects timing only."
    )
    policy_branch: str = Field(
        default=DEFAULT_POLICY_BRANCH,
        description="Descriptive policy-axis label; carries no numeric effect on its own in v1.",
    )
    forecast_ref: ForecastReference | None = Field(
        default=None, description="Hash-pinned external forecast backing this case, if any."
    )
    notes: str = ""
    warnings: list[str] = Field(default_factory=list)


class ScenarioSet(GridQModel):
    """A complete set of discrete scenarios evaluated together.

    Carries the ``assumption_version`` it was built against so the set is traceable. The
    weight-sum check is a *warning*, not an error: the engine normalises off-sum weights
    (and also warns), so a set is never silently mis-weighted but a caller can still run.
    """

    scenario_set_id: str
    assumption_version: str
    scenarios: list[Scenario] = Field(min_length=1)
    bankable_status: BankableStatus = Field(
        default=BankableStatus.REVIEW_REQUIRED,
        description=(
            "Machine-readable bankability of the set. Scenarios never assert "
            "bankable_candidate; placeholder-driven sets are NOT_BANKABLE."
        ),
    )
    validation_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _checks(self) -> ScenarioSet:
        ids = [s.scenario_id for s in self.scenarios]
        if len(set(ids)) != len(ids):
            raise ValueError("scenario_id values must be unique within a ScenarioSet.")
        total = sum(s.probability_weight for s in self.scenarios)
        if abs(total - 1.0) > WEIGHT_SUM_TOL:
            msg = (
                f"probability weights sum to {total:.6g}, not 1.0 — the engine will normalise "
                f"them and warn; supply weights summing to 1.0 to avoid normalisation."
            )
            if msg not in self.validation_warnings:
                self.validation_warnings.append(msg)
        return self
