"""IC-4 ``EvidenceObject``: frozen contract, version 1.2.0 (WP-BO-01, WP-BO-06, WP-GRC-09, spec
§10A, D1).

An evidence object is a **document, hashed**: who issued it, what it is, when it was issued, when
it takes effect and expires, which site or revenue stream it covers, and who checked it. It is the
record that replaces "assumed approved" (spec §11 D1) and feeds A6, B6 and packs. The issuer is a
third party, with one restricted exception (ADR-D10 option b, widened by the owner 2026-11-23):
Neura may issue its own **pre-registration and decision records** (`SELF_ISSUABLE_TYPES`: a
baseline method, a trial record, a decision record), marked on the wire by
``issuer.role == "neura_self_issued"`` and refused by every consumer function unless the caller
explicitly admits them, by type (`resolve_ref` with ``expected_type``, `assess`,
`require_third_party`). A decision record is evidence that Neura decided something on a date;
it is never evidence that the thing decided is true, or that a third party agreed to it.

Spec §10A IC-4 field list → this contract:

=====================  ============================================================
spec IC-4              field
=====================  ============================================================
type                   ``evidence_type`` (`EvidenceType`)
issuer                 ``issuer`` (`Issuer`: name, role, the issuer's own reference)
document hash          ``document_hash`` (``sha256:<hex>`` of the document bytes)
dates                  ``issued_on``, ``effective_from``
expiry                 ``expiry`` (`ExpiresOn` | `NoExpiry` | `Unknown`)
scope (site/stream)    ``scope`` (`SiteScope` | `StreamScope`)
verifier               ``verification`` (`Verified` with ``verifier`` | `Unverified`)
=====================  ============================================================

Acceptance (roadmap WP-BO-01), and where each is enforced:

- **Contract version is tagged.** ``CONTRACT_VERSION`` below, carried on every instance as a
  ``Literal`` so a payload of another version is refused; JSON Schema frozen at
  ``schemas/ic4_evidence_object.v1.2.0.schema.json`` (1.0.0 and 1.1.0 archived beside it, pinned
  by hash). Git tag to create when committed: ``GIT_TAG`` below. A 1.2.0 reader accepts every
  1.0.0 and 1.1.0 object with the same meaning and hash; an object labelled 1.0.0 or 1.1.0 may
  hold only that version's members (the down-level rule), so the label stays honest for an
  older reader.
- **An expired evidence object validates as expired, not as absent.** Expiry never makes
  construction fail: `EvidenceObject.status_at` returns ``EXPIRED``, and `assess` reports
  ``expired`` for a matching expired object, keeping ``absent`` for "no matching object at all".
- **An assumption can never be serialised as an evidence object.** See `_refuse_assumptions`:
  assumption-shaped payloads, GridQ assumption instances, a subclass and an empty-document hash
  are all refused, and `serialise_evidence` accepts only this exact type. Neura is not an
  `IssuerRole`; the separate `SelfIssuerRole` may issue only `SELF_ISSUABLE_TYPES`, may not
  verify its own record, and must name and date a decision record (`_check_version_and_issuer`).

Determinism: nothing here reads the clock. Every status is computed *as of* an explicit date.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Final, Literal, final

from pydantic import BaseModel, Field, model_validator

from gridq.contracts._base import (
    EMPTY_SHA256,
    ContractModel,
    IsoDate,
    NonEmptyStr,
    Sha256,
    Unknown,
    Version1x,
    canonical_json,
    content_hash,
)

__all__ = [
    "ALL_SITES",
    "ASSUMPTION_MARKER_KEYS",
    "ASSUMPTION_MARKER_VALUES",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "GIT_TAG",
    "SELF_ISSUABLE_TYPES",
    "SUPPORTED_VERSIONS",
    "V1_0_0_EVIDENCE_TYPES",
    "V1_0_0_ISSUER_ROLES",
    "V1_1_0_EVIDENCE_TYPES",
    "EvidenceCheck",
    "EvidenceNotFound",
    "EvidenceObject",
    "EvidenceRef",
    "EvidenceRefused",
    "EvidenceStatus",
    "EvidenceType",
    "ExpiresOn",
    "Issuer",
    "IssuerRole",
    "NoExpiry",
    "SelfIssuedEvidenceRefused",
    "SelfIssuerRole",
    "SiteScope",
    "StreamScope",
    "SyntheticEvidenceRefused",
    "Unverified",
    "VerificationMethod",
    "Verified",
    "VerifierRole",
    "assess",
    "evidence_ref",
    "require_third_party",
    "resolve_ref",
    "scope_covers",
    "serialise_evidence",
]

CONTRACT_ID: Final = "IC-4"
CONTRACT_VERSION: Final = "1.2.0"
#: Every version this module reads. The last one is the version it writes.
SUPPORTED_VERSIONS: Final = ("1.0.0", "1.1.0", "1.2.0")
#: The annotated git tag to create on the commit that lands this version (not created here).
GIT_TAG: Final = "contracts/ic4-evidence-object/v1.2.0"

#: Stream-scoped evidence that applies to every site (e.g. a market rule for one stream).
ALL_SITES: Final = "ALL_SITES"


class EvidenceType(StrEnum):
    """What the document is. The fifteen 1.0.0 members are the spec §11 D1 list plus the two
    kinds WP-GRC-03 and A6 need. 1.1.0 appends seventeen: the agreements, opinions,
    certificates, reports, licences, registrations and data records the roadmap's acceptance
    criteria name, and the three kinds Neura may also issue itself (a baseline method, a trial
    record, a decision record). 1.2.0 appends five: the executed counterparts of the WP-GRC-09
    contract templates that no earlier member fits (an assessment statement of work, a master
    services agreement, a site access and installation agreement, a lender reliance letter, a
    performance wrapper agreement). Their required fields live in IC-15, not here.

    **There is no assumption-like member, by design.** A test asserts that no member's value
    names an assumption, a derivation, a placeholder or an approval-by-assumption.
    """

    MPAN_REGISTRATION = "mpan_registration"
    CONNECTION_OFFER = "connection_offer"
    CONNECTION_AGREEMENT = "connection_agreement"
    G99_APPROVAL = "g99_approval"
    G100_COMPLIANCE = "g100_compliance"
    BMU_REGISTRATION = "bmu_registration"
    CMU_PREQUALIFICATION = "cmu_prequalification"
    CAPACITY_AGREEMENT = "capacity_agreement"
    DNO_FLEXIBILITY_CONTRACT = "dno_flexibility_contract"
    AGGREGATOR_AGREEMENT = "aggregator_agreement"
    METER_COMMISSIONING_CERTIFICATE = "meter_commissioning_certificate"
    CUSTOMER_CONSENT = "customer_consent"
    CRITICAL_FACILITIES_SIGNOFF = "critical_facilities_signoff"
    #: A regulator / system-operator publication backing a §12 register row (WP-GRC-03).
    REGULATORY_PUBLICATION = "regulatory_publication"
    #: A counterparty's written rate or price (A6 sourcing), e.g. an aggregator quote.
    COUNTERPARTY_RATE = "counterparty_rate"
    # ----- added in 1.1.0 (WP-BO-06; src/gridq/contracts/IC4_INVENTORY.md maps each to its user)
    #: An executed customer DPA.
    DATA_PROCESSING_AGREEMENT = "data_processing_agreement"
    #: An executed DSA with a DNO, NESO or Elexon.
    DATA_SHARING_AGREEMENT = "data_sharing_agreement"
    #: An executed NDA.
    NON_DISCLOSURE_AGREEMENT = "non_disclosure_agreement"
    #: Counsel's written opinion.
    LEGAL_OPINION = "legal_opinion"
    #: A certificate from an accredited body (Cyber Essentials Plus, ISO 27001, meter calibration).
    CONFORMITY_CERTIFICATE = "conformity_certificate"
    #: A third party's report of a technical assessment (RMS/EMT, independent engineer).
    TECHNICAL_REPORT = "technical_report"
    #: A data provider's licence or written authorisation.
    DATA_LICENCE = "data_licence"
    #: A counterparty's signed agreement to a definition, method or tolerance fixed in advance.
    METHOD_AGREEMENT = "method_agreement"
    #: A customer or host SLA document.
    SERVICE_LEVEL_AGREEMENT = "service_level_agreement"
    #: A commissioning record issued or signed by a third party.
    COMMISSIONING_RECORD = "commissioning_record"
    #: A host's sign-off that Neura may write to site plant.
    CONTROL_AUTHORITY_SIGNOFF = "control_authority_signoff"
    #: An asset's record in the flexibility market asset register.
    FMAR_REGISTRATION = "fmar_registration"
    #: Meter data issued by the meter operator or host.
    METERED_DATA_RECORD = "metered_data_record"
    #: A third party's own data extract (BMS history, design data, constraint data).
    OPERATIONAL_DATA_RECORD = "operational_data_record"
    #: A counterfactual or baseline method document, registered before the data it will judge.
    BASELINE_METHOD = "baseline_method"
    #: The record or report of a flex trial.
    TRIAL_RECORD = "trial_record"
    #: A recorded decision of the issuing party (for Neura: an ADR or board minute). Evidence that
    #: the decision was made on a date; never evidence that what it decides is true.
    DECISION_RECORD = "decision_record"
    # ----- added in 1.2.0 (WP-GRC-09; required fields: IC-15 `contract_templates`)
    #: The customer's or host's signed copy of an Assessment statement of work.
    ASSESSMENT_STATEMENT_OF_WORK = "assessment_statement_of_work"
    #: The customer's or host's signed copy of the Software and Dispatch master services agreement.
    MASTER_SERVICES_AGREEMENT = "master_services_agreement"
    #: The host's signed copy of a site access and installation agreement.
    SITE_ACCESS_INSTALLATION_AGREEMENT = "site_access_installation_agreement"
    #: The lender's countersigned copy of a reliance letter.
    LENDER_RELIANCE_LETTER = "lender_reliance_letter"
    #: The risk carrier's signed performance wrapper agreement.
    PERFORMANCE_WRAPPER_AGREEMENT = "performance_wrapper_agreement"


#: Not in the docstring on purpose: the docstring is copied into the IC-2 and IC-11 schemas. Neura's
#: one restricted issuer role is `SelfIssuerRole` (IC-4 1.1.0, ADR-D10 option b), a separate enum so
#: it can never be an IC-2 publisher role or an IC-11 ``on_behalf_of``.
class IssuerRole(StrEnum):
    """Who issued the document. **Neura is not an issuer role**: a claim Neura authors about
    itself or a site is an assumption or a derived record, never evidence. Neura may *verify*.
    """

    DNO = "dno"
    IDNO = "idno"
    NESO = "neso"
    ELEXON = "elexon"
    EMR_SETTLEMENT = "emr_settlement"
    OFGEM = "ofgem"
    GOVERNMENT = "government"
    SUPPLIER = "supplier"
    AGGREGATOR = "aggregator"
    CUSTOMER = "customer"
    HOST = "host"
    METER_OPERATOR = "meter_operator"
    OEM = "oem"
    INSURER = "insurer"
    LENDER = "lender"
    INDEPENDENT_ENGINEER = "independent_engineer"
    LEGAL_ADVISER = "legal_adviser"
    CERTIFICATION_BODY = "certification_body"
    DATA_PROVIDER = "data_provider"
    FUNDER = "funder"
    RESEARCH_PARTNER = "research_partner"
    INDEPENDENT_VERIFIER = "independent_verifier"


class VerifierRole(StrEnum):
    """Who checked the document against its issuer."""

    NEURA_INTERNAL = "neura_internal"
    INDEPENDENT_THIRD_PARTY = "independent_third_party"


class VerificationMethod(StrEnum):
    """How the verifier checked it."""

    CONFIRMED_WITH_ISSUER = "confirmed_with_issuer"
    CHECKED_AGAINST_ISSUER_REGISTER = "checked_against_issuer_register"
    RETRIEVED_FROM_ISSUER_PUBLICATION = "retrieved_from_issuer_publication"
    DOCUMENT_INSPECTION = "document_inspection"


class EvidenceStatus(StrEnum):
    """The state of one evidence object, or of a scope's evidence, *as of* a date.

    ``ABSENT`` is only ever produced by `assess` when no object matches. An object that exists
    but has expired is ``EXPIRED``; the two are never conflated.
    """

    VALID = "valid"
    VALIDITY_UNKNOWN = "validity_unknown"
    NOT_YET_EFFECTIVE = "not_yet_effective"
    EXPIRED = "expired"
    ABSENT = "absent"


class SelfIssuerRole(StrEnum):
    """Neura as issuer of its own pre-registration and decision records (ADR-D10 option b). The
    value is the self-issued marker: it is on the wire in every such object. Not an
    `IssuerRole`, so it can never be a publisher role (IC-2) or the party an instruction serves
    (IC-11)."""

    NEURA_SELF_ISSUED = "neura_self_issued"


#: The only evidence types Neura may issue itself (ADR-D10 option b, widened by the owner on
#: 2026-11-23): its pre-registration records and its own decision records. Third parties may
#: issue these types too. Widening this set widens ADR-D10, which is the owner's decision.
SELF_ISSUABLE_TYPES: Final[frozenset[EvidenceType]] = frozenset(
    {EvidenceType.BASELINE_METHOD, EvidenceType.TRIAL_RECORD, EvidenceType.DECISION_RECORD}
)

#: The fifteen IC-4 1.0.0 evidence types, written out (not computed from the enum) so the
#: down-level rule cannot drift when the enum grows. A test reads them from the archived schema.
V1_0_0_EVIDENCE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "mpan_registration",
        "connection_offer",
        "connection_agreement",
        "g99_approval",
        "g100_compliance",
        "bmu_registration",
        "cmu_prequalification",
        "capacity_agreement",
        "dno_flexibility_contract",
        "aggregator_agreement",
        "meter_commissioning_certificate",
        "customer_consent",
        "critical_facilities_signoff",
        "regulatory_publication",
        "counterparty_rate",
    }
)

#: The thirty-two IC-4 1.1.0 evidence types, written out for the same reason (WP-GRC-09). An object
#: labelled 1.1.0 holds only these (R7). 1.2.0 adds no role, so no 1.1.0 role list is needed.
V1_1_0_EVIDENCE_TYPES: Final[frozenset[str]] = V1_0_0_EVIDENCE_TYPES | frozenset(
    {
        "data_processing_agreement",
        "data_sharing_agreement",
        "non_disclosure_agreement",
        "legal_opinion",
        "conformity_certificate",
        "technical_report",
        "data_licence",
        "method_agreement",
        "service_level_agreement",
        "commissioning_record",
        "control_authority_signoff",
        "fmar_registration",
        "metered_data_record",
        "operational_data_record",
        "baseline_method",
        "trial_record",
        "decision_record",
    }
)

#: The sixteen IC-4 1.0.0 issuer roles, written out for the same reason.
V1_0_0_ISSUER_ROLES: Final[frozenset[str]] = frozenset(
    {
        "dno",
        "idno",
        "neso",
        "elexon",
        "emr_settlement",
        "ofgem",
        "government",
        "supplier",
        "aggregator",
        "customer",
        "host",
        "meter_operator",
        "oem",
        "insurer",
        "lender",
        "independent_engineer",
    }
)


class Issuer(ContractModel):
    name: NonEmptyStr
    role: IssuerRole | SelfIssuerRole
    #: The issuer's own reference for the document (offer number, registration id...).
    reference: NonEmptyStr | Unknown


class ExpiresOn(ContractModel):
    """The evidence stops being valid **on** ``expires_on``: valid strictly before it."""

    kind: Literal["expires_on"]
    expires_on: IsoDate


class NoExpiry(ContractModel):
    """The document states no expiry (e.g. an enduring agreement). The reason says where."""

    kind: Literal["no_expiry"]
    reason: NonEmptyStr


class SiteScope(ContractModel):
    kind: Literal["site"]
    site_id: NonEmptyStr


class StreamScope(ContractModel):
    """A revenue stream, at one site or at ``ALL_SITES`` (a market-wide rule for the stream).

    ``stream`` is an upper-snake identifier; GridQ's `MarketStream` names are valid values but
    the contract does not import GridQ's enum (contracts are a leaf).
    """

    kind: Literal["stream"]
    stream: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]
    site_id: NonEmptyStr


class Verified(ContractModel):
    kind: Literal["verified"]
    verifier: NonEmptyStr
    verifier_role: VerifierRole
    method: VerificationMethod
    verified_on: IsoDate


class Unverified(ContractModel):
    """A document is held but nobody has yet checked it against its issuer."""

    kind: Literal["unverified"]
    reason: NonEmptyStr


#: Keys that only an assumption record carries (GridQ `DerivedAssumption`, `Assumption`, a study
#: basis register row). Their presence in an evidence payload is refused with an explicit message,
#: before the generic "extra field" error, so the reader learns *why*.
ASSUMPTION_MARKER_KEYS: Final[frozenset[str]] = frozenset(
    {
        "assumed",
        "assumed_approved",
        "assumption",
        "assumption_version",
        "basis",
        "confidence",
        "confidence_ceiling",
        "confirmation_ask",
        "derivation_method",
        "derived_by",
        "derived_on_release",
        "kind",
        "missing_input",
        "owner_or_reviewer",
        "rationale",
        "status",
        "study_basis",
        "value",
    }
)

#: Status tokens that mark an assumption. Refused as the exact value of any string in the payload.
ASSUMPTION_MARKER_VALUES: Final[frozenset[str]] = frozenset(
    {
        "assumed",
        "assumed_approved",
        "assumed_granted",
        "derived_input",
        "pending_permission",
        "placeholder",
        "needs_review",
        "all_streams_enabled_study_v1",
    }
)


def _string_values(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, Mapping):
        for v in node.values():
            yield from _string_values(v)
    elif isinstance(node, list | tuple):
        for v in node:
            yield from _string_values(v)


@final
class EvidenceObject(ContractModel):
    """IC-4 v1.2.0. One hashed document, issued by a third party or, for a baseline method, a
    trial record or a decision record only, by Neura with issuer role neura_self_issued (ADR-D10
    option b). Neura cannot verify its own record, and its decision record names and dates the
    decision. An object labelled 1.0.0 holds only 1.0.0 types and roles; one labelled 1.1.0 holds
    only 1.1.0 types. Final: it cannot be subclassed."""

    contract: Literal["IC-4"] = CONTRACT_ID
    contract_version: Literal["1.0.0", "1.1.0", "1.2.0"] = CONTRACT_VERSION
    #: ``synthetic_fixture`` marks schema-test data. Consumers (A6, packs) must refuse it;
    #: `assess` ignores it unless asked to include it.
    record_class: Literal["registered", "synthetic_fixture"]
    evidence_type: EvidenceType
    title: NonEmptyStr
    issuer: Issuer
    document_hash: Sha256
    issued_on: IsoDate
    effective_from: IsoDate | Unknown
    expiry: ExpiresOn | NoExpiry | Unknown = Field(discriminator="kind")
    scope: SiteScope | StreamScope = Field(discriminator="kind")
    verification: Verified | Unverified = Field(discriminator="kind")

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        # A subclass could add an "assumed" field or loosen a validator and still pass an
        # isinstance check. The contract is closed to extension; a new shape is a new version.
        raise TypeError("EvidenceObject is final: a new evidence shape is a new contract version")

    @model_validator(mode="before")
    @classmethod
    def _refuse_assumptions(cls, data: Any) -> Any:
        if isinstance(data, BaseModel) and not isinstance(data, EvidenceObject):
            raise ValueError(
                f"an assumption is not evidence: a {type(data).__name__} cannot be converted to "
                "an EvidenceObject. Evidence is a hashed third-party document; register the "
                "document, do not relabel the record."
            )
        if isinstance(data, Mapping):
            markers = sorted(ASSUMPTION_MARKER_KEYS.intersection(map(str, data.keys())))
            if markers:
                raise ValueError(
                    f"an assumption is not evidence: payload carries assumption fields {markers}"
                )
            tokens = sorted(ASSUMPTION_MARKER_VALUES.intersection(_string_values(data)))
            if tokens:
                raise ValueError(
                    f"an assumption is not evidence: payload carries assumption status {tokens}"
                )
        return data

    @model_validator(mode="after")
    def _check_document_and_dates(self) -> EvidenceObject:
        if self.document_hash == EMPTY_SHA256:
            raise ValueError(
                "document_hash is the hash of zero bytes: there is no document, so this is not "
                "evidence (an assumption has no document)"
            )
        if isinstance(self.expiry, ExpiresOn) and self.expiry.expires_on <= self.issued_on:
            raise ValueError("expiry.expires_on must be after issued_on")
        if (
            isinstance(self.effective_from, date)
            and isinstance(self.expiry, ExpiresOn)
            and self.expiry.expires_on <= self.effective_from
        ):
            raise ValueError("expiry.expires_on must be after effective_from")
        if isinstance(self.verification, Verified) and self.verification.verified_on < (
            self.issued_on
        ):
            raise ValueError("verification.verified_on cannot precede issued_on")
        return self

    @model_validator(mode="after")
    def _check_version_and_issuer(self) -> EvidenceObject:
        # R1, R2 (down-level rule): an object labelled 1.0.0 holds only what 1.0.0 allowed, so a
        # 1.0.0 reader never meets a member it cannot read under that label.
        if self.contract_version == "1.0.0":
            if self.evidence_type.value not in V1_0_0_EVIDENCE_TYPES:
                raise ValueError(
                    f"evidence_type {self.evidence_type.value} is not in IC-4 1.0.0: label the "
                    "object 1.1.0"
                )
            if self.issuer.role.value not in V1_0_0_ISSUER_ROLES:
                raise ValueError(
                    f"issuer role {self.issuer.role.value} is not in IC-4 1.0.0: label the "
                    "object 1.1.0"
                )
        if self.is_self_issued:
            # R3 (ADR-D10 option b): Neura issues only its pre-registration and decision records.
            if self.evidence_type not in SELF_ISSUABLE_TYPES:
                raise ValueError(
                    "Neura may self-issue only its pre-registration and decision records "
                    f"({', '.join(sorted(t.value for t in SELF_ISSUABLE_TYPES))}), not "
                    f"{self.evidence_type.value} (ADR-D10 option b)"
                )
            # R4: Neura checking Neura's own record is not a check.
            if (
                isinstance(self.verification, Verified)
                and self.verification.verifier_role is VerifierRole.NEURA_INTERNAL
            ):
                raise ValueError(
                    "a self-issued record cannot verify its own document: leave it unverified, "
                    "or have an independent third party countersign it"
                )
            if self.evidence_type is EvidenceType.DECISION_RECORD:
                # R5: Neura's decision record names the decision (e.g. the ADR id).
                if isinstance(self.issuer.reference, Unknown):
                    raise ValueError(
                        "a decision record names its decision: issuer.reference must be the "
                        "decision's own id (e.g. the ADR id), not unknown"
                    )
                # R6: and says when it takes effect.
                if isinstance(self.effective_from, Unknown):
                    raise ValueError(
                        "a decision record states when it takes effect: effective_from must be "
                        "a date, not unknown"
                    )
        # R7 (down-level rule, WP-GRC-09): an object labelled 1.1.0 holds only 1.1.0 types.
        # Checked after R3 to R6, so a self-issued object reports its issuer violation first.
        if self.contract_version == "1.1.0":
            if self.evidence_type.value not in V1_1_0_EVIDENCE_TYPES:
                raise ValueError(
                    f"evidence_type {self.evidence_type.value} is not in IC-4 1.1.0: label the "
                    "object 1.2.0"
                )
        return self

    # ----- derived views (no clock: every status is as of an explicit date) -----

    @property
    def is_verified(self) -> bool:
        return isinstance(self.verification, Verified)

    @property
    def is_self_issued(self) -> bool:
        """True when Neura issued this record itself (``issuer.role == "neura_self_issued"``)."""
        return isinstance(self.issuer.role, SelfIssuerRole)

    def status_at(self, as_of: date) -> EvidenceStatus:
        """Validity of this object on ``as_of``. Expired is a status, never a validation error."""
        if isinstance(self.expiry, ExpiresOn) and as_of >= self.expiry.expires_on:
            return EvidenceStatus.EXPIRED
        if isinstance(self.effective_from, date) and as_of < self.effective_from:
            return EvidenceStatus.NOT_YET_EFFECTIVE
        if isinstance(self.effective_from, Unknown) or isinstance(self.expiry, Unknown):
            return EvidenceStatus.VALIDITY_UNKNOWN
        return EvidenceStatus.VALID

    def content_hash(self) -> str:
        """``sha256:<hex>`` of the canonical JSON: the identity a registry references."""
        return content_hash(self)


class EvidenceRef(ContractModel):
    """A reference to an IC-4 instance by its content hash (what IC-1 and others embed)."""

    kind: Literal["evidence_ref"]
    contract: Literal["IC-4"]
    contract_version: Version1x
    evidence_hash: Sha256


def evidence_ref(obj: EvidenceObject) -> EvidenceRef:
    """A reference to ``obj`` that states ``obj``'s own version (a 1.0.0 object stays 1.0.0)."""
    return EvidenceRef(
        kind="evidence_ref",
        contract=CONTRACT_ID,
        contract_version=obj.contract_version,
        evidence_hash=obj.content_hash(),
    )


def serialise_evidence(obj: EvidenceObject) -> str:
    """Canonical JSON of an evidence object; the only sanctioned serialiser.

    Accepts **exactly** `EvidenceObject` (statically via the signature, and at runtime, since the
    signature alone cannot stop an untyped caller). The object is re-validated from its own dump,
    so a value smuggled past construction (``model_construct``) is refused here too.
    """
    if type(obj) is not EvidenceObject:
        raise TypeError(
            f"only an EvidenceObject can be serialised as evidence, not {type(obj).__name__}"
        )
    EvidenceObject.model_validate_json(canonical_json(obj))
    return canonical_json(obj)


# --------------------------------------------------------------------------------------------
# Consumer refusals: third-party evidence is the default (ADR-D10 option b)
# --------------------------------------------------------------------------------------------


class EvidenceRefused(ValueError):
    """The object exists but is not admissible for the use the caller asked about."""


class SelfIssuedEvidenceRefused(EvidenceRefused):
    """Neura issued the object itself, and the caller did not admit self-issued records."""


class SyntheticEvidenceRefused(EvidenceRefused):
    """The object is a synthetic schema-test fixture, and the caller did not include fixtures."""


class EvidenceNotFound(LookupError):
    """No object among those given has the referenced content hash."""


def require_third_party(obj: EvidenceObject) -> EvidenceObject:
    """The refusal ADR-D10 requires. Returns ``obj`` when a third party issued it.
    Raises `SelfIssuedEvidenceRefused` when Neura issued it. Call this wherever
    third-party evidence is required; that is everywhere unless the README table says otherwise.

    Accepts exactly `EvidenceObject` (``TypeError`` otherwise) and re-validates it from its
    canonical JSON, as `serialise_evidence` does, so a value smuggled past construction
    (``model_construct``) is judged as the wire would carry it.
    """
    if type(obj) is not EvidenceObject:
        raise TypeError(
            f"only an EvidenceObject can be checked as evidence, not {type(obj).__name__}"
        )
    checked = EvidenceObject.model_validate_json(canonical_json(obj))
    if checked.is_self_issued:
        raise SelfIssuedEvidenceRefused(
            f"{checked.evidence_type.value} {checked.content_hash()} is self-issued by Neura; "
            "third-party evidence is required here"
        )
    return obj


def resolve_ref(
    ref: EvidenceRef,
    objects: Iterable[EvidenceObject],
    *,
    expected_type: EvidenceType | None = None,
    admit_self_issued: bool = False,
    include_synthetic: bool = False,
) -> EvidenceObject:
    """Find the object a reference points at, and refuse what the caller did not ask for.

    Admission of self-issued records is per type: ``admit_self_issued=True`` without an
    ``expected_type`` is a ``ValueError``, raised before any lookup, so a decision record can
    never sit in a baseline-method slot. Then, in this order: no object with the referenced hash
    (`EvidenceNotFound`); an object whose version is not the one the reference states
    (`EvidenceRefused`); an object whose type is not ``expected_type``, when given
    (`EvidenceRefused`); a synthetic fixture unless ``include_synthetic``
    (`SyntheticEvidenceRefused`); a self-issued record unless ``admit_self_issued``
    (`SelfIssuedEvidenceRefused`, via `require_third_party`).
    """
    if admit_self_issued and expected_type is None:
        raise ValueError(
            "admitting a self-issued record is per type: name the expected_type it may fill"
        )
    found = next((o for o in objects if o.content_hash() == ref.evidence_hash), None)
    if found is None:
        raise EvidenceNotFound(f"no evidence object has content hash {ref.evidence_hash}")
    if found.contract_version != ref.contract_version:
        raise EvidenceRefused(
            f"the reference states contract_version {ref.contract_version} but the object "
            f"{ref.evidence_hash} is {found.contract_version}"
        )
    if expected_type is not None and found.evidence_type is not expected_type:
        raise EvidenceRefused(
            f"expected evidence_type {expected_type.value} but {ref.evidence_hash} is "
            f"{found.evidence_type.value}"
        )
    if found.record_class == "synthetic_fixture" and not include_synthetic:
        raise SyntheticEvidenceRefused(
            f"{found.evidence_type.value} {ref.evidence_hash} is a synthetic schema-test fixture"
        )
    if not admit_self_issued:
        require_third_party(found)
    return found


# --------------------------------------------------------------------------------------------
# Registry-side check: present-and-expired is never reported as absent
# --------------------------------------------------------------------------------------------

# Best first. Expired sits below every "present" state but is still reported as expired.
_RANK: Final = (
    EvidenceStatus.VALID,
    EvidenceStatus.VALIDITY_UNKNOWN,
    EvidenceStatus.NOT_YET_EFFECTIVE,
    EvidenceStatus.EXPIRED,
)


class EvidenceCheck(ContractModel):
    """The answer to "is there evidence of type T for scope S as of D?"."""

    evidence_type: EvidenceType
    scope: SiteScope | StreamScope = Field(discriminator="kind")
    as_of: IsoDate
    status: EvidenceStatus
    #: Hash and status of every matching object, sorted by hash (deterministic).
    matches: tuple[tuple[Sha256, EvidenceStatus], ...]
    #: True when the best-status match is verified. False when absent.
    best_is_verified: bool
    #: True when the best-status match is self-issued by Neura (only possible when the caller
    #: admitted self-issued records). False when absent. A consumer labels such a result.
    best_is_self_issued: bool


def scope_covers(evidence_scope: SiteScope | StreamScope, query: SiteScope | StreamScope) -> bool:
    """Whether evidence held at ``evidence_scope`` covers a question asked at ``query``.

    A site-scoped document covers the site and every stream at it; a stream-scoped one covers
    that stream at its site, or at every site when its ``site_id`` is ``ALL_SITES``.
    """
    if isinstance(evidence_scope, SiteScope):
        return evidence_scope.site_id == query.site_id
    if not isinstance(query, StreamScope) or evidence_scope.stream != query.stream:
        return False
    return evidence_scope.site_id in (ALL_SITES, query.site_id)


def assess(
    objects: Iterable[EvidenceObject],
    *,
    evidence_type: EvidenceType,
    scope: SiteScope | StreamScope,
    as_of: date,
    include_synthetic: bool = False,
    admit_self_issued: bool = False,
) -> EvidenceCheck:
    """Evaluate a set of evidence objects for one (type, scope) as of a date.

    ``absent`` means no object of that type covers the scope. A matching object that has expired
    yields ``expired``. Synthetic fixtures are ignored unless ``include_synthetic``; records
    self-issued by Neura are ignored unless ``admit_self_issued`` (ADR-D10 option b), exactly as
    fixtures are. The ranking of what remains is unchanged from 1.0.0.
    """
    found: list[tuple[str, EvidenceStatus, bool, bool]] = []
    for obj in objects:
        if obj.record_class == "synthetic_fixture" and not include_synthetic:
            continue
        if obj.is_self_issued and not admit_self_issued:
            continue
        if obj.evidence_type is evidence_type and scope_covers(obj.scope, scope):
            found.append(
                (obj.content_hash(), obj.status_at(as_of), obj.is_verified, obj.is_self_issued)
            )
    found.sort(key=lambda t: t[0])
    if not found:
        return EvidenceCheck(
            evidence_type=evidence_type,
            scope=scope,
            as_of=as_of,
            status=EvidenceStatus.ABSENT,
            matches=(),
            best_is_verified=False,
            best_is_self_issued=False,
        )
    best = min(found, key=lambda t: (_RANK.index(t[1]), not t[2], t[0]))
    return EvidenceCheck(
        evidence_type=evidence_type,
        scope=scope,
        as_of=as_of,
        status=best[1],
        matches=tuple((h, s) for h, s, _, _ in found),
        best_is_verified=best[2],
        best_is_self_issued=best[3],
    )
