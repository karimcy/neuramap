"""IC-15 ``ContractTemplateSet`` (roadmap NEW-IC-15, released by WP-GRC-09), version 1.0.0.

The contract template set is seven draft templates. Each is a markdown text under ``templates/``,
described by one manifest entry (``templates/contract_template_set.json``, an instance of
`ContractTemplateSet`). The templates are:

- the Assessment statement of work;
- the Software and Dispatch master services agreement, written in full;
- the data-sharing agreement;
- the aggregator / flexibility services agreement;
- the site access and installation agreement;
- the lender reliance letter;
- the performance wrapper.

All but the MSA are lean heads of terms. **Every template is a draft that needs legal review.**
None is presented as reviewed. The review state is a closed union. ``counsel_reviewed`` binds the
SHA-256 of the reviewed text and a reference to counsel's third-party ``legal_opinion``
evidence object, so editing the text puts the template back to draft.

Each entry gives the following:

- **How an executed copy is registered.** It names the IC-4 evidence type and the issuer roles
  admitted for it (acceptance 1). The five types IC-4 1.2.0 added, plus ``data_sharing_agreement``,
  ``aggregator_agreement`` and ``dno_flexibility_contract``.
- **The required fields.** Each is typed and carries the ask that fills it. No party name, price or
  term that is not on record appears as a value.
- **The named clauses.** The MSA must carry the eight in `MSA_NAMED_CLAUSES` (acceptance 2) and the
  spec §8 safety hierarchy as an ordered typed list.
- **The commercial terms.** A price is either one of the four list prices on record (ADR-D1), shown
  as "list price on record, not agreed with any customer", or ``to_be_agreed`` with its ask.
- **The ADR-D1 financing variants.** There are three, and each has a schedule section.
- **The cross-contract references.** Each is a `ContractVersionRef` that names a contract and a
  major-1 range, never prose (acceptance 3). IC-3 is ``frozen``: WP-SIZE-07 froze it at 1.0.0
  (tag ``contracts/ic3-dispatch-envelope/v1.0.0``), so a field bound to it holds an envelope by
  content hash. IC-8 is ``not_yet_frozen``: the reference names the package that freezes it
  (WP-LEDGER-07). Its text says "unavailable until" that package, and a field bound to it cannot
  hold a value until then.

`ExecutedContractTerms` records one executed contract: the template, its version and text hash,
the IC-4 hash of the signed copy, the variant, and every required field as a value, an `Unknown`
with its ask, or a `NotApplicable` with its reason. `check_executed_terms` refuses a draft template
by default.

This module renders nothing and substitutes nothing. It is the typed manifest and its checks.
Like every contract module it is a leaf: it imports only `_base` and `evidence`. It holds IC-4
evidence types and issuer roles as strings checked against the live IC-4 enums, so a future IC-4
types-only minor does not change this contract's schema.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import Field, StringConstraints, model_validator

from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    Unknown,
    Version1x,
    content_hash,
)
from gridq.contracts.evidence import (
    SELF_ISSUABLE_TYPES,
    EvidenceObject,
    EvidenceRef,
    EvidenceRefused,
    EvidenceType,
    IssuerRole,
    resolve_ref,
)

__all__ = [
    "CLOSED_LOOP_GATES",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "DRAFT_HEADER",
    "GIT_TAG",
    "LIST_PRICES_ON_RECORD",
    "LIST_PRICE_RENDERING",
    "MANIFEST_PATH",
    "MSA_NAMED_CLAUSES",
    "PROSE_GUARD_TERMS",
    "SAFETY_HIERARCHY",
    "SAFETY_TIER_LABELS",
    "SUPPORTED_VERSIONS",
    "TEMPLATE_DIR",
    "VARIANT_LABELS",
    "Answerer",
    "Clause",
    "CommercialTerm",
    "ContractInstanceValue",
    "ContractTemplateSet",
    "ContractVersionRef",
    "ControlAuthorityStaging",
    "ControlStage",
    "ControlStageTerm",
    "CounselReviewed",
    "DateValue",
    "DraftNeedsLegalReview",
    "DraftTemplateRefused",
    "ExecutedAs",
    "ExecutedContractTerms",
    "FieldEntry",
    "FieldKind",
    "FinancingVariant",
    "Frozen",
    "ListPriceId",
    "ListPriceOnRecord",
    "NotYetFrozen",
    "PersonOnlyKind",
    "PersonOnlyStep",
    "QuantityUnit",
    "QuantityValue",
    "RequiredField",
    "RiskCarrier",
    "RiskCarrierOption",
    "SafetyHierarchy",
    "SafetyTier",
    "TemplateEntry",
    "TemplateId",
    "TemplateRefused",
    "TemplateTextMismatch",
    "TextValue",
    "ToBeAgreed",
    "UnavailableTerm",
    "VariantSchedule",
    "check_counsel_review",
    "check_executed_terms",
    "effective_review",
    "live_contract_versions",
    "load_template_set",
    "manifest_text",
    "reference_findings",
    "template_set_findings",
    "template_text",
    "text_findings",
    "text_sha256",
]

CONTRACT_ID: Final = "IC-15"
CONTRACT_VERSION: Final = "1.0.0"
#: Every version this code reads.
SUPPORTED_VERSIONS: Final = ("1.0.0",)
GIT_TAG: Final = "contracts/ic15-contract-template-set/v1.0.0"

#: The template texts and the manifest live beside the schemas, read from the source tree.
TEMPLATE_DIR: Final = Path(__file__).resolve().parent / "templates"
MANIFEST_PATH: Final = TEMPLATE_DIR / "contract_template_set.json"

#: The first line of every draft text is ``> **`` + this + ``**``.
DRAFT_HEADER: Final = "DRAFT — NEEDS LEGAL REVIEW. NOT LEGAL ADVICE."
#: How a list price on record is shown wherever a template cites it.
LIST_PRICE_RENDERING: Final = "list price on record, not agreed with any customer"

SnakeId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
ContractId = Annotated[str, StringConstraints(pattern=r"^IC-[1-9][0-9]*$")]
WorkPackageId = Annotated[str, StringConstraints(pattern=r"^WP-[A-Z]+-[0-9]{2}$")]
#: A roadmap gate: an external gate (XG-n), a decision (D-n) or a work package.
GateId = Annotated[
    str, StringConstraints(pattern=r"^(XG-[1-9][0-9]*|D-[1-9][0-9]*|WP-[A-Z]+-[0-9]{2})$")
]
SemVer = Annotated[
    str,
    StringConstraints(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"),
]
GapId = Annotated[str, StringConstraints(pattern=r"^G[1-9][0-9]*$")]
NonNegativeInt = Annotated[int, Field(ge=0)]


# --------------------------------------------------------------------------------------------
# Closed vocabularies
# --------------------------------------------------------------------------------------------


class TemplateId(StrEnum):
    """The seven templates, in the order WP-GRC-09 lists them."""

    ASSESSMENT_SOW = "assessment_sow"
    SOFTWARE_DISPATCH_MSA = "software_dispatch_msa"
    DATA_SHARING_AGREEMENT = "data_sharing_agreement"
    FLEXIBILITY_SERVICES_AGREEMENT = "flexibility_services_agreement"
    SITE_ACCESS_INSTALLATION = "site_access_installation"
    LENDER_RELIANCE_LETTER = "lender_reliance_letter"
    PERFORMANCE_WRAPPER = "performance_wrapper"


class FieldKind(StrEnum):
    """What a required field holds once known."""

    TEXT = "text"
    DATE = "date"
    #: A non-negative integer in the field's `QuantityUnit`.
    QUANTITY = "quantity"
    #: A reference to an instance of another contract, by hash, bound to a `ContractVersionRef`.
    CONTRACT_INSTANCE = "contract_instance"


class QuantityUnit(StrEnum):
    """Integer units only: money in pence, rates in basis points, no floats."""

    GBP_PENCE = "gbp_pence"
    GBP_PENCE_PER_MEGAWATT_YEAR = "gbp_pence_per_megawatt_year"
    BASIS_POINTS = "basis_points"
    BASIS_POINTS_OF_UNDERWRITTEN_VALUE = "basis_points_of_underwritten_value"
    KILOWATTS = "kilowatts"
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"
    MONTHS = "months"


class Answerer(StrEnum):
    """Who answers a field's ask. Parties are roles; no party is named."""

    PROVIDER = "provider"
    CUSTOMER = "customer"
    HOST = "host"
    LENDER = "lender"
    RISK_CARRIER = "risk_carrier"
    FLEXIBILITY_COUNTERPARTY = "flexibility_counterparty"
    DATA_COUNTERPARTY = "data_counterparty"
    COUNSEL = "counsel"


class SafetyTier(StrEnum):
    """The spec §8 safety hierarchy tiers. Their order is fixed in `SAFETY_HIERARCHY`."""

    LIFE_SAFETY = "life_safety"
    IT_SLA_AND_THERMAL_LIMITS = "it_sla_and_thermal_limits"
    CONNECTION_COMPLIANCE = "connection_compliance"
    RESILIENCE_RESERVE = "resilience_reserve"
    CONTRACTED_MARKET_DELIVERY = "contracted_market_delivery"
    MERCHANT_REVENUE = "merchant_revenue"


#: Spec §8 principle 1, highest first: life safety > IT SLA / thermal limits > connection
#: compliance (import limit, ramp) > resilience reserve > contracted market delivery > merchant
#: revenue. Written out, not taken from the enum's order, so reordering the enum cannot move it.
SAFETY_HIERARCHY: Final[tuple[SafetyTier, ...]] = (
    SafetyTier.LIFE_SAFETY,
    SafetyTier.IT_SLA_AND_THERMAL_LIMITS,
    SafetyTier.CONNECTION_COMPLIANCE,
    SafetyTier.RESILIENCE_RESERVE,
    SafetyTier.CONTRACTED_MARKET_DELIVERY,
    SafetyTier.MERCHANT_REVENUE,
)
#: How each tier reads in a template text. `text_findings` checks they appear in order.
SAFETY_TIER_LABELS: Final[dict[SafetyTier, str]] = {
    SafetyTier.LIFE_SAFETY: "Life safety",
    SafetyTier.IT_SLA_AND_THERMAL_LIMITS: "IT service levels and thermal limits",
    SafetyTier.CONNECTION_COMPLIANCE: "Connection compliance (import limit, ramp)",
    SafetyTier.RESILIENCE_RESERVE: "Resilience reserve",
    SafetyTier.CONTRACTED_MARKET_DELIVERY: "Contracted market delivery",
    SafetyTier.MERCHANT_REVENUE: "Merchant revenue",
}


class ControlStage(StrEnum):
    """ADR-D2 control-authority stages, L0 to L4."""

    L0_READ_ONLY = "l0_read_only"
    L1_SHADOW = "l1_shadow"
    L2_BESS_CLOSED_LOOP = "l2_bess_closed_loop"
    L3_CHILLER_NARROW_BAND = "l3_chiller_narrow_band"
    L4_WORKLOAD_ADVISORY = "l4_workload_advisory"


#: Each stage with a write path waits on all three (ADR-D2): the host critical-facilities lead's
#: sign-off, the control safety case, and the load-control licence or an exemption.
CLOSED_LOOP_GATES: Final = ("XG-11", "WP-EDGE-08", "XG-17")
#: The stages ADR-D2 gives a write path. L4 stays advisory and shadow only.
_WRITE_STAGES: Final = frozenset(
    {ControlStage.L2_BESS_CLOSED_LOOP, ControlStage.L3_CHILLER_NARROW_BAND}
)

#: The clauses the MSA must carry by these ids (WP-GRC-09 acceptance 2, plan §2 item 2).
MSA_NAMED_CLAUSES: Final = (
    "safety_hierarchy",
    "it_outage_carve_out",
    "step_in",
    "liability_cap",
    "service_levels",
    "termination",
    "control_authority_staging",
    "responsibility_boundary",
)


class FinancingVariant(StrEnum):
    """ADR-D1 option (d): vendor first, with IPP and power provision as third-party-capital
    wrappers. Each template carries one schedule section covering all three."""

    DIRECT_HOST_FUNDED = "direct_host_funded"
    IPP_THIRD_PARTY_OWNER = "ipp_third_party_owner"
    POWER_PROVISION_LESSOR = "power_provision_lessor"


#: How each variant reads in a template's variant schedule. `text_findings` checks all three appear.
VARIANT_LABELS: Final[dict[FinancingVariant, str]] = {
    FinancingVariant.DIRECT_HOST_FUNDED: "Direct, host funded",
    FinancingVariant.IPP_THIRD_PARTY_OWNER: "IPP, third-party owner",
    FinancingVariant.POWER_PROVISION_LESSOR: "Power-provision lessor",
}


class ListPriceId(StrEnum):
    """The four prices on record (ADR-D1). No other figure may be a list price."""

    ASSESSMENT_FEE = "assessment_fee"
    INTEGRATION_FEE = "integration_fee"
    OPERATE_FEE = "operate_fee"
    WRAPPER_FEE = "wrapper_fee"


#: ADR-D1 Decision: "assessments (£20k), integration (£5k), Operate at £10k/MW/yr, and 2.5% of
#: underwritten value through an insurer, MGA or floor partner (XG-20)". Each price is held as an
#: integer amount, a unit, and the one template that may cite it.
LIST_PRICES_ON_RECORD: Final[dict[ListPriceId, tuple[int, QuantityUnit, TemplateId]]] = {
    ListPriceId.ASSESSMENT_FEE: (2_000_000, QuantityUnit.GBP_PENCE, TemplateId.ASSESSMENT_SOW),
    ListPriceId.INTEGRATION_FEE: (
        500_000,
        QuantityUnit.GBP_PENCE,
        TemplateId.SOFTWARE_DISPATCH_MSA,
    ),
    ListPriceId.OPERATE_FEE: (
        1_000_000,
        QuantityUnit.GBP_PENCE_PER_MEGAWATT_YEAR,
        TemplateId.SOFTWARE_DISPATCH_MSA,
    ),
    ListPriceId.WRAPPER_FEE: (
        250,
        QuantityUnit.BASIS_POINTS_OF_UNDERWRITTEN_VALUE,
        TemplateId.PERFORMANCE_WRAPPER,
    ),
}


class RiskCarrier(StrEnum):
    """Who may carry the performance wrapper's risk (ADR-D1, XG-20)."""

    INSURER = "insurer"
    MGA = "mga"
    FLOOR_PROVIDER = "floor_provider"


class PersonOnlyKind(StrEnum):
    """Steps only a person may take. No agent takes them."""

    LEGAL_REVIEW = "legal_review"
    ENGAGE_COUNSEL = "engage_counsel"
    RISK_CARRIER_AND_PERIMETER = "risk_carrier_and_perimeter"
    AGREE_COMMERCIAL_TERMS = "agree_commercial_terms"
    SIGN = "sign"
    CRITICAL_FACILITIES_SIGNOFF = "critical_facilities_signoff"
    LOAD_CONTROL_LICENCE = "load_control_licence"
    REGISTER_CONTRACT = "register_contract"


# --------------------------------------------------------------------------------------------
# Manifest building blocks
# --------------------------------------------------------------------------------------------


class RequiredField(ContractModel):
    """A field an executed contract must state, with the ask that fills it."""

    field_id: SnakeId
    kind: FieldKind
    #: Set only for a quantity.
    unit: QuantityUnit | NotApplicable
    #: The ``ref_id`` of the `ContractVersionRef` a contract-instance field is bound to.
    binds: SnakeId | NotApplicable
    #: What to ask, and of whom, to fill this field. Never a guessed value.
    ask: NonEmptyStr
    asked_of: tuple[Answerer, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_kind(self) -> RequiredField:
        if (self.kind is FieldKind.QUANTITY) != isinstance(self.unit, QuantityUnit):
            raise ValueError(f"field {self.field_id}: a unit is set for a quantity, and only then")
        if (self.kind is FieldKind.CONTRACT_INSTANCE) != isinstance(self.binds, str):
            raise ValueError(
                f"field {self.field_id}: a contract-instance field binds a reference, and only it"
            )
        return self


class Clause(ContractModel):
    """A named clause. Its id is the ``[clause:<id>]`` anchor in the template text."""

    clause_id: SnakeId
    title: NonEmptyStr


class ListPriceOnRecord(ContractModel):
    """One of the four ADR-D1 list prices. Never a price agreed with anyone."""

    kind: Literal["list_price_on_record"]
    price_id: ListPriceId
    amount: NonNegativeInt
    unit: QuantityUnit
    source: Literal["ADR-D1"]
    rendering: Literal["list price on record, not agreed with any customer"]

    @model_validator(mode="after")
    def _check_on_record(self) -> ListPriceOnRecord:
        amount, unit, _ = LIST_PRICES_ON_RECORD[self.price_id]
        if (self.amount, self.unit) != (amount, unit):
            raise ValueError(
                f"{self.price_id.value} on record is {amount} {unit.value}, not "
                f"{self.amount} {self.unit.value}"
            )
        return self


class ToBeAgreed(ContractModel):
    """A term with no figure on record: the ask says who agrees it."""

    kind: Literal["to_be_agreed"]
    ask: NonEmptyStr


PriceTerm = Annotated[ListPriceOnRecord | ToBeAgreed, Field(discriminator="kind")]


class CommercialTerm(ContractModel):
    """A price, cap, service level, threshold or period. The executed figure is always a
    required quantity field with its own ask, whatever the list price says."""

    term_id: SnakeId
    executed_field: SnakeId
    price: PriceTerm


class DraftNeedsLegalReview(ContractModel):
    """The template has not been reviewed by counsel. It must not be sent as an offer."""

    kind: Literal["draft_needs_legal_review"]
    reason: NonEmptyStr


class CounselReviewed(ContractModel):
    """Counsel reviewed exactly the text whose SHA-256 is given. ``opinion`` is an IC-4
    reference to counsel's third-party ``legal_opinion``; `check_counsel_review` resolves it."""

    kind: Literal["counsel_reviewed"]
    reviewed_text_sha256: Sha256
    opinion: EvidenceRef


LegalReview = Annotated[DraftNeedsLegalReview | CounselReviewed, Field(discriminator="kind")]


class ExecutedAs(ContractModel):
    """How an executed copy is registered: an IC-4 evidence type, and the issuer roles admitted
    for it. Both are IC-4 enum values held as strings and checked against the live enums."""

    evidence_type: SnakeId
    admissible_issuers: tuple[SnakeId, ...] = Field(min_length=1)
    #: When this registration applies (e.g. "the counterparty is a DNO or IDNO").
    applies_when: NonEmptyStr

    @model_validator(mode="after")
    def _check_ic4(self) -> ExecutedAs:
        if self.evidence_type not in {t.value for t in EvidenceType}:
            raise ValueError(f"{self.evidence_type} is not an IC-4 evidence type")
        if EvidenceType(self.evidence_type) in SELF_ISSUABLE_TYPES:
            raise ValueError(
                f"{self.evidence_type} is a type Neura may self-issue; an executed contract is "
                "evidence only when the counterparty issues it"
            )
        roles = {r.value for r in IssuerRole}
        bad = [r for r in self.admissible_issuers if r not in roles]
        if bad:
            raise ValueError(f"not IC-4 third-party issuer roles: {bad}")
        return self


class Frozen(ContractModel):
    """The referenced contract is frozen and tagged. Any 1.x at or above the minimum serves."""

    kind: Literal["frozen"]
    minimum_version: Version1x
    git_tag: Annotated[
        str, StringConstraints(pattern=r"^contracts/ic[1-9][0-9]*-[a-z0-9-]+/v1\.[0-9]+\.[0-9]+$")
    ]


class NotYetFrozen(ContractModel):
    """The referenced contract does not exist yet. The reference names the package that freezes
    it; until then the template text says "unavailable until" that package."""

    kind: Literal["not_yet_frozen"]
    freezing_work_package: WorkPackageId
    gates: tuple[GateId, ...]
    reason: NonEmptyStr


class ContractVersionRef(ContractModel):
    """A template's reference to another contract by id and major-1 version range. The text
    cites it as ``[[ref:<ref_id>]]``, never by prose."""

    kind: Literal["contract_version_ref"]
    ref_id: SnakeId
    contract: ContractId
    contract_name: NonEmptyStr
    version_range: Literal["1.x"]
    status: Annotated[Frozen | NotYetFrozen, Field(discriminator="kind")]


class UnavailableTerm(ContractModel):
    """A term no template may state until the named packages deliver it."""

    term_id: SnakeId
    until: tuple[WorkPackageId, ...] = Field(min_length=1)
    gates: tuple[GateId, ...]
    reason: NonEmptyStr


class VariantSchedule(ContractModel):
    """What one ADR-D1 financing variant changes in a template."""

    variant: FinancingVariant
    counterparty: NonEmptyStr
    changes: NonEmptyStr


class SafetyHierarchy(ContractModel):
    """The spec §8 safety hierarchy as an ordered list, highest first."""

    kind: Literal["safety_hierarchy"]
    source: Literal["spec §8 principle 1"]
    order: tuple[SafetyTier, ...]

    @model_validator(mode="after")
    def _check_order(self) -> SafetyHierarchy:
        if self.order != SAFETY_HIERARCHY:
            raise ValueError(
                "the safety hierarchy must be spec §8's, in its order: "
                + " > ".join(t.value for t in SAFETY_HIERARCHY)
            )
        return self


class ControlStageTerm(ContractModel):
    stage: ControlStage
    write_path: bool
    gates: tuple[GateId, ...]


class ControlAuthorityStaging(ContractModel):
    """ADR-D2 staging: L0 read-only, L1 shadow, L2 BESS closed loop, L3 chiller narrow band, L4
    workload advisory. Only L2 and L3 write, and each waits on `CLOSED_LOOP_GATES`."""

    kind: Literal["control_authority_staging"]
    source: Literal["ADR-D2"]
    stages: tuple[ControlStageTerm, ...]

    @model_validator(mode="after")
    def _check_stages(self) -> ControlAuthorityStaging:
        if tuple(s.stage for s in self.stages) != tuple(ControlStage):
            raise ValueError("control stages must be L0 to L4, each once, in order")
        for s in self.stages:
            if s.write_path != (s.stage in _WRITE_STAGES):
                raise ValueError(f"{s.stage.value}: only L2 and L3 have a write path (ADR-D2)")
            if s.write_path and not set(CLOSED_LOOP_GATES) <= set(s.gates):
                raise ValueError(
                    f"{s.stage.value} writes to site plant, so it waits on "
                    + ", ".join(CLOSED_LOOP_GATES)
                )
        return self


class CarrierAvailable(ContractModel):
    kind: Literal["available"]
    issuer_role: SnakeId


class CarrierUnavailable(ContractModel):
    """No IC-4 issuer role exists for this carrier yet. Adding one re-versions IC-2 and IC-11."""

    kind: Literal["unavailable"]
    gap: GapId
    owner: WorkPackageId
    reason: NonEmptyStr


class RiskCarrierOption(ContractModel):
    carrier: RiskCarrier
    admission: Annotated[CarrierAvailable | CarrierUnavailable, Field(discriminator="kind")]


class PersonOnlyStep(ContractModel):
    """A step only a person may take, with the templates and gates it concerns."""

    step_id: SnakeId
    kind: PersonOnlyKind
    action: NonEmptyStr
    templates: tuple[TemplateId, ...]
    gates: tuple[GateId, ...]


# --------------------------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------------------------


class TemplateEntry(ContractModel):
    """One template: its text by hash, its review state, how an executed copy is registered, its
    required fields, clauses, references, commercial terms and variant schedule."""

    template_id: TemplateId
    template_version: SemVer
    title: NonEmptyStr
    form: Literal["full_text", "heads_of_terms"]
    text_file: NonEmptyStr
    text_sha256: Sha256
    legal_review: LegalReview
    executed_as: tuple[ExecutedAs, ...] = Field(min_length=1)
    required_fields: tuple[RequiredField, ...] = Field(min_length=1)
    clauses: tuple[Clause, ...] = Field(min_length=1)
    #: The ``ref_id`` of every `ContractVersionRef` the text cites.
    references: tuple[SnakeId, ...]
    commercial_terms: tuple[CommercialTerm, ...]
    unavailable_terms: tuple[UnavailableTerm, ...]
    variants: tuple[VariantSchedule, ...]
    safety_hierarchy: SafetyHierarchy | NotApplicable
    control_authority: ControlAuthorityStaging | NotApplicable
    risk_carriers: tuple[RiskCarrierOption, ...] | NotApplicable

    @model_validator(mode="after")
    def _check_entry(self) -> TemplateEntry:
        tid = self.template_id.value
        if self.text_file != f"{tid}.md":
            raise ValueError(f"{tid}: the text file is {tid}.md")
        # The review binds the exact text: an edited text is a draft again.
        if isinstance(self.legal_review, CounselReviewed):
            if self.legal_review.reviewed_text_sha256 != self.text_sha256:
                raise ValueError(
                    f"{tid}: counsel reviewed another text; the template is a draft again"
                )
        fields = {f.field_id: f for f in self.required_fields}
        if len(fields) != len(self.required_fields):
            raise ValueError(f"{tid}: field ids repeat")
        clause_ids = [c.clause_id for c in self.clauses]
        if len(set(clause_ids)) != len(clause_ids):
            raise ValueError(f"{tid}: clause ids repeat")
        if "variant_schedule" not in clause_ids:
            raise ValueError(f"{tid}: every template has a variant_schedule clause (ADR-D1)")
        if len(set(self.references)) != len(self.references):
            raise ValueError(f"{tid}: references repeat")
        for f in self.required_fields:
            if isinstance(f.binds, str) and f.binds not in self.references:
                raise ValueError(f"{tid}: field {f.field_id} binds {f.binds}, not a reference")
        self._check_commercial_terms(fields)
        if tuple(v.variant for v in self.variants) != tuple(FinancingVariant):
            raise ValueError(f"{tid}: the three ADR-D1 variants, each once, in order")
        self._check_risk_carriers()
        return self

    def _check_commercial_terms(self, fields: Mapping[str, RequiredField]) -> None:
        tid = self.template_id.value
        executed = [t.executed_field for t in self.commercial_terms]
        if len(set(executed)) != len(executed):
            raise ValueError(f"{tid}: two commercial terms share an executed field")
        for term in self.commercial_terms:
            field = fields.get(term.executed_field)
            if field is None or field.kind is not FieldKind.QUANTITY:
                raise ValueError(
                    f"{tid}: {term.term_id} is executed as a required quantity field, with an ask"
                )
            if isinstance(term.price, ListPriceOnRecord):
                _, unit, owner = LIST_PRICES_ON_RECORD[term.price.price_id]
                if owner is not self.template_id:
                    raise ValueError(
                        f"{tid}: the list price {term.price.price_id.value} belongs to "
                        f"{owner.value} only"
                    )
                if field.unit is not unit:
                    raise ValueError(f"{tid}: {term.executed_field} is not in {unit.value}")

    def _check_risk_carriers(self) -> None:
        if isinstance(self.risk_carriers, NotApplicable):
            return
        tid = self.template_id.value
        if tuple(o.carrier for o in self.risk_carriers) != tuple(RiskCarrier):
            raise ValueError(f"{tid}: each risk carrier once, in order")
        available = {
            o.admission.issuer_role
            for o in self.risk_carriers
            if isinstance(o.admission, CarrierAvailable)
        }
        issuers = {r for e in self.executed_as for r in e.admissible_issuers}
        if issuers != available:
            raise ValueError(
                f"{tid}: only an available risk carrier may issue the executed copy "
                f"({sorted(available)}), not {sorted(issuers)}"
            )


class ContractTemplateSet(ContractModel):
    """The released ``contract_template_set`` (WP-GRC-09): the seven templates, the contracts
    they reference by version, and the steps only a person may take."""

    contract: Literal["IC-15"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    set_id: Literal["contract_template_set"]
    set_version: SemVer
    work_package: Literal["WP-GRC-09"]
    templates: tuple[TemplateEntry, ...]
    references: tuple[ContractVersionRef, ...]
    person_only_steps: tuple[PersonOnlyStep, ...]

    @model_validator(mode="after")
    def _check_set(self) -> ContractTemplateSet:
        if tuple(t.template_id for t in self.templates) != tuple(TemplateId):
            raise ValueError("the set holds the seven templates, each once, in WP-GRC-09 order")
        ref_ids = [r.ref_id for r in self.references]
        if len(set(ref_ids)) != len(ref_ids):
            raise ValueError("reference ids repeat")
        for t in self.templates:
            missing = set(t.references) - set(ref_ids)
            if missing:
                raise ValueError(f"{t.template_id.value} cites undeclared references {missing}")
        msa = self.template(TemplateId.SOFTWARE_DISPATCH_MSA)
        absent = [c for c in MSA_NAMED_CLAUSES if c not in {c.clause_id for c in msa.clauses}]
        if absent:
            raise ValueError(f"the MSA lacks its named clauses {absent}")
        if not isinstance(msa.safety_hierarchy, SafetyHierarchy):
            raise ValueError("the MSA carries the spec §8 safety hierarchy")
        if not isinstance(msa.control_authority, ControlAuthorityStaging):
            raise ValueError("the MSA carries the ADR-D2 control-authority staging")
        wrapper = self.template(TemplateId.PERFORMANCE_WRAPPER)
        if isinstance(wrapper.risk_carriers, NotApplicable):
            raise ValueError("the performance wrapper states its risk carriers")
        if not any("WP-GRC-13" in u.until for u in wrapper.unavailable_terms):
            raise ValueError("the performance wrapper's insurer terms wait on WP-GRC-13")
        step_ids = [s.step_id for s in self.person_only_steps]
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("person-only step ids repeat")
        reviewed = {
            tid
            for s in self.person_only_steps
            if s.kind is PersonOnlyKind.LEGAL_REVIEW
            for tid in s.templates
        }
        for t in self.templates:
            if isinstance(t.legal_review, DraftNeedsLegalReview) and t.template_id not in reviewed:
                raise ValueError(
                    f"{t.template_id.value} is a draft with no person-only legal-review step"
                )
        return self

    def template(self, template_id: TemplateId) -> TemplateEntry:
        return next(t for t in self.templates if t.template_id is template_id)

    def reference(self, ref_id: str) -> ContractVersionRef:
        return next(r for r in self.references if r.ref_id == ref_id)

    def content_hash(self) -> str:
        return content_hash(self)


# --------------------------------------------------------------------------------------------
# One executed contract
# --------------------------------------------------------------------------------------------


class TextValue(ContractModel):
    kind: Literal["text"]
    value: NonEmptyStr


class DateValue(ContractModel):
    kind: Literal["date"]
    value: IsoDate


class QuantityValue(ContractModel):
    kind: Literal["quantity"]
    unit: QuantityUnit
    value: NonNegativeInt


class ContractInstanceValue(ContractModel):
    """An instance of another contract, by its content hash."""

    kind: Literal["contract_instance"]
    contract: ContractId
    contract_version: Version1x
    content_hash: Sha256


FieldValue = Annotated[
    TextValue | DateValue | QuantityValue | ContractInstanceValue | Unknown | NotApplicable,
    Field(discriminator="kind"),
]


class FieldEntry(ContractModel):
    field_id: SnakeId
    value: FieldValue


class ExecutedContractTerms(ContractModel):
    """The terms of one executed contract: which template text it was made from, the IC-4 object
    of the signed copy, the financing variant, and a value for every required field. A field not
    yet known is `Unknown` with its ask; a field that does not apply is `NotApplicable`."""

    contract: Literal["IC-15"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    template_id: TemplateId
    template_version: SemVer
    text_sha256: Sha256
    executed_copy: EvidenceRef
    variant: FinancingVariant
    values: tuple[FieldEntry, ...]

    @model_validator(mode="after")
    def _check_unique(self) -> ExecutedContractTerms:
        ids = [v.field_id for v in self.values]
        if len(set(ids)) != len(ids):
            raise ValueError("a field is stated twice")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


# --------------------------------------------------------------------------------------------
# Loading and text checks
# --------------------------------------------------------------------------------------------


class TemplateRefused(ValueError):
    """The template, its text or an executed contract does not meet the manifest."""


class DraftTemplateRefused(TemplateRefused):
    """The template has not been reviewed by counsel, and the caller did not admit drafts."""


class TemplateTextMismatch(TemplateRefused):
    """The text on disk is not the text the manifest pins."""


def text_sha256(text: str) -> str:
    """``sha256:<hex>`` of the UTF-8 bytes of a template text."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_template_set(path: Path | None = None) -> ContractTemplateSet:
    """The packaged manifest (or the one at ``path``), validated."""
    source = MANIFEST_PATH if path is None else path
    return ContractTemplateSet.model_validate_json(source.read_text(encoding="utf-8"))


def template_text(entry: TemplateEntry, directory: Path | None = None) -> str:
    """The text of ``entry``. Raises `TemplateTextMismatch` if it is not the pinned text."""
    text = ((TEMPLATE_DIR if directory is None else directory) / entry.text_file).read_text(
        encoding="utf-8"
    )
    if text_sha256(text) != entry.text_sha256:
        raise TemplateTextMismatch(
            f"{entry.text_file} is {text_sha256(text)}, but the manifest pins {entry.text_sha256}"
        )
    return text


def effective_review(entry: TemplateEntry, text: str) -> DraftNeedsLegalReview | CounselReviewed:
    """The review state of ``text``. It is a draft unless counsel reviewed exactly this text."""
    if text_sha256(text) != entry.text_sha256:
        return DraftNeedsLegalReview(
            kind="draft_needs_legal_review",
            reason="the text differs from the text the manifest pins",
        )
    return entry.legal_review


CLAUSE_ANCHOR: Final = re.compile(r"\[clause:([a-z][a-z0-9_]*)\]")
FIELD_TOKEN: Final = re.compile(r"\{\{field:([a-z][a-z0-9_]*)\}\}")
REF_TOKEN: Final = re.compile(r"\[\[ref:([a-z][a-z0-9_]*)\]\]")
#: A contract id written as prose. A text names a contract only through a reference token.
CONTRACT_ID_IN_PROSE: Final = re.compile(r"\bIC-[0-9]+\b")
#: The prose guard: a fixed term list. A line using a term names the artefact, so it must also
#: carry a reference token to the contract that defines it.
PROSE_GUARD_TERMS: Final[dict[str, str]] = {
    "envelope": "IC-3",
    "reserve partition": "IC-3",
    "verifier": "IC-8",
    "dataset manifest": "IC-8",
    "recompute": "IC-8",
    "method version": "IC-8",
}


def text_findings(entry: TemplateEntry, text: str, template_set: ContractTemplateSet) -> list[str]:
    """Every way ``text`` departs from its manifest ``entry``. An empty list means it conforms."""
    tid = entry.template_id.value
    out: list[str] = []
    lines = text.splitlines()
    if isinstance(entry.legal_review, DraftNeedsLegalReview):
        if not lines or lines[0] != f"> **{DRAFT_HEADER}**":
            out.append(f"{tid}: a draft's first line is the DRAFT header")
    anchors = CLAUSE_ANCHOR.findall(text)
    if anchors != [c.clause_id for c in entry.clauses]:
        out.append(f"{tid}: clause anchors {anchors} are not the manifest's clauses in order")
    fields = set(FIELD_TOKEN.findall(text))
    expected = {f.field_id for f in entry.required_fields}
    if fields != expected:
        out.append(
            f"{tid}: field tokens without a field {sorted(fields - expected)}, "
            f"fields without a token {sorted(expected - fields)}"
        )
    refs = set(REF_TOKEN.findall(text))
    if refs != set(entry.references):
        out.append(f"{tid}: reference tokens {sorted(refs)} are not {sorted(entry.references)}")
    declared = {r.ref_id: r for r in template_set.references}
    for number, line in enumerate(lines, start=1):
        if CONTRACT_ID_IN_PROSE.search(line):
            out.append(f"{tid}:{number}: a contract named in prose; use a reference token")
        cited = [declared[r] for r in REF_TOKEN.findall(line) if r in declared]
        lowered = line.lower()
        for term, contract in PROSE_GUARD_TERMS.items():
            if term in lowered and not any(r.contract == contract for r in cited):
                out.append(f"{tid}:{number}: '{term}' without a reference token to {contract}")
        for ref in cited:
            if isinstance(ref.status, NotYetFrozen):
                wp = ref.status.freezing_work_package
                if f"unavailable until {wp}" not in line:
                    out.append(
                        f"{tid}:{number}: {ref.ref_id} is cited without 'unavailable until {wp}'"
                    )
    for unavailable in entry.unavailable_terms:
        wanted = "unavailable until " + " / ".join(unavailable.until)
        if wanted not in text:
            out.append(f"{tid}: {unavailable.term_id} is not shown as '{wanted}'")
    if any(isinstance(t.price, ListPriceOnRecord) for t in entry.commercial_terms):
        if LIST_PRICE_RENDERING not in text:
            out.append(f"{tid}: a list price is cited without '{LIST_PRICE_RENDERING}'")
    for variant in entry.variants:
        if VARIANT_LABELS[variant.variant] not in text:
            out.append(f"{tid}: the variant schedule does not cover {variant.variant.value}")
    if isinstance(entry.safety_hierarchy, SafetyHierarchy):
        at = [text.find(SAFETY_TIER_LABELS[t]) for t in entry.safety_hierarchy.order]
        if -1 in at or at != sorted(at):
            out.append(f"{tid}: the safety hierarchy tiers are not written in order")
    return out


def _version(v: str) -> tuple[int, int, int]:
    major, minor, patch = (int(p) for p in v.split("."))
    return major, minor, patch


def live_contract_versions() -> dict[str, str]:
    """Each contract this package freezes today, and the version it writes."""
    from gridq.contracts import (
        audit,
        connection,
        curtailment,
        dispatch_envelope,
        evidence,
        identity,
        thermal_flex,
    )

    modules = (evidence, connection, curtailment, dispatch_envelope, identity, audit, thermal_flex)
    live = {m.CONTRACT_ID: m.CONTRACT_VERSION for m in modules}
    live[CONTRACT_ID] = CONTRACT_VERSION
    return live


def reference_findings(template_set: ContractTemplateSet, live: Mapping[str, str]) -> list[str]:
    """Every reference that no longer matches the contracts that exist. A ``not_yet_frozen``
    reference to a contract that now exists must be bound (made ``frozen``) before release."""
    out: list[str] = []
    for ref in template_set.references:
        version = live.get(ref.contract)
        if isinstance(ref.status, NotYetFrozen):
            if version is not None:
                out.append(
                    f"{ref.ref_id}: {ref.contract} now exists at {version}; bind the reference"
                )
            continue
        if version is None:
            out.append(f"{ref.ref_id}: {ref.contract} is not a contract in this package")
        elif _version(version)[0] != 1:
            out.append(f"{ref.ref_id}: {ref.contract} {version} is outside the 1.x range")
        elif _version(version) < _version(ref.status.minimum_version):
            out.append(
                f"{ref.ref_id}: {ref.contract} {version} is older than the minimum "
                f"{ref.status.minimum_version}"
            )
    return out


def template_set_findings(
    template_set: ContractTemplateSet,
    directory: Path | None = None,
    live: Mapping[str, str] | None = None,
) -> list[str]:
    """All text and reference findings for the set. Empty means it is consistent, not that any
    template is reviewed."""
    out: list[str] = []
    for entry in template_set.templates:
        try:
            text = template_text(entry, directory)
        except TemplateTextMismatch as exc:
            out.append(str(exc))
            continue
        out += text_findings(entry, text, template_set)
    out += reference_findings(template_set, live_contract_versions() if live is None else live)
    return out


# --------------------------------------------------------------------------------------------
# Checking one executed contract
# --------------------------------------------------------------------------------------------

_KIND_OF_VALUE: Final[dict[str, FieldKind]] = {
    "text": FieldKind.TEXT,
    "date": FieldKind.DATE,
    "quantity": FieldKind.QUANTITY,
    "contract_instance": FieldKind.CONTRACT_INSTANCE,
}


def _check_value(
    field: RequiredField, value: FieldValue, template_set: ContractTemplateSet
) -> str | None:
    if isinstance(value, Unknown | NotApplicable):
        return None
    if _KIND_OF_VALUE[value.kind] is not field.kind:
        return f"{field.field_id} is a {field.kind.value} field, not {value.kind}"
    if isinstance(value, QuantityValue) and value.unit is not field.unit:
        return f"{field.field_id} is in {field.unit}, not {value.unit.value}"
    if isinstance(value, ContractInstanceValue):
        ref = template_set.reference(str(field.binds))
        if isinstance(ref.status, NotYetFrozen):
            return (
                f"{field.field_id} binds {ref.contract}, which is unavailable until "
                f"{ref.status.freezing_work_package}: it can only be unknown"
            )
        if value.contract != ref.contract:
            return f"{field.field_id} binds {ref.contract}, not {value.contract}"
    return None


def check_executed_terms(
    terms: ExecutedContractTerms,
    template_set: ContractTemplateSet,
    evidence_objects: Iterable[EvidenceObject],
    *,
    admit_draft: bool = False,
    include_synthetic: bool = False,
) -> EvidenceObject:
    """Check ``terms`` against the manifest and return the IC-4 object of the signed copy.

    Refuses, in this order: a template version or text the manifest does not pin; a draft template
    unless ``admit_draft`` (`DraftTemplateRefused`); a required field missing or a field the
    template does not have; a value of the wrong kind or unit, or a known instance of a contract
    not yet frozen; a signed copy that does not resolve (`resolve_ref`, which refuses a
    self-issued record, and a synthetic one unless ``include_synthetic``); a signed copy of
    another evidence type, or from an issuer role the template does not admit for that type.
    """
    entry = template_set.template(terms.template_id)
    tid = entry.template_id.value
    if (terms.template_version, terms.text_sha256) != (entry.template_version, entry.text_sha256):
        raise TemplateRefused(f"{tid}: the terms cite a text the manifest does not pin")
    if isinstance(entry.legal_review, DraftNeedsLegalReview) and not admit_draft:
        raise DraftTemplateRefused(f"{tid} is a draft that needs legal review")
    stated = {v.field_id: v.value for v in terms.values}
    required = {f.field_id: f for f in entry.required_fields}
    missing = sorted(set(required) - set(stated))
    if missing:
        raise TemplateRefused(f"{tid}: required fields missing {missing}")
    extra = sorted(set(stated) - set(required))
    if extra:
        raise TemplateRefused(f"{tid}: fields the template does not have {extra}")
    for field_id, field in required.items():
        problem = _check_value(field, stated[field_id], template_set)
        if problem is not None:
            raise TemplateRefused(f"{tid}: {problem}")
    found = resolve_ref(terms.executed_copy, evidence_objects, include_synthetic=include_synthetic)
    admitted = {e.evidence_type: e.admissible_issuers for e in entry.executed_as}
    if found.evidence_type.value not in admitted:
        raise EvidenceRefused(
            f"{tid} is executed as {sorted(admitted)}, not {found.evidence_type.value}"
        )
    if found.issuer.role.value not in admitted[found.evidence_type.value]:
        raise EvidenceRefused(
            f"{tid}: a {found.evidence_type.value} issued by {found.issuer.role.value} is not "
            "admitted"
        )
    return found


def check_counsel_review(
    entry: TemplateEntry,
    evidence_objects: Iterable[EvidenceObject],
    *,
    include_synthetic: bool = False,
) -> EvidenceObject:
    """Resolve a reviewed template's opinion: a third-party ``legal_opinion``, never one Neura
    issued itself. Raises `DraftTemplateRefused` for a draft."""
    if not isinstance(entry.legal_review, CounselReviewed):
        raise DraftTemplateRefused(f"{entry.template_id.value} is a draft that needs legal review")
    return resolve_ref(
        entry.legal_review.opinion,
        evidence_objects,
        expected_type=EvidenceType.LEGAL_OPINION,
        include_synthetic=include_synthetic,
    )


def manifest_text(template_set: ContractTemplateSet) -> str:
    """The committed manifest's text form: indented, keys sorted, one trailing newline."""
    return (
        json.dumps(
            template_set.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False
        )
        + "\n"
    )
