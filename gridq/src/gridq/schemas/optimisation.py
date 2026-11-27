"""Optimisation request/result — the platform<->engine seam.

`OptimisationRequest` is the envelope Oli hands the engine; `OptimisationResult` is what
the engine (mock or real) returns. M5/M8 consume the result without importing the engine.
No optimisation logic lives here — only the shared shapes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from gridq.schemas.constraint_library import ConstraintLibraryRef
from gridq.schemas.enums import (
    BankableStatus,
    ImplementationLevel,
    MarketStream,
    SelectionStatus,
    SolveMode,
    SolverStatus,
)
from gridq.schemas.market import GATED_UPSIDE_STREAMS, ValueStreamResult
from gridq.schemas.provenance import GridQModel
from gridq.schemas.references import (
    ConnectionAgreementRef,
    EvidenceLink,
    canonical_evidence_links,
    omit_unset,
)
from gridq.schemas.site import SiteModel


class SolverMetadata(GridQModel):
    """Run metadata stamped onto a result (versions, seed, timing, environment)."""

    seed: int | None = None
    assumption_version: str | None = None
    code_version: str | None = None
    model_version: str | None = None
    solve_seconds: float | None = Field(default=None, ge=0)
    reproduction_env: str | None = None


class OptimisationRequest(GridQModel):
    """The clean object passed to the optimiser (mock now, real engine later)."""

    request_id: str
    site_model: SiteModel
    assumption_version: str
    scenario_set_id: str | None = None
    requested_streams: list[MarketStream] = Field(default_factory=list)
    solve_mode: SolveMode = SolveMode.BASELINE
    created_at: datetime = Field(default_factory=datetime.now)


class OptimisationResult(GridQModel):
    """The object the engine returns. Frozen shared contract (with the stream registry)."""

    result_id: str
    request_id: str
    implementation_level: ImplementationLevel
    bankable_status: BankableStatus
    selected_power_kw: float | None = Field(default=None, ge=0)
    selected_energy_kwh: float | None = Field(default=None, ge=0)
    duration_hours: float | None = Field(default=None, ge=0)
    dispatch_schedule_ref: str | None = None
    value_streams: list[ValueStreamResult] = Field(default_factory=list)
    constraints_binding: list[str] = Field(default_factory=list)
    gated_upside: list[ValueStreamResult] = Field(default_factory=list)
    solver_status: SolverStatus
    #: The sizing selection's verdict (additive, 2026-07-07): ``selected`` when a system is
    #: recommended; ``no_viable_configuration`` when every candidate evaluated economically
    #: unviable (an explicit verdict — the engine never presents a grid corner as a
    #: recommendation); ``None`` on historical records and engines that predate the field.
    selection_status: SelectionStatus | None = None
    solver_metadata: SolverMetadata | None = None
    #: IC-12 2.0.0: the IC-1 agreement whose terms the solve honoured, by content hash. ``None``
    #: until an engine reads connection terms from an agreement (WP-CONN-06, WP-SIZE-02); no
    #: engine sets it today. Absent from every dump while ``None``.
    connection_agreement: ConnectionAgreementRef | None = None
    #: IC-12 2.0.0: IC-4 evidence the engine relied on, with what each object supports. ``None``
    #: until an engine reads evidence (`underwriting_basis_v1`, WP-UW-01); no engine sets it
    #: today. Absent from every dump while ``None``.
    evidence_refs: tuple[EvidenceLink, ...] | None = None
    #: IC-12 3.2.0 (WP-SIZE-02, D178): the versioned constraint library
    #: (`gridq.constraint_library`) the result was solved with: its id, version, source hash and
    #: constraint ids. Every engine in this repository sets it; ``None`` only on a payload written
    #: before 3.2.0 or a hand-built fixture. Absent from every dump while ``None``, and kept out of
    #: ``input_hash`` (it is code, pinned by ``code_version`` through ``run_id``).
    constraint_library: ConstraintLibraryRef | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)

    @model_serializer(mode="wrap")
    def _dump_without_unset_growth(self, handler: SerializerFunctionWrapHandler) -> Any:
        return omit_unset(
            handler(self), ("connection_agreement", "evidence_refs", "constraint_library")
        )

    @model_validator(mode="after")
    def _checks(self) -> OptimisationResult:
        self.evidence_refs = canonical_evidence_links(self.evidence_refs)
        if (
            self.implementation_level == ImplementationLevel.BASELINE
            and self.bankable_status == BankableStatus.BANKABLE_CANDIDATE
        ):
            raise ValueError(
                "a baseline-implementation result cannot be marked bankable_candidate."
            )
        off_register = [
            v.stream.value for v in self.gated_upside if v.stream not in GATED_UPSIDE_STREAMS
        ]
        if off_register:
            raise ValueError(
                f"gated_upside may only contain gated-upside streams; got {off_register}."
            )
        return self
