"""The candidate site dossier's data model (WP-OPS-01, data product `candidate_site_dossier`).

Design: D107 to D113 in ``docs/decisions.md`` (Fable 5.2, under the owner's delegation).

**Frozen and None-free.** Every model here is a `gridq.contracts._base.ContractModel`: frozen,
strict, closed (``extra="forbid"``), no NaN. No field accepts ``None``. The dossier has a canonical
content hash (`CandidateSiteDossier.content_hash`), so D-4 can cite the exact dossier it read. The
dossier is a data product, not a frozen interface contract: it carries its own schema name and
version and is not registered with ``python -m gridq.contracts --check``.

**Two states per field, never three.** A field is `Evidenced` (a typed value plus at least one
evidence item) or `UnknownField` (a reason plus at least one `DossierAsk`). Evidence is an IC-4
evidence object referenced by content hash that passed the field's admission bar (third-party,
site-scoped, of an admissible type and issuer role, VALID and verified as of the dossier date), or,
for the licence area only, a cited public source. A value the customer's record states without
such a link is carried on the unknown field as `RecordedUnevidenced`, beside the ask for its
document. It never counts as evidence.

**No verdict.** No model here has a go/no-go, score, rank or boolean field at site level. The D-4
input map (`D4Input`) reports only which dossier fields each ADR-D4 criterion reads and whether
those fields are evidenced. It never compares a value with the rule.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Final, Generic, Literal, TypeVar

from pydantic import Field, model_validator

from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    Unknown,
    content_hash,
)
from gridq.contracts.connection import ConnectionAgreement, ConnectionArchetype, NetworkOperator
from gridq.contracts.curtailment import ConnectionAgreementRef
from gridq.contracts.evidence import EvidenceRef, EvidenceStatus, EvidenceType, IssuerRole

__all__ = [
    "DOSSIER_SCHEMA",
    "DOSSIER_SCHEMA_VERSION",
    "NO_VERDICT_STATEMENT",
    "AskHolder",
    "CandidateEvidence",
    "CandidateSiteDossier",
    "ConnectionTermsValue",
    "ConnectionTypeValue",
    "ContractGap",
    "CoolingPlant",
    "CoolingTopologyValue",
    "D4Criterion",
    "D4Input",
    "D4Reference",
    "DnoValue",
    "DossierAsk",
    "DossierField",
    "EmbeddedAgreement",
    "Evidenced",
    "EvidenceItem",
    "Exclusion",
    "FieldValue",
    "GenerationAsset",
    "GenerationTechnology",
    "Ic4Evidence",
    "LedgerEntry",
    "LicenceAreaValue",
    "LocationBasis",
    "MeteringClassValue",
    "NetExportMeasure",
    "NetExportValue",
    "OnSiteGenerationValue",
    "PublicSource",
    "PublicSourceEvidence",
    "RecordNote",
    "RecordedUnevidenced",
    "SiteDossier",
    "SiteLocation",
    "SyntheticArchetypeEvidence",
    "UnknownField",
    "BessOwnerValue",
    "ask_id_for",
]

DOSSIER_SCHEMA: Final = "gridq.candidate_site_dossier"
DOSSIER_SCHEMA_VERSION: Final = "1.0.0"
#: The one place verdict language may appear (the verdict-language test exempts only this text).
NO_VERDICT_STATEMENT: Final = "This dossier makes no go/no-go statement; D-4 decides (ADR-D4)."

NonNegFloat = Annotated[float, Field(ge=0)]
PosFloat = Annotated[float, Field(gt=0)]
Count = Annotated[int, Field(ge=0)]


class DossierField(StrEnum):
    """The fields every candidate site is characterised by (roadmap WP-OPS-01 ``delivers``).

    ``dno`` is the connecting network operator, from a connection document or MPAN registration.
    ``dno_licence_area`` is the licence area that geographically contains the site, from a cited
    public boundary dataset. They are separate because a site inside one licence area can still be
    connected by an IDNO or a private network (D111). ``bess_owner`` and ``bess_funder``
    are separate because only ownership has an IC-4 type today (D113).
    """

    CONNECTION_TYPE = "connection_type"
    CONNECTION_TERMS = "connection_terms"
    DNO = "dno"
    DNO_LICENCE_AREA = "dno_licence_area"
    METERING_CLASS = "metering_class"
    NET_EXPORT = "net_export"
    ONSITE_GENERATION = "onsite_generation"
    COOLING_TOPOLOGY = "cooling_topology"
    BESS_OWNER = "bess_owner"
    BESS_FUNDER = "bess_funder"


# --------------------------------------------------------------------------------------------
# Field values: what an evidenced field states
# --------------------------------------------------------------------------------------------


class ConnectionTypeValue(ContractModel):
    """The IC-1 archetype and whether the import is profiled, read from the evidenced IC-1."""

    kind: Literal["connection_type"]
    archetype: ConnectionArchetype
    profiled_import: Literal["profiled", "not_profiled"] | Unknown


class EmbeddedAgreement(ContractModel):
    """One IC-1 instance, whole, with the chassis reference that names it by content hash."""

    ref: ConnectionAgreementRef
    agreement: ConnectionAgreement

    @model_validator(mode="after")
    def _ref_names_this_agreement(self) -> EmbeddedAgreement:
        if self.ref.agreement_hash != self.agreement.content_hash():
            raise ValueError("the reference's hash is not the embedded agreement's content hash")
        if self.ref.contract_version != self.agreement.contract_version:
            raise ValueError("the reference's version is not the embedded agreement's version")
        return self


class ConnectionTermsValue(ContractModel):
    """Acceptance (2): the connection terms, as IC-1 instances."""

    kind: Literal["connection_terms"]
    agreements: tuple[EmbeddedAgreement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self) -> ConnectionTermsValue:
        hashes = [a.ref.agreement_hash for a in self.agreements]
        if hashes != sorted(set(hashes)):
            raise ValueError("agreements are ordered by content hash, without repeats")
        return self


class DnoValue(ContractModel):
    """The connecting network operator (group), and its licence area where a document names it."""

    kind: Literal["dno"]
    operator: NetworkOperator
    licence_area: NonEmptyStr | Unknown


class LocationBasis(ContractModel):
    """Where a registered location comes from. ``synthetic_fixture``: SYNTHETIC- sites only."""

    basis: Literal["operator_published_address", "owner_survey", "synthetic_fixture"]
    citation: NonEmptyStr
    recorded_on: IsoDate


class SiteLocation(ContractModel):
    """A point (WGS84 degrees) the owner registered for a site, with its basis."""

    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    basis: LocationBasis


class LicenceAreaValue(ContractModel):
    """The DNO licence area whose published boundary contains the site's registered location."""

    kind: Literal["licence_area_containing_site"]
    licence_area: NonEmptyStr
    boundary_feature: NonEmptyStr
    location: SiteLocation
    margin_km: NonNegFloat


class MeteringClassValue(ContractModel):
    """SVA or CVA settlement, and for SVA the measurement class (BSC classes A to G)."""

    kind: Literal["metering_class"]
    settlement: Literal["sva", "cva"]
    measurement_class: Literal["A", "B", "C", "D", "E", "F", "G"] | NotApplicable | Unknown

    @model_validator(mode="after")
    def _cva_has_no_measurement_class(self) -> MeteringClassValue:
        if self.settlement == "cva" and not isinstance(self.measurement_class, NotApplicable):
            raise ValueError("a CVA meter has no SVA measurement class: use not_applicable")
        if self.settlement == "sva" and isinstance(self.measurement_class, NotApplicable):
            raise ValueError("an SVA meter has a measurement class, or it is unknown")
        return self


class NetExportMeasure(ContractModel):
    basis: Literal["agreed_export_capacity", "metered_peak_net_export"]
    kw: NonNegFloat


class NetExportValue(ContractModel):
    """Net export, one measure per basis (an agreed capacity and a metered peak may both exist)."""

    kind: Literal["net_export"]
    measures: tuple[NetExportMeasure, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_per_basis(self) -> NetExportValue:
        bases = [m.basis for m in self.measures]
        if bases != sorted(set(bases)):
            raise ValueError("one measure per basis, ordered by basis")
        return self


class GenerationTechnology(StrEnum):
    DIESEL_GENSET = "diesel_genset"
    GAS_GENSET = "gas_genset"
    COMBINED_HEAT_AND_POWER = "combined_heat_and_power"
    ANAEROBIC_DIGESTION = "anaerobic_digestion"
    ENERGY_FROM_WASTE = "energy_from_waste"
    LANDFILL_GAS = "landfill_gas"
    SOLAR_PV = "solar_pv"
    WIND = "wind"
    BATTERY_STORAGE = "battery_storage"
    OTHER = "other"


class GenerationAsset(ContractModel):
    technology: GenerationTechnology
    rated_kw: PosFloat | Unknown
    operation: Literal["standby_only", "parallel_with_grid", "islanded_failover"] | Unknown
    description: NonEmptyStr


class OnSiteGenerationValue(ContractModel):
    """Gensets and on-site generation. "None on site" is itself a statement that needs evidence."""

    kind: Literal["onsite_generation"]
    statement: Literal["assets_listed", "no_onsite_generation"]
    assets: tuple[GenerationAsset, ...]

    @model_validator(mode="after")
    def _statement_matches_assets(self) -> OnSiteGenerationValue:
        if (self.statement == "assets_listed") != bool(self.assets):
            raise ValueError("assets_listed needs at least one asset; no_onsite_generation, none")
        return self


class CoolingPlant(StrEnum):
    AIR_COOLED_CHILLERS = "air_cooled_chillers"
    WATER_COOLED_CHILLERS = "water_cooled_chillers"
    CHILLED_WATER_CRAH = "chilled_water_crah"
    DX_CRAC = "dx_crac"
    EVAPORATIVE = "evaporative"
    FREE_COOLING = "free_cooling"
    DIRECT_LIQUID = "direct_liquid"
    PROCESS_REFRIGERATION = "process_refrigeration"
    NO_SIGNIFICANT_COOLING_PLANT = "no_significant_cooling_plant"
    OTHER = "other"


class CoolingTopologyValue(ContractModel):
    kind: Literal["cooling_topology"]
    plant: tuple[CoolingPlant, ...] = Field(min_length=1)
    description: NonEmptyStr

    @model_validator(mode="after")
    def _ordered(self) -> CoolingTopologyValue:
        if list(self.plant) != sorted(set(self.plant)):
            raise ValueError("plant is ordered, without repeats")
        return self


class BessOwnerValue(ContractModel):
    """Who owns the BESS, as the host's site access and installation agreement states it."""

    kind: Literal["bess_owner"]
    owner_type: Literal["host", "third_party"]
    owner_name: NonEmptyStr


FieldValue = Annotated[
    ConnectionTypeValue
    | ConnectionTermsValue
    | DnoValue
    | LicenceAreaValue
    | MeteringClassValue
    | NetExportValue
    | OnSiteGenerationValue
    | CoolingTopologyValue
    | BessOwnerValue,
    Field(discriminator="kind"),
]

#: The value kind each field carries. `bess_funder` has none: it cannot be evidenced (G9).
VALUE_KIND: Final[dict[DossierField, str | None]] = {
    DossierField.CONNECTION_TYPE: "connection_type",
    DossierField.CONNECTION_TERMS: "connection_terms",
    DossierField.DNO: "dno",
    DossierField.DNO_LICENCE_AREA: "licence_area_containing_site",
    DossierField.METERING_CLASS: "metering_class",
    DossierField.NET_EXPORT: "net_export",
    DossierField.ONSITE_GENERATION: "onsite_generation",
    DossierField.COOLING_TOPOLOGY: "cooling_topology",
    DossierField.BESS_OWNER: "bess_owner",
    DossierField.BESS_FUNDER: None,
}


# --------------------------------------------------------------------------------------------
# Evidence items
# --------------------------------------------------------------------------------------------


class PublicSource(ContractModel):
    """A retrieved public document, cited and hashed. ``synthetic_fixture`` is test data only."""

    record_class: Literal["retrieved", "synthetic_fixture"]
    title: NonEmptyStr
    publisher: NonEmptyStr
    url: NonEmptyStr
    retrieved_on: IsoDate
    content_sha256: Sha256
    method: NonEmptyStr


class Ic4Evidence(ContractModel):
    """An IC-4 evidence object that passed the field's admission bar, by content hash."""

    kind: Literal["ic4"]
    ref: EvidenceRef
    evidence_type: EvidenceType
    issuer_role: IssuerRole
    #: What on the field this document supports (``ic1:<hash>``, ``record:<path>``, a clause...).
    supports: NonEmptyStr


class PublicSourceEvidence(ContractModel):
    kind: Literal["public_source"]
    source: PublicSource
    supports: NonEmptyStr


class SyntheticArchetypeEvidence(ContractModel):
    """An IC-1 SyntheticArchetype source. Admitted only on a SYNTHETIC- site (D110)."""

    kind: Literal["synthetic_archetype"]
    agreement_hash: Sha256
    statement: NonEmptyStr


EvidenceItem = Annotated[
    Ic4Evidence | PublicSourceEvidence | SyntheticArchetypeEvidence,
    Field(discriminator="kind"),
]


# --------------------------------------------------------------------------------------------
# Asks
# --------------------------------------------------------------------------------------------


class AskHolder(StrEnum):
    """Who can close an ask."""

    CUSTOMER = "customer"
    HOST = "host"
    NETWORK_OPERATOR = "network_operator"
    METER_OPERATOR = "meter_operator"
    FUNDER = "funder"
    PARTNER = "partner"
    NEURA_OWNER = "neura_owner"


def ask_id_for(
    *,
    request: str,
    holder: AskHolder,
    holder_name: str | None,
    closes_with: tuple[EvidenceType, ...],
    scope: str,
) -> str:
    """The deterministic id of an ask: sha256 of its canonical identity (D108)."""
    payload = {
        "closes_with": [t.value for t in closes_with],
        "holder": holder.value,
        "holder_name": holder_name,
        "request": request,
        "scope": scope,
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class DossierAsk(ContractModel):
    """One data ask. Its id is derived from its content; prerequisites must be met first."""

    ask_id: Sha256
    request: NonEmptyStr
    holder: AskHolder
    holder_name: NonEmptyStr | NotApplicable
    #: ``customer:<key>``, ``site:<site_id>`` or ``dossier``.
    scope: Annotated[str, Field(pattern=r"^(customer:\S+|site:\S+|dossier)$")]
    #: The IC-4 evidence types that would close the ask; empty when no document closes it.
    closes_with: tuple[EvidenceType, ...]
    prerequisites: tuple[Sha256, ...]

    @model_validator(mode="after")
    def _id_is_derived(self) -> DossierAsk:
        name = None if isinstance(self.holder_name, NotApplicable) else self.holder_name
        expected = ask_id_for(
            request=self.request,
            holder=self.holder,
            holder_name=name,
            closes_with=self.closes_with,
            scope=self.scope,
        )
        if self.ask_id != expected:
            raise ValueError("ask_id is not the hash of the ask's identity")
        if self.ask_id in self.prerequisites:
            raise ValueError("an ask cannot be its own prerequisite")
        if list(self.prerequisites) != sorted(set(self.prerequisites)):
            raise ValueError("prerequisites are ordered, without repeats")
        return self

    @classmethod
    def make(
        cls,
        *,
        request: str,
        holder: AskHolder,
        scope: str,
        closes_with: tuple[EvidenceType, ...] = (),
        holder_name: str | None = None,
        prerequisites: tuple[str, ...] = (),
    ) -> DossierAsk:
        return cls(
            ask_id=ask_id_for(
                request=request,
                holder=holder,
                holder_name=holder_name,
                closes_with=closes_with,
                scope=scope,
            ),
            request=request,
            holder=holder,
            holder_name=(
                holder_name
                if holder_name is not None
                else NotApplicable(kind="not_applicable", reason="no named holder")
            ),
            scope=scope,
            closes_with=closes_with,
            prerequisites=tuple(sorted(set(prerequisites))),
        )


# --------------------------------------------------------------------------------------------
# Fields
# --------------------------------------------------------------------------------------------

V = TypeVar("V", bound=ContractModel)


class Evidenced(ContractModel, Generic[V]):
    """A field with a typed value and at least one evidence item."""

    status: Literal["evidenced"]
    value: V
    evidence: tuple[EvidenceItem, ...] = Field(min_length=1)


class CandidateEvidence(ContractModel):
    """A document that exists but did not pass the admission bar, and why."""

    ref: EvidenceRef
    status_as_of: EvidenceStatus
    verification: Literal["verified", "unverified", "not_registered"]
    why_not_admitted: NonEmptyStr


class RecordedUnevidenced(ContractModel):
    """A value the released customer record states with no admissible IC-4 link (D107).

    Shown beside the ask for its document; never evidence, never counted as complete.
    """

    record: NonEmptyStr
    field_path: NonEmptyStr
    value: FieldValue


class UnknownField(ContractModel):
    """A field that is not evidenced: why, and the asks that would evidence it."""

    status: Literal["unknown"]
    reason: NonEmptyStr
    asks: tuple[DossierAsk, ...] = Field(min_length=1)
    candidate_evidence: tuple[CandidateEvidence, ...] = ()
    recorded_unevidenced: tuple[RecordedUnevidenced, ...] = ()

    @model_validator(mode="after")
    def _asks_unique(self) -> UnknownField:
        ids = [a.ask_id for a in self.asks]
        if len(ids) != len(set(ids)):
            raise ValueError("an unknown field lists each ask once")
        return self


_BY_STATUS: Final = Field(discriminator="status")
ConnectionTypeField = Annotated[Evidenced[ConnectionTypeValue] | UnknownField, _BY_STATUS]
ConnectionTermsField = Annotated[Evidenced[ConnectionTermsValue] | UnknownField, _BY_STATUS]
DnoField = Annotated[Evidenced[DnoValue] | UnknownField, _BY_STATUS]
LicenceAreaField = Annotated[Evidenced[LicenceAreaValue] | UnknownField, _BY_STATUS]
MeteringClassField = Annotated[Evidenced[MeteringClassValue] | UnknownField, _BY_STATUS]
NetExportField = Annotated[Evidenced[NetExportValue] | UnknownField, _BY_STATUS]
OnSiteGenerationField = Annotated[Evidenced[OnSiteGenerationValue] | UnknownField, _BY_STATUS]
CoolingTopologyField = Annotated[Evidenced[CoolingTopologyValue] | UnknownField, _BY_STATUS]
BessOwnerField = Annotated[Evidenced[BessOwnerValue] | UnknownField, _BY_STATUS]


# --------------------------------------------------------------------------------------------
# D-4 input map (D109)
# --------------------------------------------------------------------------------------------

D4Key = Literal[
    "criterion_i",
    "criterion_ii",
    "criterion_iii",
    "criterion_iv",
    "criterion_v",
    "criterion_vi",
    "spec_net_export",
    "spec_onsite_generation_r13",
]


class D4Criterion(ContractModel):
    """Which dossier fields an ADR-D4 criterion (or spec D-4 input) reads. No threshold."""

    key: D4Key
    label: NonEmptyStr
    reads: tuple[DossierField, ...]


class D4Input(ContractModel):
    """For one site and one criterion: are the fields it reads evidenced? Nothing more."""

    key: D4Key
    completeness: Literal[
        "all_inputs_evidenced", "inputs_open", "not_characterised_by_this_dossier"
    ]
    open_asks: tuple[Sha256, ...]
    open_ask_count: Count

    @model_validator(mode="after")
    def _count_matches(self) -> D4Input:
        if self.open_ask_count != len(self.open_asks):
            raise ValueError("open_ask_count is the number of open asks")
        if list(self.open_asks) != sorted(set(self.open_asks)):
            raise ValueError("open asks are ordered, without repeats")
        return self


class D4Reference(ContractModel):
    """The rule this dossier feeds, cited by path and file hash; it is not restated here."""

    adr: Literal["ADR-D4"]
    path: NonEmptyStr
    file_sha256: Sha256
    atlas_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    statement: Literal["This dossier makes no go/no-go statement; D-4 decides (ADR-D4)."]
    criteria: tuple[D4Criterion, ...] = Field(min_length=1)


# --------------------------------------------------------------------------------------------
# Site and dossier
# --------------------------------------------------------------------------------------------


class RecordNote(ContractModel):
    """A customer record that was not read, or read and not used, and why."""

    record: NonEmptyStr
    note: NonEmptyStr


class SiteDossier(ContractModel):
    """One candidate site. No verdict, score, rank or boolean field (D109)."""

    site_id: NonEmptyStr
    customer: NonEmptyStr
    origin: Literal["dpa_manifest", "owner_register", "synthetic"]
    partner: NonEmptyStr | NotApplicable
    customer_data: Literal["released_under_dpa", "synthetic", "not_released"]
    #: Customers whose missing DPA keeps this site's data unread (empty when released).
    dpa_blockers: tuple[NonEmptyStr, ...]
    records: tuple[RecordNote, ...]

    connection_type: ConnectionTypeField
    connection_terms: ConnectionTermsField
    dno: DnoField
    dno_licence_area: LicenceAreaField
    metering_class: MeteringClassField
    net_export: NetExportField
    onsite_generation: OnSiteGenerationField
    cooling_topology: CoolingTopologyField
    bess_owner: BessOwnerField
    bess_funder: UnknownField

    d4_inputs: tuple[D4Input, ...]

    def field(self, name: DossierField) -> Evidenced[Any] | UnknownField:
        value: Evidenced[Any] | UnknownField = getattr(self, name.value)
        return value

    @model_validator(mode="after")
    def _nothing_customer_sourced_without_release(self) -> SiteDossier:
        synthetic_site = self.site_id.startswith("SYNTHETIC-")
        if (self.customer_data == "synthetic") != synthetic_site:
            raise ValueError("customer_data is 'synthetic' exactly for a SYNTHETIC- site")
        if (self.customer_data == "not_released") != bool(self.dpa_blockers):
            raise ValueError("a site is not released exactly when a DPA blocker is named")
        for name in DossierField:
            f = self.field(name)
            if self.customer_data == "not_released" and name is not DossierField.DNO_LICENCE_AREA:
                if isinstance(f, Evidenced):
                    raise ValueError(
                        f"{name.value} is evidenced on a site whose customer data is not released"
                    )
                if f.recorded_unevidenced:
                    raise ValueError(f"{name.value} shows a record value the guard did not release")
            if isinstance(f, Evidenced):
                kind = VALUE_KIND[name]
                if f.value.kind != kind:
                    raise ValueError(f"{name.value} carries a {f.value.kind} value")
                for item in f.evidence:
                    if not synthetic_site and (
                        isinstance(item, SyntheticArchetypeEvidence)
                        or (
                            isinstance(item, PublicSourceEvidence)
                            and item.source.record_class == "synthetic_fixture"
                        )
                    ):
                        raise ValueError(f"{name.value} on a real site cites synthetic evidence")
        keys = [i.key for i in self.d4_inputs]
        if len(keys) != len(set(keys)):
            raise ValueError("one D-4 input per criterion")
        return self


class Exclusion(ContractModel):
    """A site the owner excluded from the register, with the owner's recorded reason."""

    site_id: NonEmptyStr
    reason: NonEmptyStr


class LedgerEntry(ContractModel):
    """One de-duplicated ask. The ledger orders asks, never sites."""

    position: Annotated[int, Field(ge=1)]
    ask: DossierAsk
    #: How many (site, field) unknowns list this ask.
    leverage: Count
    #: Those unknowns, as ``<site_id>/<field>`` (``dossier`` for a dossier-level ask).
    unknowns: tuple[NonEmptyStr, ...]


class ContractGap(ContractModel):
    """A fact a frozen contract cannot yet carry, so a field stays unknown (D113)."""

    gap_id: NonEmptyStr
    contract: NonEmptyStr
    missing: NonEmptyStr
    fields: tuple[DossierField, ...] = Field(min_length=1)
    owner: NonEmptyStr


class CandidateSiteDossier(ContractModel):
    """The dossier: every candidate site, the ask ledger, and the D-4 reference. No verdict."""

    dossier_schema: Literal["gridq.candidate_site_dossier"]
    dossier_schema_version: Literal["1.0.0"]
    work_package: Literal["WP-OPS-01"]
    as_of: IsoDate
    statement: Literal["This dossier makes no go/no-go statement; D-4 decides (ADR-D4)."]
    d4: D4Reference
    sites: tuple[SiteDossier, ...]
    excluded: tuple[Exclusion, ...]
    dossier_asks: tuple[DossierAsk, ...]
    ask_ledger: tuple[LedgerEntry, ...]
    contract_gaps: tuple[ContractGap, ...]

    @model_validator(mode="after")
    def _ordered(self) -> CandidateSiteDossier:
        ids = [s.site_id for s in self.sites]
        if ids != sorted(set(ids)):
            raise ValueError("sites are ordered by site_id, without repeats")
        if [e.position for e in self.ask_ledger] != list(range(1, len(self.ask_ledger) + 1)):
            raise ValueError("ledger positions run 1..n")
        return self

    def content_hash(self) -> str:
        """``sha256:<hex>`` of the canonical JSON: the identity D-4 cites."""
        return content_hash(self)

    @property
    def has_real_site(self) -> bool:
        """True when any characterised or excluded site is not a SYNTHETIC- site."""
        return any(not s.site_id.startswith("SYNTHETIC-") for s in self.sites) or any(
            not e.site_id.startswith("SYNTHETIC-") for e in self.excluded
        )
