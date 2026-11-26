"""The site eligibility table and its inputs (WP-UW-02, data product `site_eligibility`).

Design: D133 to D142 in ``docs/decisions.md`` (Fable 5.2, under the owner's
delegation).

**A data product, not a frozen contract.** Like the OPS-01 dossier (D107), every model here is a
`gridq.contracts._base.ContractModel`: frozen, strict, closed and None-free, with a canonical
content hash. It carries its own schema name and version (`gridq.site_eligibility` 1.0.0) and is
not registered with ``python -m gridq.contracts --check``. IC-12 is unchanged.

**Three statuses, never a fourth.** Each stream is ``eligible``, ``ineligible`` or
``undetermined``, with its checks and their reasons. A check may also be ``not_applicable`` (for
example the GSP-group condition on a CVA site), which the stream's status ignores. The stream's
status is computed from its checks by one rule, enforced by a validator: any ineligible check makes
the stream ineligible; otherwise any undetermined check makes it undetermined; otherwise it is
eligible.

**How to consume it (WP-UW-04).** Tighten only: undetermined is not eligible
(`CONSUMPTION_STATEMENT`). Nothing reads this product yet; it moves no number.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

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
from gridq.contracts.evidence import EvidenceRef
from gridq.ops.dossier_model import DossierAsk
from gridq.underwriting.eligibility_rules import FactField, Stream

__all__ = [
    "CONSUMPTION_STATEMENT",
    "ELIGIBILITY_SCHEMA",
    "ELIGIBILITY_SCHEMA_VERSION",
    "AtlasFile",
    "CheckResult",
    "CmUnitValue",
    "ConsumerClassValue",
    "ContractingCustomersValue",
    "ControlledAssetsDeclaration",
    "DfsRouteValue",
    "EligibilityFacts",
    "FactInput",
    "FactState",
    "FlexZoneValue",
    "GspGroupsValue",
    "LoaderRecord",
    "MsidExportValue",
    "OwnerAction",
    "RowVerdict",
    "SiteEligibility",
    "SiteEligibilityTable",
    "StreamResult",
    "ThresholdUse",
    "combine",
]

ELIGIBILITY_SCHEMA: Final = "gridq.site_eligibility"
ELIGIBILITY_SCHEMA_VERSION: Final = "1.0.0"
CONSUMPTION_STATEMENT: Final = (
    "Consume tighten-only: undetermined is not eligible. This table moves no number until WP-UW-04 "
    "wires it into underwriting_basis_v1; it never gates all_streams_enabled_study_v1."
)

Status = Literal["eligible", "ineligible", "undetermined"]
CheckStatus = Literal["eligible", "ineligible", "undetermined", "not_applicable"]
EvidenceAdmission = Literal["registered_only", "synthetic_fixtures_test_only"]
NonNegFloat = Annotated[float, Field(ge=0)]


# --------------------------------------------------------------------------------------------
# Inputs: facts the dossier has no field for, and the product's controlled assets
# --------------------------------------------------------------------------------------------


class ConsumerClassValue(ContractModel):
    """A customer's consumer class, named as the load-control rule names it."""

    kind: Literal["customer_consumer_class"]
    consumer_class: NonEmptyStr


class ContractingCustomersValue(ContractModel):
    """The consumer class of every other customer of the contracting entity (may be none)."""

    kind: Literal["contracting_entity_customers"]
    consumer_classes: tuple[NonEmptyStr, ...]


class GspGroupsValue(ContractModel):
    kind: Literal["gsp_groups"]
    groups: tuple[NonEmptyStr, ...] = Field(min_length=1)


class MsidExportValue(ContractModel):
    """The largest half-hourly export over the year, across the site's MSID pairs."""

    kind: Literal["msid_export"]
    msid_pairs: Annotated[int, Field(ge=1)]
    largest_export_kw: NonNegFloat


class CmUnitValue(ContractModel):
    kind: Literal["cm_unit_type"]
    unit_class: Literal["dsr", "btm_storage"]


class DfsRouteValue(ContractModel):
    kind: Literal["dfs_route"]
    route: Literal["supplier", "aggregator"]
    counterparty: NonEmptyStr


class FlexZoneValue(ContractModel):
    kind: Literal["dno_flex_zone"]
    zone: NonEmptyStr


FactValue = Annotated[
    ConsumerClassValue
    | ContractingCustomersValue
    | GspGroupsValue
    | MsidExportValue
    | CmUnitValue
    | DfsRouteValue
    | FlexZoneValue,
    Field(discriminator="kind"),
]


class FactInput(ContractModel):
    """One fact: evidenced by IC-4 documents, or declared by a named person without evidence.

    A declaration never counts as evidence. The engine passes a declared consumer class to the
    load-control rule without an evidence reference, so the rule refuses a class it refuses and
    holds a class it would pass (it needs evidence for that).
    """

    field: FactField
    basis: Literal["evidence", "declaration"]
    value: FactValue
    evidence: tuple[EvidenceRef, ...]
    declared_by: NonEmptyStr | NotApplicable

    @model_validator(mode="after")
    def _shape(self) -> FactInput:
        if self.value.kind != self.field.value:
            raise ValueError(f"{self.field.value} carries a {self.value.kind} value")
        if self.basis == "evidence" and not self.evidence:
            raise ValueError("an evidenced fact cites at least one IC-4 reference")
        if self.basis == "declaration":
            if self.evidence:
                raise ValueError("a declaration cites no evidence; use basis 'evidence'")
            if isinstance(self.declared_by, NotApplicable):
                raise ValueError("a declaration names the person who made it")
        return self


class EligibilityFacts(ContractModel):
    """Per-site facts the OPS-01 dossier has no field for. A fact left out is unknown."""

    record: Literal["gridq.eligibility_facts"]
    record_version: Literal["1.0.0"]
    record_class: Literal["registered", "synthetic_fixture"]
    site_id: NonEmptyStr
    as_of: IsoDate
    facts: tuple[FactInput, ...]

    @model_validator(mode="after")
    def _one_per_field(self) -> EligibilityFacts:
        names = [f.field for f in self.facts]
        if len(names) != len(set(names)):
            raise ValueError("one fact per field")
        if self.record_class == "synthetic_fixture" and not self.site_id.startswith("SYNTHETIC-"):
            raise ValueError("synthetic facts are only for a SYNTHETIC- site")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


class ControlledAssetsDeclaration(ContractModel):
    """The asset classes the product would control (product scope, not a site fact).

    Ids are the load-control rule's asset class ids (``bess``, ``cooling_plant`` ...).
    """

    record: Literal["gridq.controlled_assets"]
    record_version: Literal["1.0.0"]
    record_class: Literal["registered", "synthetic_fixture"]
    product: NonEmptyStr
    declared_by: NonEmptyStr
    asset_classes: tuple[NonEmptyStr, ...] = Field(min_length=1)

    def content_hash(self) -> str:
        return content_hash(self)


# --------------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------------


class RowVerdict(ContractModel):
    """The register gate's verdict on one row as of the run's date (``passes`` or the refusal)."""

    row: NonEmptyStr
    verdict: NonEmptyStr
    message: NonEmptyStr


class ThresholdUse(ContractModel):
    """A register threshold a check used, as the row states it."""

    row: NonEmptyStr
    name: NonEmptyStr
    value: NonEmptyStr
    unit: Literal["MW", "kW"]
    comparator: Literal["gt", "ge"]
    quote: NonEmptyStr


class FactState(ContractModel):
    """What a check read: a dossier field, a fact, the declaration or the Atlas rule."""

    source: Literal["dossier", "facts", "declaration", "atlas"]
    field: NonEmptyStr
    state: Literal["evidenced", "declared", "unknown", "loaded", "not_loaded"]
    evidence: tuple[Sha256, ...]
    note: NonEmptyStr


class CheckResult(ContractModel):
    check_id: NonEmptyStr
    status: CheckStatus
    reason: NonEmptyStr
    rows: tuple[RowVerdict, ...] = Field(min_length=1)
    read: tuple[FactState, ...]
    missing_fields: tuple[NonEmptyStr, ...]
    thresholds: tuple[ThresholdUse, ...]
    #: True when every fact and threshold the check needs is present and only the register gate
    #: holds it (R4): a sign-off would decide it.
    blocked_only_by_gate: bool
    #: What would let the check decide: row ids to sign off, fields to supply, thresholds to add.
    unblocked_by: tuple[NonEmptyStr, ...]


def combine(statuses: tuple[str, ...]) -> Status:
    """A stream's status from its checks' (not_applicable checks are ignored)."""
    live = [s for s in statuses if s != "not_applicable"]
    if "ineligible" in live:
        return "ineligible"
    if not live or "undetermined" in live:
        return "undetermined"
    return "eligible"


class StreamResult(ContractModel):
    stream: Stream
    question: NonEmptyStr
    status: Status
    classification: NonEmptyStr | Unknown
    checks: tuple[CheckResult, ...] = Field(min_length=1)
    #: The Atlas evaluator's result, as canonical JSON, verbatim (load-control stream only).
    atlas_evaluation: NonEmptyStr | NotApplicable

    @model_validator(mode="after")
    def _status_is_combined(self) -> StreamResult:
        if self.status != combine(tuple(c.status for c in self.checks)):
            raise ValueError(f"{self.stream.value}: status is not the combination of its checks")
        return self


class AtlasFile(ContractModel):
    name: Literal["register_module", "register_file", "loader", "segment_rule"]
    sha256: NonEmptyStr


class LoaderRecord(ContractModel):
    """How the run obtained the segment rule and the register verdicts (D133)."""

    bridge: Literal["fresh_subprocess"]
    outcome: Literal["loaded", "refused", "error", "not_run"]
    reason: NonEmptyStr
    rule_id: NonEmptyStr | Unknown
    rule_version: NonEmptyStr | Unknown
    rule_status: NonEmptyStr | Unknown
    reported_as_of: IsoDate | Unknown
    rests_on: tuple[NonEmptyStr, ...]
    adoption: Literal["adopted", "not_adopted_synthetic_mode", "not_adopted", "not_run"]
    files: tuple[AtlasFile, ...]
    files_changed_during_read: tuple[NonEmptyStr, ...]


class OwnerAction(ContractModel):
    action_id: NonEmptyStr
    owner: NonEmptyStr
    action: NonEmptyStr


class SiteEligibility(ContractModel):
    site_id: NonEmptyStr
    dossier_site_hash: Sha256
    facts_hash: Sha256 | NotApplicable
    streams: tuple[StreamResult, ...]
    data_requests: tuple[DossierAsk, ...]

    @model_validator(mode="after")
    def _every_stream_once_in_order(self) -> SiteEligibility:
        if tuple(s.stream for s in self.streams) != tuple(Stream):
            raise ValueError("a site lists every stream once, in the table's order")
        ids = [a.ask_id for a in self.data_requests]
        if ids != sorted(set(ids)):
            raise ValueError("data requests are de-duplicated and ordered by id")
        return self

    def stream(self, stream: Stream) -> StreamResult:
        return next(s for s in self.streams if s.stream is stream)


class SiteEligibilityTable(ContractModel):
    schema_name: Literal["gridq.site_eligibility"]
    schema_version: Literal["1.0.0"]
    work_package: Literal["WP-UW-02"]
    as_of: IsoDate
    evidence_admission: EvidenceAdmission
    labels: tuple[NonEmptyStr, ...]
    rule_table_version: NonEmptyStr
    rule_table_hash: Sha256
    bridge_sha256: Sha256
    dossier_hash: Sha256
    controlled_assets_hash: Sha256 | NotApplicable
    loader: LoaderRecord
    row_verdicts: tuple[RowVerdict, ...]
    sites: tuple[SiteEligibility, ...]
    owner_actions: tuple[OwnerAction, ...]
    consumption: Literal[
        "Consume tighten-only: undetermined is not eligible. This table moves no number until "
        "WP-UW-04 wires it into underwriting_basis_v1; it never gates "
        "all_streams_enabled_study_v1."
    ]

    def content_hash(self) -> str:
        """``sha256:<hex>`` of the canonical JSON: the run's identity."""
        return content_hash(self)

    def site(self, site_id: str) -> SiteEligibility:
        return next(s for s in self.sites if s.site_id == site_id)
