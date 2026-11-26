"""The two declared inputs of WP-SITE-04: the autonomy requirement and the BESS parameters.

Neither has a default for any number (the no-invented-data rule). Every element is a stated value
or `Unknown` with its reason, and every `Unknown` that blocks a step becomes a typed data ask
(`missing_requirement_asks`, `missing_bess_asks`). Decision D172 (Fable 5.2).

- `AutonomyRequirement` (``gridq.adequacy.autonomy_requirement`` 1.0.0): what load must stay up,
  for how long, judged by which statistic over the load ensemble, with what customer-stated margin.
- `BessParameters` (``gridq.adequacy.bess_parameters`` 1.0.0): usable energy inside the SoC window,
  ratings, one-way efficiencies (or a round-trip figure used as a safe-side bound), the start state
  and the state of energy the battery holds at rest.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from gridq.adequacy._common import ExactRational, NonNegInt, PosInt, RecordClass, ask
from gridq.contracts._base import ContractModel, NonEmptyStr, NotApplicable, Unknown, content_hash
from gridq.ops.dossier_model import AskHolder, DossierAsk

__all__ = [
    "AutonomyRequirement",
    "BessParameters",
    "Declarant",
    "DerivedFromStandby",
    "EnsembleMaximum",
    "ModelledSeries",
    "OneWay",
    "RoundTripOnly",
    "StatedDuration",
    "StatedKw",
    "StatedMargin",
    "StatedQuantile",
    "missing_bess_asks",
    "missing_requirement_asks",
]

REQUIREMENT_SCHEMA: Final = "gridq.adequacy.autonomy_requirement"
BESS_SCHEMA: Final = "gridq.adequacy.bess_parameters"
SCHEMA_VERSION: Final = "1.0.0"


def _unit_interval(value: ExactRational, what: str, *, allow_zero: bool = False) -> None:
    f = value.as_fraction()
    low_ok = f >= 0 if allow_zero else f > 0
    if not (low_ok and f <= 1):
        raise ValueError(f"{what} must lie in {'[0, 1]' if allow_zero else '(0, 1]'}")


# --------------------------------------------------------------------------- autonomy requirement


class StatedKw(ContractModel):
    """The customer states the protected load as a constant kW."""

    kind: Literal["stated_kw"]
    kw: PosInt


class ModelledSeries(ContractModel):
    """The protected load is a stated fraction of the load model's IT or facility series."""

    kind: Literal["modelled_series"]
    basis: Literal["it", "facility"]
    fraction: ExactRational

    @model_validator(mode="after")
    def _fraction(self) -> ModelledSeries:
        _unit_interval(self.fraction, "fraction")
        return self


class StatedDuration(ContractModel):
    """The autonomy (ride-through) the customer requires, in whole seconds."""

    kind: Literal["stated_duration"]
    seconds: PosInt


class DerivedFromStandby(ContractModel):
    """The WP-SITE-05 seam: the duration is the standby plant's handover time. Accepted by the
    schema; the floor stays indeterminate until the standby model is released."""

    kind: Literal["derived_from_standby"]
    standby_ref: NonEmptyStr


class EnsembleMaximum(ContractModel):
    """The largest window energy over every draw: the one statistic needing no risk level."""

    kind: Literal["ensemble_maximum"]


class StatedQuantile(ContractModel):
    """A customer-stated quantile across draws ("higher" method, never interpolated)."""

    kind: Literal["stated_quantile"]
    q: ExactRational
    over: Literal["draws"]

    @model_validator(mode="after")
    def _q(self) -> StatedQuantile:
        _unit_interval(self.q, "q")
        return self


class StatedMargin(ContractModel):
    """A margin the customer states: a fraction on top of the window energy, or whole kWh."""

    kind: Literal["stated_margin"]
    basis: Literal["fraction", "kwh"]
    value: ExactRational

    @model_validator(mode="after")
    def _non_negative(self) -> StatedMargin:
        if self.value.as_fraction() < 0:
            raise ValueError("a margin is not negative")
        return self


class Declarant(ContractModel):
    """Who declared the requirement. A declaration is an input, never evidence (IC-4)."""

    name: NonEmptyStr
    role: Literal["customer", "host"]
    reference: NonEmptyStr | Unknown


Coverage = Literal["ride_through_to_standby", "full_outage_autonomy"]


class AutonomyRequirement(ContractModel):
    """The customer's autonomy requirement (D172). ``coverage`` is recorded and shown;
    it does not change the arithmetic. An ``Unknown`` element blocks sizing with a data ask; a
    margin of none is declared explicitly (`NotApplicable`), never inherited as zero."""

    schema_name: Literal["gridq.adequacy.autonomy_requirement"] = REQUIREMENT_SCHEMA
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    site_id: NonEmptyStr
    record_class: RecordClass
    protected_load: StatedKw | ModelledSeries | Unknown = Field(discriminator="kind")
    coverage: Coverage | Unknown
    duration: StatedDuration | DerivedFromStandby | Unknown = Field(discriminator="kind")
    statistic: EnsembleMaximum | StatedQuantile | Unknown = Field(discriminator="kind")
    margin: StatedMargin | NotApplicable | Unknown = Field(discriminator="kind")
    declared_by: Declarant

    @model_validator(mode="after")
    def _synthetic_site(self) -> AutonomyRequirement:
        if (self.record_class == "synthetic_fixture") != self.site_id.startswith("SYNTHETIC-"):
            raise ValueError("site_id starts with SYNTHETIC- exactly when the record is synthetic")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


def missing_requirement_asks(req: AutonomyRequirement) -> tuple[tuple[str, DossierAsk], ...]:
    """``(field, ask)`` for every element that blocks sizing, in field order."""
    holder = AskHolder.HOST if req.declared_by.role == "host" else AskHolder.CUSTOMER
    out: list[tuple[str, DossierAsk]] = []
    texts = {
        "protected_load": "State the protected (critical) load the resilience reserve must carry: "
        "a constant kW, or a fraction of the modelled IT or facility load",
        "coverage": "State what the reserve covers: ride-through until standby plant takes the "
        "load, or full outage autonomy",
        "duration": "State the autonomy (ride-through) duration the reserve must provide, in "
        "seconds",
        "statistic": "State how the reserve is judged over the load ensemble: the ensemble "
        "maximum, or a stated quantile across draws",
        "margin": "State the margin on the reserve energy (a fraction or kWh), or declare that no "
        "margin applies",
    }
    for name, text in texts.items():
        if isinstance(getattr(req, name), Unknown):
            out.append((name, ask(text, site_id=req.site_id, holder=holder)))
    if isinstance(req.duration, DerivedFromStandby):
        out.append(
            (
                "duration",
                ask(
                    "Standby-plant handover time: the standby model (WP-SITE-05) is not released; "
                    "state the autonomy duration in seconds instead",
                    site_id=req.site_id,
                    holder=holder,
                ),
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- BESS parameters


class OneWay(ContractModel):
    """A one-way efficiency in (0, 1]."""

    kind: Literal["one_way"]
    eta: ExactRational

    @model_validator(mode="after")
    def _eta(self) -> OneWay:
        _unit_interval(self.eta, "eta")
        return self


class RoundTripOnly(ContractModel):
    """Only a round-trip efficiency is stated. Used as each one-way efficiency it is a provable
    lower bound (a one-way efficiency is never below the round trip), so the floor is larger and
    the stored energy smaller: safe-side, and flagged ``efficiency_bound``."""

    kind: Literal["round_trip_only"]
    eta_rt: ExactRational

    @model_validator(mode="after")
    def _eta(self) -> RoundTripOnly:
        _unit_interval(self.eta_rt, "eta_rt")
        return self


Efficiency = Annotated[OneWay | RoundTripOnly | Unknown, Field(discriminator="kind")]


def efficiency_value(e: OneWay | RoundTripOnly) -> Fraction:
    return e.eta.as_fraction() if isinstance(e, OneWay) else e.eta_rt.as_fraction()


class BessParameters(ContractModel):
    """The battery as declared (D172). ``usable_energy_kwh`` is energy inside the SoC
    window (the quantity IC-3 stacks as ``EnergyStack.total_kwh()``); state of energy is measured
    from the bottom of that window. No fade is assumed: a non end-of-life basis is flagged."""

    schema_name: Literal["gridq.adequacy.bess_parameters"] = BESS_SCHEMA
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    site_id: NonEmptyStr
    record_class: RecordClass
    usable_energy_kwh: PosInt | Unknown
    usable_basis: Literal["beginning_of_life", "end_of_life", "at_date"] | Unknown
    discharge_rating_kw: PosInt | Unknown
    charge_rating_kw: PosInt | Unknown
    discharge_efficiency: Efficiency
    charge_efficiency: Efficiency
    #: State of energy at the start of every simulated scenario.
    initial_soe_kwh: NonNegInt | Unknown
    #: State of energy the battery is recharged to and held at when no restriction is in force.
    operating_soe_kwh: NonNegInt | Unknown

    @model_validator(mode="after")
    def _consistent(self) -> BessParameters:
        if (self.record_class == "synthetic_fixture") != self.site_id.startswith("SYNTHETIC-"):
            raise ValueError("site_id starts with SYNTHETIC- exactly when the record is synthetic")
        if isinstance(self.usable_energy_kwh, int):
            for name in ("initial_soe_kwh", "operating_soe_kwh"):
                v = getattr(self, name)
                if isinstance(v, int) and v > self.usable_energy_kwh:
                    raise ValueError(f"{name} exceeds usable_energy_kwh")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


#: Fields the floor sizing needs (D172).
SIZING_FIELDS: Final = ("usable_energy_kwh", "discharge_rating_kw", "discharge_efficiency")
#: Fields the simulator needs in addition.
SIMULATION_FIELDS: Final = (
    "charge_rating_kw",
    "charge_efficiency",
    "initial_soe_kwh",
    "operating_soe_kwh",
)

_BESS_TEXT: Final = {
    "usable_energy_kwh": "State the BESS usable energy inside its state-of-charge window (kWh), "
    "at end of life where the warranty gives it",
    "discharge_rating_kw": "State the BESS discharge power rating at the connection (kW)",
    "discharge_efficiency": "State the BESS one-way discharge efficiency (or its round-trip "
    "efficiency)",
    "charge_rating_kw": "State the BESS charge power rating at the connection (kW)",
    "charge_efficiency": "State the BESS one-way charge efficiency (or its round-trip efficiency)",
    "initial_soe_kwh": "State the BESS state of energy at the start of the assessed period (kWh)",
    "operating_soe_kwh": "State the state of energy the BESS is held at when no restriction is "
    "in force (kWh)",
}


def missing_bess_asks(
    bess: BessParameters, fields: tuple[str, ...]
) -> tuple[tuple[str, DossierAsk], ...]:
    """``(field, ask)`` for every named BESS field that is `Unknown`, in the order given. The
    end-of-life and one-way efficiency asks that a stated but weaker value leaves open are added by
    the floor policy (`gridq.adequacy.floor`), beside the flag they belong to."""
    out: list[tuple[str, DossierAsk]] = []
    for name in fields:
        if isinstance(getattr(bess, name), Unknown):
            out.append((name, ask(_BESS_TEXT[name], site_id=bess.site_id)))
    return tuple(out)
