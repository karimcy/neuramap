"""The simulation handoff to PSAL: what an RMS/EMT study of the BESS needs (A3-04; D202).

**A closed, versioned catalogue.** `CATALOGUE` lists every input the RMS and EMT studies need
(`REQUIRED_INPUTS`) and the study cases they feed (`STUDY_CASES`), each with the clauses that ask
for it. Its content hash is embedded in every bundle, so PSAL confirms one exact list.
``catalogue_status`` stays ``neura_proposed`` until PSAL confirms that hash in a method agreement
(an IC-4 ``method_agreement`` issued by the research partner whose ``document_hash`` is the
catalogue hash); until then every bundle carries the person-only ask to PSAL.

**Every bundle item is exactly one of three things.** `Provided` (a value with its provenance),
`Asked` (the typed asks that would provide it) or `NotApplicable` (with a reason). There is no
fourth state, and there is no default. Site electrical data (fault level, X/R, transformer data,
the network equivalent) is `Provided` only from IC-4 evidence; otherwise it is `Asked`. The G99
generic 50 MVA short-circuit power (B.4.4.3) is a **separate item** typed as a public constant,
never a stand-in for the site's notified minimum fault level.

**Readiness is derived, not stored.** `case_readiness` computes, for each study case, `Ready`,
`ReadyGenericOnly` (the case can run on the G99 generic short-circuit power only: labelled not
site-compliant) or `Blocked` (with the asks). The screen record carries the derivation and checks
it against the bundle.

OEM models are referenced only by IC-4 `EvidenceRef`; the bundle never holds a model. This is not
a new IC contract: it is a release of WP-SITE-06, versioned by its schema and catalogue versions.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from fractions import Fraction
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from gridq.adequacy._common import ExactRational, RecordClass
from gridq.contracts._base import (
    ContractModel,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    Unknown,
    content_hash,
)
from gridq.contracts.connection import ConnectionAgreement, PccTerms, PowerFactorTerms, RampTerms
from gridq.contracts.evidence import (
    EvidenceObject,
    EvidenceRef,
    EvidenceStatus,
    EvidenceType,
    IssuerRole,
    VerificationMethod,
    Verified,
    require_third_party,
)
from gridq.ops.dossier_model import AskHolder, DossierAsk
from gridq.ramp_ridethrough._common import (
    OEM_NAME,
    PSAL_NAME,
    Cite,
    bess_ask,
    cite,
    exact,
    fault_level_ask,
    rational,
    site_ask,
    sorted_asks,
    x_over_r_ask,
)
from gridq.ramp_ridethrough.inputs import (
    BessDeclaration,
    Declarant,
    EvidencedQuantity,
    ReactiveRange,
    StudyInputs,
)
from gridq.ramp_ridethrough.ramp import ramp_terms_ask
from gridq.ramp_ridethrough.ridethrough import (
    CitedParameter,
    RideThroughFlag,
    TypeObligation,
    cited_parameter,
    pcc_ask,
)

__all__ = [
    "BUNDLE_SCHEMA",
    "CATALOGUE",
    "CATALOGUE_HASH",
    "Asked",
    "Blocked",
    "BundleItem",
    "Catalogue",
    "CaseReadiness",
    "Provided",
    "PsalBundle",
    "Ready",
    "ReadyGenericOnly",
    "build_bundle",
    "case_readiness",
    "catalogue_confirmation_ask",
]

BUNDLE_SCHEMA: Final = "gridq.ramp_ridethrough.psal_handoff"
CATALOGUE_SCHEMA: Final = "gridq.ramp_ridethrough.psal_catalogue"

Domain = Literal["rms", "emt", "rms_and_emt"]
ItemId = Literal[
    "pcc_voltage",
    "site_notified_minimum_fault_level",
    "g99_generic_minimum_short_circuit_power",
    "x_over_r",
    "pre_post_fault_short_circuit",
    "network_equivalent",
    "statutory_voltage_limits",
    "ic1_ramp_terms",
    "ic1_power_factor_terms",
    "ic1_anm_terms",
    "transformer_data",
    "transformer_saturation",
    "transformer_remanence_basis",
    "site_network_data",
    "customer_installation_equivalent",
    "bess_registered_capacity",
    "bess_rated_import_power",
    "bess_reactive_range",
    "oem_rms_model",
    "oem_emt_model",
    "ppc_settings",
    "protection_settings",
    "frt_manufacturer_information",
    "g99_type_and_frt_parameters",
]
CaseId = Literal[
    "frt_three_phase",
    "frt_unbalanced",
    "ffci",
    "post_fault_recovery",
    "pcs_trip_rvc",
    "transformer_energisation_rvc",
    "ppc_ramp_response",
]
SourceClass = Literal[
    "network_operator_evidence",
    "customer_evidence",
    "oem_evidence",
    "ic1_instance",
    "bess_declaration",
    "public_constant",
    "screen_derived",
]

#: Items that may be `Provided` only from IC-4 evidence (site electrical data and OEM documents).
EVIDENCE_ONLY: Final = frozenset(
    {
        "site_notified_minimum_fault_level",
        "x_over_r",
        "pre_post_fault_short_circuit",
        "network_equivalent",
        "statutory_voltage_limits",
        "transformer_data",
        "transformer_saturation",
        "site_network_data",
        "customer_installation_equivalent",
        "oem_rms_model",
        "oem_emt_model",
        "ppc_settings",
        "protection_settings",
        "frt_manufacturer_information",
    }
)


# ------------------------------------------------------------------------------------- catalogue


class CatalogueItem(ContractModel):
    item_id: ItemId
    domain: Domain
    source_class: SourceClass
    anchors: tuple[Cite, ...]
    #: The IC-1 field the item is read from, where it is an IC-1 term.
    ic1_fields: tuple[NonEmptyStr, ...]


class StudyCase(ContractModel):
    case_id: CaseId
    domain: Domain
    anchors: tuple[Cite, ...] = Field(min_length=1)
    requires: tuple[ItemId, ...] = Field(min_length=1)
    #: G99 B.4.4.3 lets the FRT study run at the generic 50 MVA where the DNO has notified no
    #: minimum; such a run is labelled not site-compliant.
    generic_fault_level_permitted: bool


class Catalogue(ContractModel):
    schema_name: Literal["gridq.ramp_ridethrough.psal_catalogue"] = CATALOGUE_SCHEMA
    catalogue_version: Literal["1.0.0"] = "1.0.0"
    items: tuple[CatalogueItem, ...]
    cases: tuple[StudyCase, ...]

    @model_validator(mode="after")
    def _closed(self) -> Catalogue:
        ids = [i.item_id for i in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("catalogue item ids are unique")
        for c in self.cases:
            missing = set(c.requires) - set(ids)
            if missing:
                raise ValueError(f"case {c.case_id} requires uncatalogued items {sorted(missing)}")
        return self


def _g(clause: str) -> Cite:
    return cite("G99_ISSUE_2", clause)


def _p(clause: str) -> Cite:
    return cite("P28_ISSUE_2", clause)


def _item(
    item_id: str,
    domain: str,
    source_class: str,
    anchors: tuple[Cite, ...],
    ic1_fields: tuple[str, ...] = (),
) -> CatalogueItem:
    return CatalogueItem(
        item_id=item_id,  # type: ignore[arg-type]
        domain=domain,  # type: ignore[arg-type]
        source_class=source_class,  # type: ignore[arg-type]
        anchors=anchors,
        ic1_fields=ic1_fields,
    )


REQUIRED_INPUTS: Final[tuple[CatalogueItem, ...]] = (
    _item("pcc_voltage", "rms_and_emt", "ic1_instance", (_g("4"), _p("6.1.2")), ("pcc",)),
    _item(
        "site_notified_minimum_fault_level",
        "rms_and_emt",
        "network_operator_evidence",
        (_p("6.4.1(a)"), _g("B.4.4.3"), _g("C.7.5.3")),
    ),
    _item("g99_generic_minimum_short_circuit_power", "rms", "public_constant", (_g("B.4.4.3"),)),
    _item("x_over_r", "rms_and_emt", "network_operator_evidence", (_p("6.1.5"), _p("6.3.5"))),
    _item(
        "pre_post_fault_short_circuit",
        "rms_and_emt",
        "network_operator_evidence",
        (_g("12.3.1.7(c)"),),
    ),
    _item(
        "network_equivalent",
        "rms_and_emt",
        "network_operator_evidence",
        (_g("6.3.3"), _g("12.3.1.7(c)")),
    ),
    _item("statutory_voltage_limits", "rms", "network_operator_evidence", (_p("6.4.1(c)"),)),
    _item("ic1_ramp_terms", "rms", "ic1_instance", (_g("10.3.4"),), ("ramp",)),
    _item("ic1_power_factor_terms", "rms", "ic1_instance", (), ("power_factor",)),
    _item(
        "ic1_anm_terms",
        "rms",
        "ic1_instance",
        (),
        ("anm_response_time_s", "signal_type", "signal_protocol"),
    ),
    _item("transformer_data", "rms_and_emt", "customer_evidence", (_g("6.4.3.3"),)),
    _item("transformer_saturation", "emt", "customer_evidence", (_p("6.4.1"), _g("9.3.4"))),
    _item("transformer_remanence_basis", "emt", "public_constant", (_p("6.4.1(b)"),)),
    _item("site_network_data", "rms_and_emt", "customer_evidence", (_g("6.4.2"),)),
    _item(
        "customer_installation_equivalent",
        "rms_and_emt",
        "customer_evidence",
        (_g("B.4.1.3 (first)"),),
    ),
    _item("bess_registered_capacity", "rms_and_emt", "bess_declaration", (_g("4"),)),
    _item("bess_rated_import_power", "rms_and_emt", "bess_declaration", (_g("4"),)),
    _item("bess_reactive_range", "rms", "bess_declaration", (_p("6.3.5"),)),
    _item("oem_rms_model", "rms", "oem_evidence", (_g("6.3.7-6.3.8.4"), _g("B.4.1.2"))),
    _item("oem_emt_model", "emt", "oem_evidence", (_g("6.3.7-6.3.8.4"), _g("B.4.1.2"))),
    _item("ppc_settings", "rms_and_emt", "oem_evidence", (_g("B.4.1.2"), _g("10.3.4"))),
    _item(
        "protection_settings",
        "rms_and_emt",
        "oem_evidence",
        (_g("12.3.1.7(d)"), _g("10.6.7")),
    ),
    _item(
        "frt_manufacturer_information",
        "rms_and_emt",
        "oem_evidence",
        (_g("B.4.1.3 (second)"), _g("B.4.4.5"), _g("C.7.5.5")),
    ),
    _item(
        "g99_type_and_frt_parameters",
        "rms_and_emt",
        "screen_derived",
        (_g("4"), _g("12.3"), _g("12.6"), _g("13.3"), _g("13.6")),
    ),
)

_FRT_ITEMS: Final = (
    "pcc_voltage",
    "site_notified_minimum_fault_level",
    "x_over_r",
    "pre_post_fault_short_circuit",
    "network_equivalent",
    "transformer_data",
    "site_network_data",
    "customer_installation_equivalent",
    "bess_registered_capacity",
    "bess_reactive_range",
    "oem_rms_model",
    "oem_emt_model",
    "ppc_settings",
    "protection_settings",
    "frt_manufacturer_information",
    "g99_type_and_frt_parameters",
)


def _case(
    case_id: str, domain: str, anchors: tuple[Cite, ...], requires: tuple[str, ...], generic: bool
) -> StudyCase:
    return StudyCase(
        case_id=case_id,  # type: ignore[arg-type]
        domain=domain,  # type: ignore[arg-type]
        anchors=anchors,
        requires=requires,  # type: ignore[arg-type]
        generic_fault_level_permitted=generic,
    )


STUDY_CASES: Final[tuple[StudyCase, ...]] = (
    _case(
        "frt_three_phase",
        "rms_and_emt",
        (_g("B.4.4"), _g("C.7.5"), _g("12.3"), _g("13.3")),
        _FRT_ITEMS,
        True,
    ),
    _case(
        "frt_unbalanced",
        "rms_and_emt",
        (_g("B.4.4"), _g("C.7.5"), _g("12.3"), _g("13.3")),
        _FRT_ITEMS,
        True,
    ),
    _case("ffci", "emt", (_g("12.6"), _g("13.6")), _FRT_ITEMS, True),
    _case(
        "post_fault_recovery",
        "rms",
        (_g("12.3.1.7(e)"),),
        (
            "pcc_voltage",
            "site_notified_minimum_fault_level",
            "x_over_r",
            "network_equivalent",
            "bess_registered_capacity",
            "oem_rms_model",
            "ppc_settings",
            "g99_type_and_frt_parameters",
        ),
        True,
    ),
    _case(
        "pcs_trip_rvc",
        "rms",
        (_p("5.3.2 Table 4"), _p("6.3.5"), _g("9.3.2")),
        (
            "pcc_voltage",
            "site_notified_minimum_fault_level",
            "x_over_r",
            "statutory_voltage_limits",
            "bess_registered_capacity",
            "bess_rated_import_power",
            "bess_reactive_range",
            "oem_rms_model",
        ),
        False,
    ),
    _case(
        "transformer_energisation_rvc",
        "emt",
        (_p("6.4.1"), _p("6.4.2"), _g("9.3.4")),
        (
            "pcc_voltage",
            "site_notified_minimum_fault_level",
            "x_over_r",
            "statutory_voltage_limits",
            "network_equivalent",
            "transformer_data",
            "transformer_saturation",
            "transformer_remanence_basis",
        ),
        False,
    ),
    _case(
        "ppc_ramp_response",
        "rms",
        (_g("10.3.4"),),
        (
            "ic1_ramp_terms",
            "ic1_power_factor_terms",
            "ic1_anm_terms",
            "bess_registered_capacity",
            "bess_rated_import_power",
            "oem_rms_model",
            "ppc_settings",
        ),
        False,
    ),
)

CATALOGUE: Final = Catalogue(items=REQUIRED_INPUTS, cases=STUDY_CASES)
CATALOGUE_HASH: Final = content_hash(CATALOGUE)


# ------------------------------------------------------------------------------------ the values


class PublicConstantValue(ContractModel):
    """A published figure, cited: never a site value."""

    kind: Literal["public_constant"]
    parameters: tuple[CitedParameter, ...] = Field(min_length=1)


class DeclaredValue(ContractModel):
    kind: Literal["declared_value"]
    value: ExactRational
    unit: Literal["W"]
    declared_by: Declarant


class DeclaredReactive(ContractModel):
    kind: Literal["declared_reactive_range"]
    reactive_range: ReactiveRange
    declared_by: Declarant


class Ic1Field(ContractModel):
    name: NonEmptyStr
    value: ExactRational | NonEmptyStr | NotApplicable


class Ic1Term(ContractModel):
    """IC-1 terms, exact (the agreement's floats as the decimals they were written as)."""

    kind: Literal["ic1_term"]
    agreement_hash: Sha256
    fields: tuple[Ic1Field, ...] = Field(min_length=1)


class G99Parameters(ContractModel):
    kind: Literal["g99_type_parameters"]
    obligation: TypeObligation


Value = Annotated[
    EvidencedQuantity
    | EvidenceRef
    | PublicConstantValue
    | DeclaredValue
    | DeclaredReactive
    | Ic1Term
    | G99Parameters,
    Field(discriminator="kind"),
]

_PROVENANCE = {
    "evidenced_quantity": "ic4_evidence",
    "evidence_ref": "ic4_evidence",
    "public_constant": "public_constant",
    "declared_value": "bess_declaration",
    "declared_reactive_range": "bess_declaration",
    "ic1_term": "ic1_instance",
    "g99_type_parameters": "ridethrough_screen",
}


class Provided(ContractModel):
    kind: Literal["provided"]
    value: Value
    provenance: Literal[
        "ic4_evidence", "public_constant", "bess_declaration", "ic1_instance", "ridethrough_screen"
    ]

    @model_validator(mode="after")
    def _provenance_matches(self) -> Provided:
        if _PROVENANCE[self.value.kind] != self.provenance:
            raise ValueError(f"a {self.value.kind} value has provenance {self.provenance}")
        return self


class Asked(ContractModel):
    kind: Literal["asked"]
    asks: tuple[DossierAsk, ...] = Field(min_length=1)


Entry = Annotated[Provided | Asked | NotApplicable, Field(discriminator="kind")]


class BundleItem(ContractModel):
    item_id: ItemId
    entry: Entry

    @model_validator(mode="after")
    def _evidence_only(self) -> BundleItem:
        e = self.entry
        if self.item_id in EVIDENCE_ONLY and isinstance(e, Provided):
            if e.provenance != "ic4_evidence":
                raise ValueError(f"{self.item_id} is provided only from IC-4 evidence")
        if self.item_id == "site_notified_minimum_fault_level" and isinstance(e, Provided):
            if not isinstance(e.value, EvidencedQuantity) or e.value.unit != "VA":
                raise ValueError("the site's minimum fault level is an evidenced quantity in VA")
        if self.item_id == "g99_generic_minimum_short_circuit_power" and isinstance(e, Provided):
            if not isinstance(e.value, PublicConstantValue):
                raise ValueError("the G99 generic short-circuit power is a public constant")
        return self


class PsalBundle(ContractModel):
    schema_name: Literal["gridq.ramp_ridethrough.psal_handoff"] = BUNDLE_SCHEMA
    schema_version: Literal["1.0.0"] = "1.0.0"
    site_id: NonEmptyStr
    record_class: RecordClass
    catalogue_version: Literal["1.0.0"]
    catalogue_hash: Sha256
    catalogue_status: Literal["neura_proposed", "psal_confirmed"]
    catalogue_confirmations: tuple[Sha256, ...]
    agreement_hash: Sha256
    declaration_hash: Sha256
    study_inputs_hash: Sha256
    items: tuple[BundleItem, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    requests: tuple[DossierAsk, ...]

    @model_validator(mode="after")
    def _matches_catalogue(self) -> PsalBundle:
        if self.catalogue_hash != CATALOGUE_HASH:
            raise ValueError("the bundle is built against this module's catalogue")
        if tuple(i.item_id for i in self.items) != tuple(i.item_id for i in CATALOGUE.items):
            raise ValueError("a bundle holds exactly the catalogue's items, in catalogue order")
        if (self.catalogue_status == "psal_confirmed") != bool(self.catalogue_confirmations):
            raise ValueError("the catalogue is confirmed exactly when a confirmation is held")
        return self

    def item(self, item_id: str) -> BundleItem:
        return next(i for i in self.items if i.item_id == item_id)


# ------------------------------------------------------------------------------------- readiness


class Ready(ContractModel):
    kind: Literal["ready"]


class ReadyGenericOnly(ContractModel):
    kind: Literal["ready_generic_only"]
    label: Literal["generic_g99_fault_level_not_site_compliant"]
    asks: tuple[DossierAsk, ...] = Field(min_length=1)


class Blocked(ContractModel):
    kind: Literal["blocked"]
    missing: tuple[ItemId, ...] = Field(min_length=1)
    asks: tuple[DossierAsk, ...] = Field(min_length=1)


class CaseReadiness(ContractModel):
    case_id: CaseId
    readiness: Annotated[Ready | ReadyGenericOnly | Blocked, Field(discriminator="kind")]


def case_readiness(bundle: PsalBundle) -> tuple[CaseReadiness, ...]:
    """Derive each study case's readiness from the bundle (never stored on the bundle)."""
    entries = {i.item_id: i.entry for i in bundle.items}
    generic_held = isinstance(entries["g99_generic_minimum_short_circuit_power"], Provided)
    out = []
    for case in CATALOGUE.cases:
        missing = tuple(r for r in case.requires if isinstance(entries[r], Asked))
        asks = sorted_asks(*(entries[m].asks for m in missing))  # type: ignore[union-attr]
        r: Ready | ReadyGenericOnly | Blocked
        if not missing:
            r = Ready(kind="ready")
        elif (
            missing == ("site_notified_minimum_fault_level",)
            and case.generic_fault_level_permitted
            and generic_held
        ):
            r = ReadyGenericOnly(
                kind="ready_generic_only",
                label="generic_g99_fault_level_not_site_compliant",
                asks=asks,
            )
        else:
            r = Blocked(kind="blocked", missing=missing, asks=asks)
        out.append(CaseReadiness(case_id=case.case_id, readiness=r))
    return tuple(out)


# ---------------------------------------------------------------------------------------- build


def catalogue_confirmation_ask() -> DossierAsk:
    """Person-only: PSAL confirms the catalogue it will study against (scope: the dossier)."""
    return DossierAsk.make(
        request=(
            f"Confirm that PSAL's RMS and EMT studies of the BESS can run from the WP-SITE-06 "
            f"handoff catalogue version {CATALOGUE.catalogue_version} ({CATALOGUE_HASH}), or "
            "name the inputs and study cases to add or remove, by a method agreement whose "
            "document hash is that catalogue hash"
        ),
        holder=AskHolder.PARTNER,
        holder_name=PSAL_NAME,
        scope="dossier",
        closes_with=(EvidenceType.METHOD_AGREEMENT,),
    )


def _confirmations(
    evidence: Iterable[EvidenceObject], as_of: date, synthetic_run: bool
) -> tuple[str, ...]:
    held = []
    for o in evidence:
        if o.evidence_type is not EvidenceType.METHOD_AGREEMENT:
            continue
        if o.issuer.role is not IssuerRole.RESEARCH_PARTNER or o.document_hash != CATALOGUE_HASH:
            continue
        require_third_party(o)
        if o.record_class == "synthetic_fixture" and not synthetic_run:
            continue
        v = o.verification
        if (
            o.status_at(as_of) is EvidenceStatus.VALID
            and isinstance(v, Verified)
            and v.method is VerificationMethod.CONFIRMED_WITH_ISSUER
            and v.verified_on <= as_of
        ):
            held.append(o.content_hash())
    return tuple(sorted(held))


_NETWORK_DOCS = {
    "pre_post_fault_short_circuit": "State the pre-fault and post-fault short-circuit capacity at "
    "the PCC (G99 12.3.1.7(c))",
    "network_equivalent": "Provide the equivalent model of the distribution network at the PCC "
    "for RMS and EMT study (G99 6.3.3)",
    "statutory_voltage_limits": "State the declared voltage and the statutory voltage limits at "
    "the PCC (P28 6.4.1(c))",
}
_CUSTOMER_DOCS = {
    "transformer_data": "Provide the site transformer data (DDRC Schedule 5c; G99 6.4.3.3)",
    "transformer_saturation": "Provide the site transformer saturation and inrush data for "
    "energisation studies (P28 6.4.1)",
    "site_network_data": "Provide the site single-line diagram with cable and busbar data "
    "(G99 6.4.2)",
    "customer_installation_equivalent": "Provide equivalents of the site's own plant (UPS, motor "
    "and chiller loads) for the BESS simulation studies (G99 B.4.1.3)",
}
_OEM_DOCS = {
    "oem_rms_model": "Provide a validated RMS simulation model of the BESS PCS and PPC "
    "(G99 6.3.7 to 6.3.8.4)",
    "oem_emt_model": "Provide a validated EMT simulation model of the BESS PCS and PPC",
    "ppc_settings": "Provide the PPC control settings: ramp limits and P, Q and V control modes "
    "(G99 B.4.1.2)",
    "protection_settings": "Provide the PCS and interface protection settings "
    "(G99 10.6.7, 12.3.1.7(d))",
    "frt_manufacturer_information": "Provide the Manufacturers' Information on fault ride-through "
    "and fast fault current of the PCS (G99 B.4.4.5, C.7.5.5)",
}


def _document_ask(item_id: str, site_id: str, bess: BessDeclaration) -> DossierAsk:
    if item_id in _NETWORK_DOCS:
        return site_ask(
            _NETWORK_DOCS[item_id],
            site_id=site_id,
            holder=AskHolder.NETWORK_OPERATOR,
            closes_with=(EvidenceType.TECHNICAL_REPORT, EvidenceType.OPERATIONAL_DATA_RECORD),
        )
    if item_id in _CUSTOMER_DOCS:
        holder = AskHolder.HOST if bess.declared_by.role == "host" else AskHolder.CUSTOMER
        return site_ask(
            _CUSTOMER_DOCS[item_id],
            site_id=site_id,
            holder=holder,
            closes_with=(EvidenceType.TECHNICAL_REPORT,),
        )
    name = bess.declared_by.name if bess.declared_by.role == "oem" else OEM_NAME
    return site_ask(
        _OEM_DOCS[item_id],
        site_id=site_id,
        holder=AskHolder.PARTNER,
        holder_name=name,
        closes_with=(EvidenceType.TECHNICAL_REPORT, EvidenceType.CONFORMITY_CERTIFICATE),
    )


def _asked(*asks: DossierAsk) -> Asked:
    return Asked(kind="asked", asks=sorted_asks(asks))


def _provided(value: Value) -> Provided:
    return Provided(kind="provided", value=value, provenance=_PROVENANCE[value.kind])  # type: ignore[arg-type]


def _na(reason: str) -> NotApplicable:
    return NotApplicable(kind="not_applicable", reason=reason)


def _ex(v: float) -> ExactRational:
    return rational(exact(v))


def _ic1_value(v: object) -> ExactRational | str | NotApplicable:
    if isinstance(v, NotApplicable):
        return v
    if isinstance(v, float | int):
        return _ex(float(v))
    return str(getattr(v, "value", v))


def _ic1_entries(agreement: ConnectionAgreement) -> dict[str, Provided | Asked | NotApplicable]:
    h = agreement.content_hash()
    site = agreement.site_id
    out: dict[str, Provided | Asked | NotApplicable] = {}

    def term(*fields: tuple[str, ExactRational | str | NotApplicable]) -> Provided:
        return _provided(
            Ic1Term(
                kind="ic1_term",
                agreement_hash=h,
                fields=tuple(Ic1Field(name=n, value=v) for n, v in fields),
            )
        )

    pcc = agreement.pcc
    out["pcc_voltage"] = (
        term(("voltage_kv", _ex(pcc.voltage_kv)), ("description", pcc.description))
        if isinstance(pcc, PccTerms)
        else _asked(pcc_ask(agreement))
    )
    ramp = agreement.ramp
    if isinstance(ramp, RampTerms):
        out["ic1_ramp_terms"] = term(
            ("max_ramp_up_mw_per_min", _ex(ramp.max_ramp_up_mw_per_min)),
            ("max_ramp_down_mw_per_min", _ex(ramp.max_ramp_down_mw_per_min)),
        )
    elif isinstance(ramp, NotApplicable):
        out["ic1_ramp_terms"] = _na(f"IC-1 states no ramp term: {ramp.reason}")
    else:
        out["ic1_ramp_terms"] = _asked(ramp_terms_ask(agreement))
    pf = agreement.power_factor
    if isinstance(pf, PowerFactorTerms):
        out["ic1_power_factor_terms"] = term(
            ("min_power_factor", _ex(pf.min_power_factor)),
            ("reactive_direction", pf.reactive_direction),
        )
    elif isinstance(pf, NotApplicable):
        out["ic1_power_factor_terms"] = _na(f"IC-1 states no power factor term: {pf.reason}")
    else:
        out["ic1_power_factor_terms"] = _asked(
            site_ask(
                f"Provide the power factor terms of connection agreement "
                f"{agreement.agreement_ref}, or confirm in writing that it states none",
                site_id=site,
                holder=AskHolder.CUSTOMER,
                closes_with=(EvidenceType.CONNECTION_AGREEMENT, EvidenceType.CONNECTION_OFFER),
            )
        )
    anm = (
        ("anm_response_time_s", agreement.anm_response_time_s),
        ("signal_type", agreement.signal_type),
        ("signal_protocol", agreement.signal_protocol),
    )
    if any(isinstance(v, Unknown) for _, v in anm):
        out["ic1_anm_terms"] = _asked(
            site_ask(
                f"Provide the active network management terms of connection agreement "
                f"{agreement.agreement_ref}: signal type, protocol and required response time",
                site_id=site,
                holder=AskHolder.CUSTOMER,
                closes_with=(EvidenceType.CONNECTION_AGREEMENT, EvidenceType.CONNECTION_OFFER),
            )
        )
    elif all(isinstance(v, NotApplicable) for _, v in anm):
        out["ic1_anm_terms"] = _na("IC-1 states no active network management signal")
    else:
        out["ic1_anm_terms"] = term(*((n, _ic1_value(v)) for n, v in anm))  # type: ignore[misc]
    return out


def _public(*keys: str) -> Provided:
    return _provided(
        PublicConstantValue(
            kind="public_constant", parameters=tuple(cited_parameter(k) for k in keys)
        )
    )


def build_bundle(
    agreement: ConnectionAgreement,
    bess: BessDeclaration,
    study: StudyInputs,
    flag: RideThroughFlag,
    *,
    evidence: Iterable[EvidenceObject] = (),
    as_of: date,
    record_class: RecordClass,
) -> PsalBundle:
    """Assemble the handoff bundle: every catalogue item Provided, Asked or NotApplicable."""
    evidence = tuple(evidence)
    site = bess.site_id
    synthetic_run = record_class == "synthetic_fixture"
    entries: dict[str, Provided | Asked | NotApplicable] = _ic1_entries(agreement)
    refs: list[EvidenceRef] = []

    for name, ask_fn in (
        ("min_fault_level_va", fault_level_ask),
        ("x_over_r", x_over_r_ask),
    ):
        q = getattr(study, name)
        item_id = "site_notified_minimum_fault_level" if name == "min_fault_level_va" else name
        if isinstance(q, EvidencedQuantity):
            entries[item_id] = _provided(q)
            refs.append(q.evidence)
        else:
            entries[item_id] = _asked(ask_fn(site))
    entries["g99_generic_minimum_short_circuit_power"] = _public(
        "g99.study.generic_min_short_circuit_va"
    )
    entries["transformer_remanence_basis"] = _public("p28.rvc.remanent_flux_pu")

    for item_id in (*_NETWORK_DOCS, *_CUSTOMER_DOCS, *_OEM_DOCS):
        doc = getattr(study, item_id)
        if isinstance(doc, EvidenceRef):
            entries[item_id] = _provided(doc)
            refs.append(doc)
        else:
            entries[item_id] = _asked(_document_ask(item_id, site, bess))

    for item_id, field in (
        ("bess_registered_capacity", "registered_capacity_w"),
        ("bess_rated_import_power", "rated_import_power_w"),
    ):
        v = getattr(bess, field)
        entries[item_id] = (
            _provided(
                DeclaredValue(
                    kind="declared_value",
                    value=rational(Fraction(v)),
                    unit="W",
                    declared_by=bess.declared_by,
                )
            )
            if isinstance(v, int)
            else _asked(bess_ask(bess, field))
        )
    rr = bess.reactive_range
    entries["bess_reactive_range"] = (
        _provided(
            DeclaredReactive(
                kind="declared_reactive_range", reactive_range=rr, declared_by=bess.declared_by
            )
        )
        if isinstance(rr, ReactiveRange)
        else _asked(bess_ask(bess, "reactive_range"))
    )

    if not flag.candidate_types:
        entries["g99_type_and_frt_parameters"] = _na(
            "below the G99 Type A threshold: no fault ride-through requirement"
        )
    elif len(flag.obligations) == 1:
        entries["g99_type_and_frt_parameters"] = _provided(
            G99Parameters(kind="g99_type_parameters", obligation=flag.obligations[0])
        )
    else:
        narrowing = []
        if isinstance(bess.registered_capacity_w, Unknown):
            narrowing.append(bess_ask(bess, "registered_capacity_w"))
        if isinstance(agreement.pcc, Unknown):
            narrowing.append(pcc_ask(agreement))
        entries["g99_type_and_frt_parameters"] = _asked(*narrowing)

    confirmations = _confirmations(evidence, as_of, synthetic_run)
    items = tuple(BundleItem(item_id=i.item_id, entry=entries[i.item_id]) for i in CATALOGUE.items)
    asks: list[DossierAsk] = [a for i in items if isinstance(i.entry, Asked) for a in i.entry.asks]
    if not confirmations:
        asks.append(catalogue_confirmation_ask())
    unique_refs = {r.evidence_hash: r for r in refs}
    return PsalBundle(
        site_id=site,
        record_class=record_class,
        catalogue_version=CATALOGUE.catalogue_version,
        catalogue_hash=CATALOGUE_HASH,
        catalogue_status="psal_confirmed" if confirmations else "neura_proposed",
        catalogue_confirmations=confirmations,
        agreement_hash=agreement.content_hash(),
        declaration_hash=content_hash(bess),
        study_inputs_hash=content_hash(study),
        items=items,
        evidence_refs=tuple(unique_refs[k] for k in sorted(unique_refs)),
        requests=sorted_asks(asks),
    )
