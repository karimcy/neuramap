"""What the candidate site dossier reads, and the gates every input passes (WP-OPS-01).

**The register** (D112). The candidate sites are the customer sites named in the DPA
manifest (`gridq.governance.customer_data.CUSTOMERS`) by default, extended or overridden by an
owner register file (``dossiers/candidate_register.json``, git-ignored). The owner list may add a
site, or exclude one with a reason that the dossier shows. When no candidate comes from the
Chainergy partnership, the dossier carries a dossier-level ask instead of a placeholder site.

**The DPA gate** (binding data rule). A site's customer data is read only when the guard releases
it: ``require_dpa`` for the site, then ``check_load`` and the guarded loader for each record. With
no DPA registered, nothing customer-sourced is opened: the record paths are put to ``check_load``
only, so the guard's refusal is recorded, and the site's per-site input directories are not
listed. A SYNTHETIC- site is test data and needs no DPA.

**The evidence bar** (D107). A value is evidenced only by an IC-4 object that is in the
registry, at the version its reference states, issued by a third party, of a type and issuer role
the field admits (`FIELD_EVIDENCE`), scoped to the site, VALID as of the dossier date and verified.
Anything less leaves the field unknown, with the document as `CandidateEvidence` and a specific ask.
A synthetic fixture, synthetic IC-1 source or synthetic transcription on a real site is an error,
not a downgrade.

Inputs, per site, all under the evidence registry directory (git-ignored):

- ``connection_agreements/<site_id>/*.json``: IC-1 instances (D110);
- ``dossier_transcriptions/<site_id>/*.json``: `FieldTranscription` records, an owner's typed
  reading of one field from cited IC-4 documents, with a page or clause locator.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import Field, model_validator

from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    NotApplicable,
    content_hash,
)
from gridq.contracts.connection import ConnectionAgreement
from gridq.contracts.evidence import (
    EvidenceObject,
    EvidenceRef,
    EvidenceStatus,
    EvidenceType,
    IssuerRole,
    SiteScope,
)
from gridq.governance.customer_data import CUSTOMERS, load_manifest
from gridq.governance.dpa_guard import (
    SYNTHETIC_SITE_PREFIX,
    DpaEvidenceMissing,
    check_load,
    repo_root,
    require_dpa,
)
from gridq.ingestion.access_log import record_hh_access
from gridq.ops.dossier_model import (
    VALUE_KIND,
    AskHolder,
    CandidateEvidence,
    DossierAsk,
    DossierField,
    Exclusion,
    FieldValue,
    Ic4Evidence,
    SiteLocation,
)

__all__ = [
    "CUSTOMER_SOURCED",
    "FIELD_EVIDENCE",
    "IC1_DIR",
    "REGISTER_PATH",
    "TRANSCRIPTION_DIR",
    "Admission",
    "CandidateEntry",
    "CandidateRegister",
    "DossierInputError",
    "FieldTranscription",
    "SiteGate",
    "admit",
    "default_register",
    "dpa_ask",
    "gate_site",
    "is_synthetic_site",
    "load_register_file",
    "read_site_agreements",
    "read_site_transcriptions",
]

IC1_DIR: Final = "connection_agreements"
TRANSCRIPTION_DIR: Final = "dossier_transcriptions"
REGISTER_PATH: Final = Path("dossiers") / "candidate_register.json"
REGISTER_SCHEMA: Final = "gridq.candidate_register"
REGISTER_VERSION: Final = "1.0.0"


class DossierInputError(ValueError):
    """An input the dossier must not use: synthetic data on a real site, a file under another
    site's directory, a malformed register. Fail closed: the dossier is not built."""


def is_synthetic_site(site_id: str) -> bool:
    return site_id.startswith(SYNTHETIC_SITE_PREFIX)


# --------------------------------------------------------------------------------------------
# Which documents can evidence which field (Fable binding test rule 3: covers every field)
# --------------------------------------------------------------------------------------------

_CONNECTION_DOCS: Final = {
    EvidenceType.CONNECTION_AGREEMENT: frozenset({IssuerRole.DNO, IssuerRole.IDNO}),
    EvidenceType.CONNECTION_OFFER: frozenset({IssuerRole.DNO, IssuerRole.IDNO}),
}

#: field -> {admissible IC-4 type -> admissible issuer roles}. An empty map: no IC-4 document can
#: evidence the field (the licence area is public-source only; the funder has no IC-4 type, G9).
FIELD_EVIDENCE: Final[Mapping[DossierField, Mapping[EvidenceType, frozenset[IssuerRole]]]] = {
    DossierField.CONNECTION_TYPE: _CONNECTION_DOCS,
    DossierField.CONNECTION_TERMS: _CONNECTION_DOCS,
    DossierField.DNO: {
        **_CONNECTION_DOCS,
        EvidenceType.MPAN_REGISTRATION: frozenset(
            {
                IssuerRole.ELEXON,
                IssuerRole.SUPPLIER,
                IssuerRole.METER_OPERATOR,
                IssuerRole.DNO,
                IssuerRole.IDNO,
            }
        ),
    },
    DossierField.DNO_LICENCE_AREA: {},
    DossierField.METERING_CLASS: {
        EvidenceType.MPAN_REGISTRATION: frozenset(
            {
                IssuerRole.ELEXON,
                IssuerRole.SUPPLIER,
                IssuerRole.METER_OPERATOR,
                IssuerRole.DNO,
                IssuerRole.IDNO,
            }
        ),
        EvidenceType.METER_COMMISSIONING_CERTIFICATE: frozenset({IssuerRole.METER_OPERATOR}),
        EvidenceType.BMU_REGISTRATION: frozenset({IssuerRole.ELEXON}),
    },
    DossierField.NET_EXPORT: {
        **_CONNECTION_DOCS,
        EvidenceType.G99_APPROVAL: frozenset({IssuerRole.DNO, IssuerRole.IDNO}),
        EvidenceType.METERED_DATA_RECORD: frozenset({IssuerRole.METER_OPERATOR, IssuerRole.HOST}),
    },
    DossierField.ONSITE_GENERATION: {
        EvidenceType.G99_APPROVAL: frozenset({IssuerRole.DNO, IssuerRole.IDNO}),
        EvidenceType.CONNECTION_AGREEMENT: frozenset({IssuerRole.DNO, IssuerRole.IDNO}),
        EvidenceType.TECHNICAL_REPORT: frozenset({IssuerRole.INDEPENDENT_ENGINEER, IssuerRole.OEM}),
        EvidenceType.OPERATIONAL_DATA_RECORD: frozenset(
            {IssuerRole.HOST, IssuerRole.CUSTOMER, IssuerRole.OEM}
        ),
        EvidenceType.COMMISSIONING_RECORD: frozenset(
            {IssuerRole.INDEPENDENT_ENGINEER, IssuerRole.OEM, IssuerRole.HOST}
        ),
    },
    DossierField.COOLING_TOPOLOGY: {
        EvidenceType.OPERATIONAL_DATA_RECORD: frozenset(
            {IssuerRole.HOST, IssuerRole.CUSTOMER, IssuerRole.OEM}
        ),
        EvidenceType.TECHNICAL_REPORT: frozenset({IssuerRole.INDEPENDENT_ENGINEER, IssuerRole.OEM}),
    },
    DossierField.BESS_OWNER: {
        EvidenceType.SITE_ACCESS_INSTALLATION_AGREEMENT: frozenset({IssuerRole.HOST}),
    },
    DossierField.BESS_FUNDER: {},
}

#: Fields whose facts come from the customer (gated by the DPA). The licence area is public; the
#: funder is not the customer's to give.
CUSTOMER_SOURCED: Final = frozenset(DossierField) - {
    DossierField.DNO_LICENCE_AREA,
    DossierField.BESS_FUNDER,
}

#: Fields an owner may transcribe from a document. Connection type and terms come only as IC-1.
_TRANSCRIBABLE: Final = frozenset(
    {
        DossierField.DNO,
        DossierField.METERING_CLASS,
        DossierField.NET_EXPORT,
        DossierField.ONSITE_GENERATION,
        DossierField.COOLING_TOPOLOGY,
        DossierField.BESS_OWNER,
    }
)


# --------------------------------------------------------------------------------------------
# The register
# --------------------------------------------------------------------------------------------


class CandidateEntry(ContractModel):
    """One candidate site: whose it is, where its records are, and an owner-registered location."""

    site_id: NonEmptyStr
    customer: NonEmptyStr
    origin: Literal["dpa_manifest", "owner_register", "synthetic"]
    partner: NonEmptyStr | NotApplicable
    #: Repo-relative or absolute paths of the site's SiteModel records.
    record_paths: tuple[NonEmptyStr, ...]
    location: SiteLocation | NotApplicable

    @model_validator(mode="after")
    def _origin_matches_site(self) -> CandidateEntry:
        if (self.origin == "synthetic") != is_synthetic_site(self.site_id):
            raise ValueError("origin is 'synthetic' exactly for a SYNTHETIC- site")
        return self


class CandidateRegister(ContractModel):
    entries: tuple[CandidateEntry, ...]
    exclusions: tuple[Exclusion, ...]
    #: Dossier-level asks about the pipeline itself (e.g. no Chainergy site identified).
    pipeline_asks: tuple[DossierAsk, ...]

    @model_validator(mode="after")
    def _unique(self) -> CandidateRegister:
        ids = [e.site_id for e in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("a site appears once in the register")
        excluded = {x.site_id for x in self.exclusions}
        if excluded & set(ids):
            raise ValueError("an excluded site is not also an entry")
        return self


CHAINERGY_ASK: Final = DossierAsk.make(
    request=(
        "Identify the Chainergy candidate sites (site id and customer for each) so they can be "
        "added to the candidate register"
    ),
    holder=AskHolder.PARTNER,
    holder_name="Chainergy",
    scope="dossier",
)


def _display(customer: str) -> str:
    spec = CUSTOMERS.get(customer)
    return str(spec["display"]) if spec else customer


def _manifest_entries() -> list[CandidateEntry]:
    """One entry per customer site in the manifest, with that site's SiteModel records.

    A record is the site's when the site is the first the manifest maps it to; the manifest also
    maps shared records to every site they might describe (fail-closed attribution), and those
    attributions release nothing here.
    """
    manifest = load_manifest()
    entries: list[CandidateEntry] = []
    for customer, spec in CUSTOMERS.items():
        for site_id in spec["site_ids"]:
            paths = tuple(
                sorted(
                    d.path
                    for d in manifest.datasets
                    if d.kind == "site_facts" and d.site_ids and d.site_ids[0] == site_id
                )
            )
            entries.append(
                CandidateEntry(
                    site_id=site_id,
                    customer=customer,
                    origin="dpa_manifest",
                    partner=NotApplicable(kind="not_applicable", reason="direct customer"),
                    record_paths=paths,
                    location=NotApplicable(
                        kind="not_applicable", reason="no location registered by the owner"
                    ),
                )
            )
    return entries


class _RegisterFile(ContractModel):
    register_schema: Literal["gridq.candidate_register"]
    register_version: Literal["1.0.0"]
    entries: tuple[CandidateEntry, ...] = ()
    exclude: tuple[Exclusion, ...] = ()


def load_register_file(
    path: str | Path,
) -> tuple[tuple[CandidateEntry, ...], tuple[Exclusion, ...]]:
    try:
        parsed = _RegisterFile.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DossierInputError(f"the candidate register {path} is not readable: {exc}") from exc
    return parsed.entries, parsed.exclude


def default_register(register_file: str | Path | None = None) -> CandidateRegister:
    """Manifest sites, extended or overridden by the owner register file when it exists."""
    by_id = {e.site_id: e for e in _manifest_entries()}
    exclusions: tuple[Exclusion, ...] = ()
    path = Path(register_file) if register_file is not None else repo_root() / REGISTER_PATH
    if path.is_file():
        owner_entries, exclusions = load_register_file(path)
        for entry in owner_entries:
            by_id[entry.site_id] = entry
        for x in exclusions:
            by_id.pop(x.site_id, None)
    entries = tuple(sorted(by_id.values(), key=lambda e: e.site_id))
    has_chainergy = any(
        isinstance(e.partner, str) and e.partner.strip().lower() == "chainergy" for e in entries
    )
    return CandidateRegister(
        entries=entries,
        exclusions=tuple(sorted(exclusions, key=lambda x: x.site_id)),
        pipeline_asks=() if has_chainergy else (CHAINERGY_ASK,),
    )


# --------------------------------------------------------------------------------------------
# The DPA gate
# --------------------------------------------------------------------------------------------


def dpa_ask(customer: str) -> DossierAsk:
    """The binding DPA ask: one per customer, the prerequisite of every customer document ask."""
    return DossierAsk.make(
        request=f"DPA for {customer} not registered (WP-GRC-05)",
        holder=AskHolder.CUSTOMER,
        holder_name=_display(customer),
        scope=f"customer:{customer}",
        closes_with=(EvidenceType.DATA_PROCESSING_AGREEMENT,),
    )


@dataclass(frozen=True)
class SiteGate:
    """What the guard released for one site, and what it refused."""

    customer_data: Literal["released_under_dpa", "synthetic", "not_released"]
    blockers: tuple[str, ...]
    refusal: str
    #: (record path, note) for every record not read or not used.
    record_notes: tuple[tuple[str, str], ...] = ()
    #: Record paths the guard released.
    released_records: tuple[str, ...] = ()


def gate_site(
    entry: CandidateEntry, *, registry: tuple[EvidenceObject, ...], as_of: date
) -> SiteGate:
    """Ask the guard about the site and each of its records. Opens no file."""
    if is_synthetic_site(entry.site_id):
        return SiteGate("synthetic", (), "", (), entry.record_paths)
    try:
        require_dpa(registry, site_id=entry.site_id, as_of=as_of)
    except DpaEvidenceMissing as exc:
        notes: list[tuple[str, str]] = []
        for path in entry.record_paths:
            try:
                check_load(_resolve(path), site_id=entry.site_id, as_of=as_of)
            except DpaEvidenceMissing as refused:
                notes.append((path, f"not read: the DPA guard refused it ({refused})"))
            else:
                # The site has no DPA, so its records are not read even if one is not listed.
                notes.append((path, "not read: the site has no registered DPA"))
        return SiteGate("not_released", (entry.customer,), str(exc), tuple(notes), ())
    notes = []
    released: list[str] = []
    for path in entry.record_paths:
        try:
            check_load(_resolve(path), site_id=entry.site_id, as_of=as_of)
        except DpaEvidenceMissing as refused:
            notes.append((path, f"not read: the DPA guard refused it ({refused})"))
        else:
            released.append(path)
    return SiteGate("released_under_dpa", (), "", tuple(notes), tuple(released))


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else repo_root() / p


# --------------------------------------------------------------------------------------------
# Per-site owner inputs (read only after the gate)
# --------------------------------------------------------------------------------------------


class FieldTranscription(ContractModel):
    """An owner's typed reading of one dossier field from cited IC-4 documents.

    The value is evidenced only if at least one cited document passes the field's admission bar.
    ``locator`` names where in the document the fact is (page, clause, schedule).
    """

    record: Literal["gridq.dossier_transcription"]
    record_version: Literal["1.0.0"]
    record_class: Literal["registered", "synthetic_fixture"]
    site_id: NonEmptyStr
    field: DossierField
    value: FieldValue
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    locator: NonEmptyStr
    transcribed_on: IsoDate

    @model_validator(mode="after")
    def _value_fits_field(self) -> FieldTranscription:
        if self.field not in _TRANSCRIBABLE:
            raise ValueError(
                f"{self.field.value} is not transcribed: it comes from IC-1 instances or a "
                "public source"
            )
        if self.value.kind != VALUE_KIND[self.field]:
            raise ValueError(f"a {self.value.kind} value does not fit field {self.field.value}")
        return self


def _site_dir_files(root: Path, sub: str, site_id: str) -> list[Path]:
    directory = root / sub / site_id
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"))


def _log_read(path: Path, site_id: str, synthetic: bool) -> None:
    record_hh_access(
        str(path),
        action="read_dossier_input",
        site_id=site_id,
        dpa_basis="not_customer_data" if synthetic else "dpa",
    )


def read_site_agreements(root: Path, site_id: str) -> tuple[ConnectionAgreement, ...]:
    """The site's IC-1 instances. Call only after `gate_site` released the site."""
    out: list[ConnectionAgreement] = []
    for path in _site_dir_files(root, IC1_DIR, site_id):
        _log_read(path, site_id, is_synthetic_site(site_id))
        try:
            agreement = ConnectionAgreement.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise DossierInputError(f"{path.name} is not a valid IC-1 instance: {exc}") from exc
        if agreement.site_id != site_id:
            raise DossierInputError(
                f"{path.name} is filed under {site_id} but names site {agreement.site_id}"
            )
        out.append(agreement)
    return tuple(sorted(out, key=lambda a: a.content_hash()))


def read_site_transcriptions(root: Path, site_id: str) -> tuple[FieldTranscription, ...]:
    """The site's field transcriptions. Call only after `gate_site` released the site."""
    out: list[FieldTranscription] = []
    synthetic = is_synthetic_site(site_id)
    for path in _site_dir_files(root, TRANSCRIPTION_DIR, site_id):
        _log_read(path, site_id, synthetic)
        try:
            record = FieldTranscription.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise DossierInputError(f"{path.name} is not a valid transcription: {exc}") from exc
        if record.site_id != site_id:
            raise DossierInputError(
                f"{path.name} is filed under {site_id} but names site {record.site_id}"
            )
        if record.record_class == "synthetic_fixture" and not synthetic:
            raise DossierInputError(
                f"{path.name} is a synthetic transcription on real site {site_id}"
            )
        out.append(record)
    return tuple(sorted(out, key=content_hash))


# --------------------------------------------------------------------------------------------
# Admission of one IC-4 reference for one field
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Admission:
    """The result of putting one reference to a field's bar: evidence, or a candidate and asks."""

    evidence: Ic4Evidence | None
    candidate: CandidateEvidence | None
    asks: tuple[DossierAsk, ...] = field(default=())
    #: True when the field's standard document ask still applies (wrong document, expired...).
    needs_document: bool = False


def _short(h: str) -> str:
    return h.removeprefix("sha256:")[:12]


def admit(
    ref: EvidenceRef,
    *,
    field_name: DossierField,
    site_id: str,
    registry: Iterable[EvidenceObject],
    as_of: date,
    supports: str,
) -> Admission:
    """Put ``ref`` to ``field_name``'s admission bar for ``site_id`` as of ``as_of``."""
    synthetic_site = is_synthetic_site(site_id)
    scope = f"site:{site_id}"
    found = next((o for o in registry if o.content_hash() == ref.evidence_hash), None)
    if found is None:
        return Admission(
            None,
            CandidateEvidence(
                ref=ref,
                status_as_of=EvidenceStatus.ABSENT,
                verification="not_registered",
                why_not_admitted="no evidence object with this hash is in the registry",
            ),
            (
                DossierAsk.make(
                    request=(
                        f"Register the IC-4 evidence object {_short(ref.evidence_hash)} that "
                        f"the {field_name.value} input cites"
                    ),
                    holder=AskHolder.NEURA_OWNER,
                    scope=scope,
                ),
            ),
        )
    status = found.status_at(as_of)
    verification: Literal["verified", "unverified"] = (
        "verified" if found.is_verified else "unverified"
    )

    def refuse(why: str, *, needs_document: bool, asks: tuple[DossierAsk, ...] = ()) -> Admission:
        return Admission(
            None,
            CandidateEvidence(
                ref=ref, status_as_of=status, verification=verification, why_not_admitted=why
            ),
            asks,
            needs_document,
        )

    if found.contract_version != ref.contract_version:
        return refuse(
            f"the reference states IC-4 {ref.contract_version}; the object is "
            f"{found.contract_version}",
            needs_document=False,
            asks=(
                DossierAsk.make(
                    request=(
                        f"Correct the reference to evidence {_short(ref.evidence_hash)}: it "
                        f"states IC-4 {ref.contract_version}, the object is "
                        f"{found.contract_version}"
                    ),
                    holder=AskHolder.NEURA_OWNER,
                    scope=scope,
                ),
            ),
        )
    if found.record_class == "synthetic_fixture" and not synthetic_site:
        raise DossierInputError(
            f"evidence {_short(ref.evidence_hash)} is a synthetic fixture cited for real site "
            f"{site_id}"
        )
    if found.is_self_issued:
        return refuse(
            "Neura issued this record itself; a site fact needs a third-party document",
            needs_document=True,
        )
    roles = FIELD_EVIDENCE[field_name].get(found.evidence_type)
    if roles is None or found.issuer.role not in roles:
        return refuse(
            f"a {found.evidence_type.value} from a {found.issuer.role.value} does not evidence "
            f"{field_name.value}",
            needs_document=True,
        )
    if not (isinstance(found.scope, SiteScope) and found.scope.site_id == site_id):
        return refuse("the document is not scoped to this site", needs_document=True)
    if status is EvidenceStatus.VALIDITY_UNKNOWN:
        return refuse(
            "the document's effective date or expiry is not recorded",
            needs_document=False,
            asks=(
                DossierAsk.make(
                    request=(
                        f"Record the effective date and expiry of evidence "
                        f"{_short(ref.evidence_hash)} ({found.evidence_type.value}) from the "
                        "document"
                    ),
                    holder=AskHolder.NEURA_OWNER,
                    scope=scope,
                ),
            ),
        )
    if status is not EvidenceStatus.VALID:
        return refuse(f"the document is {status.value} as of {as_of}", needs_document=True)
    if not found.is_verified:
        return refuse(
            "the document has not been verified with its issuer",
            needs_document=False,
            asks=(
                DossierAsk.make(
                    request=(
                        f"Verify evidence {_short(ref.evidence_hash)} "
                        f"({found.evidence_type.value}) with its issuer"
                    ),
                    holder=AskHolder.NEURA_OWNER,
                    scope=scope,
                ),
            ),
        )
    return Admission(
        Ic4Evidence(
            kind="ic4",
            ref=ref,
            evidence_type=found.evidence_type,
            issuer_role=found.issuer.role,  # type: ignore[arg-type]  # self-issued refused above
            supports=supports,
        ),
        None,
    )


def evidence_files_under(root: Path) -> dict[str, Any]:
    """Counts of per-site input files (for the CLI summary; reads no file)."""
    return {
        sub: sum(1 for _ in (root / sub).glob("*/*.json")) if (root / sub).is_dir() else 0
        for sub in (IC1_DIR, TRANSCRIPTION_DIR)
    }
