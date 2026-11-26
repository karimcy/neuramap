"""IC-1 ``ConnectionAgreement``: frozen contract, version 1.1.0 (roadmap WP-CONN-01, spec A1-01).

The typed replacement for the free-text ``ConnectionDetails.anm_or_curtailment_notes``
(`gridq.schemas.connection`): every term of a GB connection offer or agreement that decides how,
when and how far a site's import can be restricted. Producers: intake / D1 registry. Consumers:
A1 (connection-risk engine), A5 (sizer), B1 (edge gateway). Instances are stored by the evidence
registry and **referenced by hash** (`ConnectionAgreement.content_hash`).

**No field accepts ``None``.** Every A1-01 term is required, and a term that is not known is an
explicit `Unknown(reason=...)`; a term that does not exist for this agreement (a LIFO position on
a firm connection) is `NotApplicable(reason=...)`. `A1_01_FIELDS` maps each spec phrase to its
field; a conformance test proves every one is present, required and None-free.

Version: ``CONTRACT_VERSION`` (carried on every instance as a ``Literal``); JSON Schema frozen at
``schemas/ic1_connection_agreement.v1.1.0.schema.json``; git tag ``GIT_TAG`` (create on commit).
Depends on IC-4 v1.1.0 (`EvidenceRef`) for the signed document an agreement was transcribed from.

Where the structure comes from (public GB sources, as cited in spec §3.1, §7.1 and §17):
firm / non-firm / profiled import (UKPN "Profiled" connections); Curtailment Limit and the
exceeded-curtailment price (Ofgem Access SCR; DCUSA DCP404/405); LIFO stacking per constraint
(NGED ANM); transmission Technical Limits (SSEN methodology); hybrid firm-base-plus-interruptible
and N-1 vs N-2 (DESNZ strategic-demand response); ANM response time (spec B1-03, G100).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal, final

from pydantic import Field, model_validator

from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    NotApplicable,
    Unknown,
    content_hash,
)
from gridq.contracts.evidence import EvidenceRef

__all__ = [
    "A1_01_FIELDS",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "GIT_TAG",
    "IC1_EXTRA_FIELDS",
    "SUPPORTED_VERSIONS",
    "Citation",
    "ConnectionAgreement",
    "ConnectionArchetype",
    "ConstraintCause",
    "CurtailmentLimit",
    "CurtailmentPrice",
    "EvidenceBacked",
    "ImportProfile",
    "LifoPosition",
    "NetworkOperator",
    "PccTerms",
    "PowerFactorTerms",
    "ProfileBlock",
    "RampTerms",
    "SignalProtocol",
    "SyntheticArchetype",
]

CONTRACT_ID: Final = "IC-1"
CONTRACT_VERSION: Final = "1.1.0"
#: Every version this module reads. The last one is the version it writes.
SUPPORTED_VERSIONS: Final = ("1.0.0", "1.1.0")
GIT_TAG: Final = "contracts/ic1-connection-agreement/v1.1.0"

NonNegFloat = Annotated[float, Field(ge=0)]
PosFloat = Annotated[float, Field(gt=0)]

#: Hours in a leap year: the ceiling for an hours-per-year curtailment limit.
_MAX_HOURS_PER_YEAR: Final = 8784.0


class ConnectionArchetype(StrEnum):
    """Spec A1-01 "data-centre archetype"."""

    FIRM = "firm"
    HYBRID = "hybrid"  # firm base + interruptible (non-firm) tranche
    FULLY_NON_FIRM = "fully_non_firm"


class NetworkOperator(StrEnum):
    """The licensee that issued the offer: the six GB DNO groups, an IDNO, or NESO."""

    UKPN = "ukpn"
    NGED = "nged"
    SSEN = "ssen"
    SPEN = "spen"
    ENWL = "enwl"
    NPG = "npg"
    IDNO = "idno"
    NESO = "neso"


class ConstraintCause(StrEnum):
    """Spec A1-01 "cause (distribution vs transmission / Technical Limits)"."""

    DISTRIBUTION = "distribution"
    TRANSMISSION = "transmission"
    TRANSMISSION_TECHNICAL_LIMITS = "transmission_technical_limits"


class SignalProtocol(StrEnum):
    """IC-1 "signal protocol": how the DNO ANM / RTU instruction reaches site (spec §6, B1-03)."""

    HARDWIRED_CONTACT = "hardwired_contact"
    DNP3 = "dnp3"
    IEC_60870_5_104 = "iec_60870_5_104"
    IEC_61850 = "iec_61850"
    MODBUS_TCP = "modbus_tcp"


# --------------------------------------------------------------------------------------------
# Structured terms
# --------------------------------------------------------------------------------------------


class ProfileBlock(ContractModel):
    """One block of a profiled (time-varying) maximum import.

    Times are GB settlement periods, 1–48, inclusive at both ends (period 1 = 00:00–00:30).
    """

    months: tuple[Annotated[int, Field(ge=1, le=12)], ...] = Field(min_length=1)
    day_type: Literal["all", "weekday", "weekend"]
    start_period: Annotated[int, Field(ge=1, le=48)]
    end_period: Annotated[int, Field(ge=1, le=48)]
    max_import_mw: NonNegFloat

    @model_validator(mode="after")
    def _ordered(self) -> ProfileBlock:
        if self.end_period < self.start_period:
            raise ValueError("end_period must be >= start_period (blocks do not wrap midnight)")
        if list(self.months) != sorted(set(self.months)):
            raise ValueError("months must be strictly ascending with no repeats")
        return self

    def cells(self) -> set[tuple[int, str, int]]:
        days = ("weekday", "weekend") if self.day_type == "all" else (self.day_type,)
        return {
            (m, d, p)
            for m in self.months
            for d in days
            for p in range(self.start_period, self.end_period + 1)
        }


class ImportProfile(ContractModel):
    """Spec A1-01 "profiled import (time-varying max import, UKPN 'Profiled')"."""

    kind: Literal["import_profile"]
    blocks: tuple[ProfileBlock, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_overlap(self) -> ImportProfile:
        seen: set[tuple[int, str, int]] = set()
        for b in self.blocks:
            cells = b.cells()
            if seen & cells:
                raise ValueError("profile blocks overlap: a half-hour has two maximum imports")
            seen |= cells
        return self

    @property
    def covers_whole_year(self) -> bool:
        return sum(len(b.cells()) for b in self.blocks) == 12 * 2 * 48

    @property
    def peak_mw(self) -> float:
        return max(b.max_import_mw for b in self.blocks)


class CurtailmentLimit(ContractModel):
    """Spec A1-01 "Curtailment Limit (hours or % per year, Access SCR / DCUSA DCP404/405)"."""

    kind: Literal["curtailment_limit"]
    basis: Literal["hours_per_year", "percent_of_hours_per_year", "percent_of_energy_per_year"]
    value: NonNegFloat

    @model_validator(mode="after")
    def _bounded(self) -> CurtailmentLimit:
        cap = _MAX_HOURS_PER_YEAR if self.basis == "hours_per_year" else 100.0
        if self.value > cap:
            raise ValueError(f"curtailment limit {self.value} exceeds {cap} for {self.basis}")
        return self


class CurtailmentPrice(ContractModel):
    """Spec A1-01 "exceeded-curtailment price": paid for curtailment beyond the limit."""

    kind: Literal["curtailment_price"]
    gbp_per_mwh: NonNegFloat


class LifoPosition(ContractModel):
    """Spec A1-01 "LIFO position", one per constraint (NGED's LIFO stack is per constraint)."""

    constraint_ref: NonEmptyStr
    position: Annotated[int, Field(ge=1)]
    queue_length: Annotated[int, Field(ge=1)] | Unknown

    @model_validator(mode="after")
    def _within_queue(self) -> LifoPosition:
        if isinstance(self.queue_length, int) and self.position > self.queue_length:
            raise ValueError("LIFO position cannot exceed the queue length")
        return self


class RampTerms(ContractModel):
    """Spec A1-01 "ramp" terms on import changes."""

    kind: Literal["ramp_terms"]
    max_ramp_up_mw_per_min: PosFloat
    max_ramp_down_mw_per_min: PosFloat


class PowerFactorTerms(ContractModel):
    """Spec A1-01 "power-factor" terms."""

    kind: Literal["power_factor_terms"]
    min_power_factor: Annotated[float, Field(gt=0, le=1)]
    reactive_direction: Literal["lagging", "leading", "lagging_or_leading"]


class PccTerms(ContractModel):
    """Spec A1-01 "PCC terms": the point of common coupling."""

    kind: Literal["pcc_terms"]
    voltage_kv: PosFloat
    description: NonEmptyStr


class Citation(ContractModel):
    title: NonEmptyStr
    url: NonEmptyStr
    used_for: NonEmptyStr


class EvidenceBacked(ContractModel):
    """Transcribed from a registered IC-4 evidence object (the signed offer or agreement)."""

    kind: Literal["evidence_backed"]
    evidence: EvidenceRef


class SyntheticArchetype(ContractModel):
    """Schema-test data, not an agreement anyone holds. Consumers must refuse it outside tests."""

    kind: Literal["synthetic_archetype"]
    statement: NonEmptyStr
    based_on: tuple[Citation, ...] = Field(min_length=1)


# --------------------------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------------------------


@final
class ConnectionAgreement(ContractModel):
    """IC-1 v1.1.0: all A1-01 terms, plus ANM response time and signal protocol."""

    contract: Literal["IC-1"] = CONTRACT_ID
    contract_version: Literal["1.0.0", "1.1.0"] = CONTRACT_VERSION

    # ----- identity and provenance -----
    agreement_ref: NonEmptyStr
    site_id: NonEmptyStr
    source: EvidenceBacked | SyntheticArchetype | Unknown = Field(discriminator="kind")
    network_operator: NetworkOperator | Unknown

    # ----- A1-01 -----
    archetype: ConnectionArchetype
    firm_import_mw: NonNegFloat | Unknown
    non_firm_import_mw: NonNegFloat | Unknown
    profiled_import: ImportProfile | NotApplicable | Unknown = Field(discriminator="kind")
    curtailment_limit: CurtailmentLimit | NotApplicable | Unknown = Field(discriminator="kind")
    exceeded_curtailment_price: CurtailmentPrice | NotApplicable | Unknown = Field(
        discriminator="kind"
    )
    non_firm_end_date: IsoDate | NotApplicable | Unknown
    firm_by_date: IsoDate | NotApplicable | Unknown
    tenure: Literal["interim", "enduring"] | Unknown
    constraint_causes: Annotated[tuple[ConstraintCause, ...], Field(min_length=1)] | (
        NotApplicable | Unknown
    )
    lifo_positions: Annotated[tuple[LifoPosition, ...], Field(min_length=1)] | (
        NotApplicable | Unknown
    )
    notice_period_s: NonNegFloat | NotApplicable | Unknown
    signal_type: Literal["setpoint", "trip", "setpoint_and_trip"] | NotApplicable | Unknown
    ramp: RampTerms | NotApplicable | Unknown = Field(discriminator="kind")
    power_factor: PowerFactorTerms | NotApplicable | Unknown = Field(discriminator="kind")
    pcc: PccTerms | Unknown = Field(discriminator="kind")
    network_security: Literal["N-0", "N-1", "N-2"] | Unknown

    # ----- IC-1 additions (spec §10A) -----
    anm_response_time_s: PosFloat | NotApplicable | Unknown
    signal_protocol: SignalProtocol | NotApplicable | Unknown

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ConnectionAgreement is final: a new shape is a new contract version")

    @model_validator(mode="after")
    def _consistent(self) -> ConnectionAgreement:
        firm, nonfirm = self.firm_import_mw, self.non_firm_import_mw
        a = self.archetype
        if isinstance(firm, float) and isinstance(nonfirm, float):
            if firm + nonfirm <= 0:
                raise ValueError("an agreement with zero firm and zero non-firm import is empty")
        if a is ConnectionArchetype.FIRM:
            if isinstance(nonfirm, float) and nonfirm != 0:
                raise ValueError("a firm archetype has non_firm_import_mw == 0")
            if isinstance(firm, float) and firm <= 0:
                raise ValueError("a firm archetype has firm_import_mw > 0")
        elif a is ConnectionArchetype.HYBRID:
            if (isinstance(firm, float) and firm <= 0) or (
                isinstance(nonfirm, float) and nonfirm <= 0
            ):
                raise ValueError("a hybrid archetype has firm_import_mw > 0 and non_firm > 0")
        elif a is ConnectionArchetype.FULLY_NON_FIRM:
            if isinstance(firm, float) and firm != 0:
                raise ValueError("a fully non-firm archetype has firm_import_mw == 0")
            if isinstance(nonfirm, float) and nonfirm <= 0:
                raise ValueError("a fully non-firm archetype has non_firm_import_mw > 0")
        if (
            isinstance(self.profiled_import, ImportProfile)
            and isinstance(firm, float)
            and isinstance(nonfirm, float)
            and self.profiled_import.peak_mw > firm + nonfirm
        ):
            raise ValueError("a profile block exceeds the agreed firm + non-firm import")
        if isinstance(self.lifo_positions, tuple):
            refs = [p.constraint_ref for p in self.lifo_positions]
            if len(refs) != len(set(refs)):
                raise ValueError("one LIFO position per constraint_ref")
        if a is not ConnectionArchetype.FIRM and isinstance(self.signal_type, NotApplicable):
            if isinstance(self.profiled_import, NotApplicable):
                raise ValueError(
                    "a non-firm agreement with no ANM signal must be restricted by a profile; "
                    "signal_type and profiled_import cannot both be not_applicable"
                )
        return self

    @model_validator(mode="after")
    def _down_level(self) -> ConnectionAgreement:
        # Down-level rule (WP-BO-06): an agreement labelled 1.0.0 holds only what 1.0.0 allowed.
        if self.contract_version == "1.0.0" and isinstance(self.source, EvidenceBacked):
            version = self.source.evidence.contract_version
            if version != "1.0.0":
                raise ValueError(
                    f"source.evidence IC-4 reference version {version} is not in IC-1 1.0.0: "
                    "label the agreement 1.1.0"
                )
        return self

    def content_hash(self) -> str:
        """``sha256:<hex>`` of the canonical JSON: the identity the registry references."""
        return content_hash(self)


#: Spec A1-01, phrase by phrase → field. A conformance test proves each is present and None-free.
A1_01_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "firm MW": ("firm_import_mw",),
    "non-firm MW": ("non_firm_import_mw",),
    "profiled import (time-varying max import, UKPN 'Profiled')": ("profiled_import",),
    "Curtailment Limit (hours or % per year, Access SCR / DCUSA DCP404/405)": (
        "curtailment_limit",
    ),
    "exceeded-curtailment price": ("exceeded_curtailment_price",),
    "end date / firm-by date": ("non_firm_end_date", "firm_by_date"),
    "interim vs enduring": ("tenure",),
    "cause (distribution vs transmission / Technical Limits)": ("constraint_causes",),
    "LIFO position": ("lifo_positions",),
    "notice period": ("notice_period_s",),
    "signal type (setpoint vs trip)": ("signal_type",),
    "ramp, power-factor and PCC terms": ("ramp", "power_factor", "pcc"),
    "N-1/N-2 resilience": ("network_security",),
    "data-centre archetype (firm, hybrid firm-base + interruptible, fully non-firm)": (
        "archetype",
    ),
}

#: Spec §10A IC-1 additions beyond A1-01.
IC1_EXTRA_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "required ANM response time": ("anm_response_time_s",),
    "signal protocol": ("signal_protocol",),
}
