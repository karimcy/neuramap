"""``gridq.adequacy_result`` 1.0.0: the simulator's record (D171, D173, D174).

No floats: probabilities and expectations are `ExactRational`, energies integer joules (W x s),
durations integer seconds. The record binds its inputs by hash, embeds the floor policy and the
sign-off outcome, and carries two hashes:

- ``simulation_hash``: everything the physics and the statistics depend on, *excluding* the
  approval, its date and its label. A signed and an unsigned run of the same inputs share it.
- `AdequacyResult.content_hash`: the whole record, approval included.

The validator re-runs the sign-off admission on the embedded evidence: an `Approved` that the bar
would not produce is refused, so a hand-built "approved" record cannot exist. The approval label
comes from one closed table (`approval_label`); an unsigned floor never reads as approved.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import Field, model_validator

from gridq.adequacy._common import ExactRational, NonNegInt, PosInt, RecordClass
from gridq.adequacy.floor import (
    Approved,
    ResilienceFloorPolicy,
    Unsigned,
    admit_floor_signoff,
    approval_label,
    floor_statement_hash,
)
from gridq.adequacy.load import LoadAnchor, LoadIdentity, LoadMapping
from gridq.adequacy.thermal import FlexCall
from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    Unknown,
    content_hash,
)
from gridq.ops.dossier_model import DossierAsk

__all__ = [
    "AdequacyConfig",
    "AdequacyResult",
    "Computed",
    "Conformance",
    "InputBinding",
    "NotComputed",
    "PostureRun",
    "RESULT_SCHEMA",
    "StatedImportCapacity",
    "simulation_hash_of",
]

RESULT_SCHEMA: Final = "gridq.adequacy_result"
METHOD_VERSION: Final = "gridq.adequacy/1.0.0"
KERNEL_VERSION: Final = "gridq.adequacy.kernel/1.0.0"
INTERVAL_SCOPE: Final = "load_draw_sampling_error_only"
WARNINGS: tuple[str, ...] = (
    "Intervals cover load-draw sampling error only; scenario weights, the load model's structure "
    "and the battery parameters are taken as given.",
    "Draws are paired across scenarios (the cross product): per-draw totals are not independent "
    "of the scenario set.",
    "Thermal flex is read from IC-11 on its own terms; a modelled surface is not a measured one.",
)


class StatedImportCapacity(ContractModel):
    """The import capacity the restriction depth is measured below, stated by the caller when the
    scenario set's ``reference_capacity_mw`` is ``Unknown``."""

    kind: Literal["stated_import_capacity"]
    kw: PosInt
    source: NonEmptyStr


class AdequacyConfig(ContractModel):
    resolution_s: Literal[60, 1]
    seed: NonNegInt
    confidence_ppm: Annotated[int, Field(gt=0, lt=1_000_000)] = 950_000
    unserved_threshold_w: PosInt = 1
    charge_during_restriction: bool = True
    import_capacity: StatedImportCapacity | NotApplicable = Field(discriminator="kind")
    anchors: tuple[LoadAnchor, ...] = ()


class InputBinding(ContractModel):
    scenario_set_hash: Sha256
    thermal_flex_hash: Sha256 | NotApplicable
    envelope_hash: Sha256 | NotApplicable
    load: LoadIdentity
    floor_policy_hash: Sha256
    bess_parameters_hash: Sha256
    config_hash: Sha256
    seed: NonNegInt
    draws: PosInt
    resolution_s: Literal[60, 1]
    kernel_version: Literal["gridq.adequacy.kernel/1.0.0"]


class Interval(ContractModel):
    lo: ExactRational
    hi: ExactRational


class EventLolp(ContractModel):
    scenario_id: NonEmptyStr
    #: The event index, or ``outside_event`` for steps before a scenario's first event.
    event: NonNegInt | Literal["outside_event"]
    #: Draws with an unserved step attributed to this event, out of ``draws``.
    k: NonNegInt


class Lolp(ContractModel):
    """Probability that a path (scenario, draw) has any unserved step, weighted over scenarios."""

    point: ExactRational
    interval: Interval
    expected_loss_seconds: ExactRational
    per_event: tuple[EventLolp, ...]


class Eue(ContractModel):
    """Expected unserved energy, joules (W x s), over each scenario period."""

    expected_ws: ExactRational
    per_year_ws: ExactRational | NotApplicable
    p50_ws: ExactRational
    p95_ws: ExactRational
    p95_rank_interval: tuple[PosInt, PosInt] | NotApplicable
    bootstrap: Interval


class MarginAt(ContractModel):
    scenario_id: NonEmptyStr
    draw: NonNegInt
    offset_s: NonNegInt


class ScenarioMargin(ContractModel):
    scenario_id: NonEmptyStr
    min_margin_j: int


class Margin(ContractModel):
    """The least state of energy above the resilience floor, joules. Negative only when the
    battery starts (or a floor step-up finds it) below the floor: it is never discharged there."""

    min_margin_j: int
    attained_at: MarginAt
    per_scenario: tuple[ScenarioMargin, ...]


class WorstGap(ContractModel):
    scenario_id: NonEmptyStr
    gap_index: NonNegInt
    k_infeasible: NonNegInt


class Recharge(ContractModel):
    """After each (merged) event the battery recharges toward the level it entered at (capped at
    the held level); feasible when it gets there before the next event starts."""

    gaps: NonNegInt
    k_infeasible: NonNegInt
    k_censored: NonNegInt
    min_margin_s: NonNegInt | NotApplicable
    max_shortfall_wh: NonNegInt
    p_any_infeasible_gap: ExactRational
    p_any_infeasible_gap_interval: Interval
    worst_gap: WorstGap | NotApplicable


class PostureRun(ContractModel):
    posture: Literal["reserve_held", "envelope_adversary"]
    thermal: Literal["off", "ic11"]
    bound: Literal["no_trading", "every_envelope_compliant_dispatch"]
    lolp: Lolp
    eue: Eue
    margin: Margin
    recharge: Recharge | NotApplicable
    paths_digests: tuple[tuple[NonEmptyStr, Sha256], ...]
    floor_breaches: Literal[0]


class Finding(ContractModel):
    code: Literal[
        "ENVELOPE_FLOOR_BELOW_POLICY",
        "ENVELOPE_RESILIENCE_KW_BELOW_PROTECTED_LOAD",
        "ENVELOPE_FAIL_SAFE_FLOOR_BELOW_POLICY",
    ]
    detail: NonEmptyStr


class Conformance(ContractModel):
    kind: Literal["conformance"]
    status: Literal["conforming", "nonconforming"]
    findings: tuple[Finding, ...]

    @model_validator(mode="after")
    def _status(self) -> Conformance:
        if (self.status == "nonconforming") != bool(self.findings):
            raise ValueError("an envelope is nonconforming exactly when there is a finding")
        return self


class Computed(ContractModel):
    kind: Literal["computed"]
    runs: tuple[PostureRun, ...] = Field(min_length=1)
    headline: tuple[Literal["reserve_held", "envelope_adversary"], Literal["off", "ic11"]]
    mappings: tuple[LoadMapping, ...]
    flex_calls: tuple[tuple[NonEmptyStr, tuple[FlexCall, ...]], ...]
    confidence_ppm: int
    interval_scope: Literal["load_draw_sampling_error_only"]
    warnings: tuple[NonEmptyStr, ...]


class NotComputed(ContractModel):
    """No LOLP or EUE: the inputs are not all there. ``reasons`` name them; the asks close them."""

    kind: Literal["not_computed"]
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)


class AdequacyResult(ContractModel):
    schema_name: Literal["gridq.adequacy_result"] = RESULT_SCHEMA
    schema_version: Literal["1.0.0"] = "1.0.0"
    method_version: Literal["gridq.adequacy/1.0.0"] = METHOD_VERSION
    site_id: NonEmptyStr
    record_class: RecordClass
    inputs: InputBinding | NotApplicable
    config: AdequacyConfig
    policy: ResilienceFloorPolicy
    conformance: Conformance | NotApplicable
    outcome: Computed | NotComputed = Field(discriminator="kind")
    data_requests: tuple[DossierAsk, ...]
    simulation_hash: Sha256
    # ----- excluded from simulation_hash
    as_of: IsoDate
    approval: Approved | Unsigned = Field(discriminator="kind")
    approval_label: NonEmptyStr
    #: Reads as approved only when the floor is signed, the envelope (if any) conforms, and the
    #: run was computed.
    approved_for_use: bool

    @model_validator(mode="after")
    def _consistent(self) -> AdequacyResult:
        check_result(self, simulation_hash_of(self), extra_ok=True)
        return self

    def content_hash(self) -> str:
        return content_hash(self)


def check_result(r: Any, sim_hash: str, *, extra_ok: bool) -> None:
    """The checks every ``gridq.adequacy_result`` version shares (1.0.0 and 1.1.0): the simulation
    hash, the sign-off re-admission, the closed label table, the derived ``approved_for_use``
    (also requiring ``extra_ok``) and the asks."""
    if r.simulation_hash != sim_hash:
        raise ValueError("simulation_hash does not match the record")
    if r.approval.as_of != r.as_of:
        raise ValueError("the approval is dated the run's as_of")
    if r.approval_label != approval_label(r.approval):
        raise ValueError("approval_label must come from the closed table")
    sh = floor_statement_hash(r.policy)
    if isinstance(r.approval, Approved):
        again = admit_floor_signoff(r.policy, (r.approval.evidence,), as_of=r.as_of)
        if again != r.approval:
            raise ValueError(
                "the embedded approval is not what the sign-off bar admits for this policy"
            )
    elif isinstance(sh, Unknown) != isinstance(r.approval.statement_hash, Unknown) or (
        not isinstance(sh, Unknown) and sh != r.approval.statement_hash
    ):
        raise ValueError("the unsigned outcome names a different floor statement")
    expected = (
        isinstance(r.approval, Approved)
        and isinstance(r.outcome, Computed)
        and not (isinstance(r.conformance, Conformance) and r.conformance.status == "nonconforming")
        and extra_ok
    )
    if r.approved_for_use != expected:
        raise ValueError("approved_for_use is derived, never set")
    ids = [a.ask_id for a in r.data_requests]
    if ids != sorted(set(ids)):
        raise ValueError("data_requests are sorted by ask_id, without repeats")
    if isinstance(r.outcome, NotComputed) and not r.data_requests:
        raise ValueError("a run that is not computed says what is missing")


_EXCLUDED = frozenset(
    {"simulation_hash", "as_of", "approval", "approval_label", "approved_for_use"}
)


class _SimulationView(ContractModel):
    schema_name: str
    schema_version: str
    method_version: str
    site_id: str
    record_class: str
    inputs: InputBinding | NotApplicable
    config: AdequacyConfig
    policy: ResilienceFloorPolicy
    conformance: Conformance | NotApplicable
    outcome: Computed | NotComputed = Field(discriminator="kind")
    data_requests: tuple[DossierAsk, ...]


def simulation_hash_of(result: AdequacyResult | dict[str, object]) -> str:
    """The hash of everything but the approval (see module docstring)."""
    if isinstance(result, AdequacyResult):
        fields = {k: getattr(result, k) for k in _SimulationView.model_fields}
    else:
        fields = {k: v for k, v in result.items() if k not in _EXCLUDED}
    return content_hash(_SimulationView(**fields))
