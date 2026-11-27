"""``gridq.adequacy_result`` 1.1.0: the simulator's record with on-site generation (D213).

A sibling of the 1.0.0 `AdequacyResult`, never a subclass, so 1.0.0 is byte-for-byte unchanged and
is still what `run_adequacy` returns without ``generation=``. 1.1.0 adds:

- `InputBindingV1_1`: the 1.0.0 binding plus ``generation_hash`` (the declared model's hash);
- `GenerationSection`: the declared model, the credit marker (``full`` / ``partial`` / ``none``),
  every scenario's plan (runs, limits, stores, findings), the exclusions, and the run *without*
  on-site generation: its ``simulation_hash`` (the 1.0.0 run, actually re-run) and headline LOLP
  and EUE, so the value of the plant is read from two hashed runs, not asserted.

``method_version`` is ``gridq.adequacy/1.1.0``; the kernel is unchanged. ``simulation_hash`` has the
1.0.0 exclusions over a 1.1.0 view. ``approved_for_use`` additionally needs no nonconforming
generation finding (a test schedule that a run-hour limit cannot hold).
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, model_validator

from gridq.adequacy._common import ExactRational, RecordClass
from gridq.adequacy.floor import Approved, ResilienceFloorPolicy, Unsigned
from gridq.adequacy.onsite import OnsiteGenerationModel
from gridq.adequacy.result import (
    AdequacyConfig,
    Computed,
    Conformance,
    InputBinding,
    NotComputed,
    check_result,
)
from gridq.adequacy.standby import GenerationFinding, ScenarioGeneration
from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    content_hash,
)
from gridq.ops.dossier_model import DossierAsk

__all__ = [
    "AdequacyResultV1_1",
    "GenerationSection",
    "InputBindingV1_1",
    "METHOD_VERSION_V1_1",
    "WithoutGeneration",
    "simulation_hash_v1_1",
]

METHOD_VERSION_V1_1: Final = "gridq.adequacy/1.1.0"


class InputBindingV1_1(InputBinding):
    """The 1.0.0 binding plus the on-site generation model's hash."""

    generation_hash: Sha256


class WithoutGeneration(ContractModel):
    """The same inputs run without on-site generation (the 1.0.0 run), at its headline."""

    kind: Literal["without_generation"]
    simulation_hash: Sha256
    headline_lolp: ExactRational
    headline_eue_ws: ExactRational


class GenerationSection(ContractModel):
    generation_hash: Sha256
    model: OnsiteGenerationModel
    credit: Literal["full", "partial", "none"]
    status: Literal["conforming", "nonconforming"]
    plans: tuple[ScenarioGeneration, ...]
    exclusions: tuple[NonEmptyStr, ...]
    without_generation: WithoutGeneration | NotApplicable = Field(discriminator="kind")

    @model_validator(mode="after")
    def _consistent(self) -> GenerationSection:
        if self.model.content_hash() != self.generation_hash:
            raise ValueError("generation_hash is the embedded model's content hash")
        bad = any(f.severity == "nonconforming" for f in self.findings())
        if (self.status == "nonconforming") != bad:
            raise ValueError("generation is nonconforming exactly when a plan finding is")
        return self

    def findings(self) -> tuple[GenerationFinding, ...]:
        return tuple(f for p in self.plans for f in p.findings)


class AdequacyResultV1_1(ContractModel):
    schema_name: Literal["gridq.adequacy_result"] = "gridq.adequacy_result"
    schema_version: Literal["1.1.0"] = "1.1.0"
    method_version: Literal["gridq.adequacy/1.1.0"] = METHOD_VERSION_V1_1
    site_id: NonEmptyStr
    record_class: RecordClass
    inputs: InputBindingV1_1 | NotApplicable
    config: AdequacyConfig
    policy: ResilienceFloorPolicy
    conformance: Conformance | NotApplicable
    outcome: Computed | NotComputed = Field(discriminator="kind")
    generation: GenerationSection
    data_requests: tuple[DossierAsk, ...]
    simulation_hash: Sha256
    # ----- excluded from simulation_hash
    as_of: IsoDate
    approval: Approved | Unsigned = Field(discriminator="kind")
    approval_label: NonEmptyStr
    approved_for_use: bool

    @model_validator(mode="after")
    def _consistent(self) -> AdequacyResultV1_1:
        check_result(
            self,
            simulation_hash_v1_1(self),
            extra_ok=self.generation.status == "conforming",
        )
        if (
            isinstance(self.inputs, InputBindingV1_1)
            and self.inputs.generation_hash != self.generation.generation_hash
        ):
            raise ValueError("the binding and the generation section name different models")
        if isinstance(self.outcome, Computed) != isinstance(
            self.generation.without_generation, WithoutGeneration
        ):
            raise ValueError("a computed run states the run without generation")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


_EXCLUDED: Final = frozenset(
    {"simulation_hash", "as_of", "approval", "approval_label", "approved_for_use"}
)


class _SimulationViewV1_1(ContractModel):
    schema_name: str
    schema_version: str
    method_version: str
    site_id: str
    record_class: str
    inputs: InputBindingV1_1 | NotApplicable
    config: AdequacyConfig
    policy: ResilienceFloorPolicy
    conformance: Conformance | NotApplicable
    outcome: Computed | NotComputed = Field(discriminator="kind")
    generation: GenerationSection
    data_requests: tuple[DossierAsk, ...]


def simulation_hash_v1_1(result: AdequacyResultV1_1 | dict[str, object]) -> str:
    """The hash of everything but the approval, over the 1.1.0 view."""
    if isinstance(result, AdequacyResultV1_1):
        fields = {k: getattr(result, k) for k in _SimulationViewV1_1.model_fields}
    else:
        fields = {k: v for k, v in result.items() if k not in _EXCLUDED}
    return content_hash(_SimulationViewV1_1(**fields))
