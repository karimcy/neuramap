"""Build, render and write the candidate site dossier (WP-OPS-01, ``candidate_site_dossier``).

``build_dossier`` characterises every candidate site in the register by connection type and
IC-1 connection terms, connecting DNO, DNO licence area, metering class, net export, gensets and
on-site generation, cooling topology, BESS owner and BESS funder. Each field is evidenced or
unknown with its data asks (`gridq.ops.dossier_model`). Customer data is read only through the
DPA guard (`gridq.ops.dossier_inputs.gate_site`, then the guarded SiteModel loader).

The dossier makes no go/no-go statement. Its D-4 input map says only which fields each ADR-D4
criterion reads and whether they are evidenced; D-4 decides (D109).

The build is a pure function of its inputs and ``as_of``: no clock is read, so the same inputs
give byte-identical JSON and the same content hash.

``python -m gridq.ops.dossier --as-of YYYY-MM-DD`` writes the owner-side dossier (JSON and a
Markdown rendering of that JSON) into ``dossiers/``, and refuses to write a dossier naming a real
site anywhere git does not ignore (D112). Stdout carries counts and paths, never values.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final

from gridq.contracts._base import NotApplicable, Unknown, canonical_json, content_hash
from gridq.contracts.connection import (
    ConnectionAgreement,
    EvidenceBacked,
    ImportProfile,
    NetworkOperator,
    SyntheticArchetype,
)
from gridq.contracts.evidence import EvidenceObject, EvidenceType
from gridq.governance.dpa_guard import DpaEvidenceMissing, evidence_dir, load_evidence_registry
from gridq.governance.dpa_guard import repo_root as _repo_root
from gridq.ingestion.loaders import load_site_model_json
from gridq.ingestion.validators import SiteModelLoadError
from gridq.ops.dossier_inputs import (
    CUSTOMER_SOURCED,
    FIELD_EVIDENCE,
    CandidateEntry,
    CandidateRegister,
    DossierInputError,
    default_register,
    dpa_ask,
    gate_site,
    is_synthetic_site,
    read_site_agreements,
    read_site_transcriptions,
)
from gridq.ops.dossier_inputs import admit as _admit
from gridq.ops.dossier_model import (
    DOSSIER_SCHEMA,
    DOSSIER_SCHEMA_VERSION,
    NO_VERDICT_STATEMENT,
    AskHolder,
    CandidateEvidence,
    CandidateSiteDossier,
    ConnectionTermsValue,
    ConnectionTypeValue,
    ContractGap,
    D4Criterion,
    D4Input,
    D4Reference,
    DnoValue,
    DossierAsk,
    DossierField,
    EmbeddedAgreement,
    Evidenced,
    EvidenceItem,
    LedgerEntry,
    NetExportMeasure,
    NetExportValue,
    PublicSourceEvidence,
    RecordedUnevidenced,
    RecordNote,
    SiteDossier,
    SiteLocation,
    SyntheticArchetypeEvidence,
    UnknownField,
)
from gridq.ops.public_sources import BoundaryDataset, licence_area_at, load_boundary_dataset
from gridq.schemas.references import connection_agreement_ref

__all__ = [
    "ADR_D4_COMMIT",
    "ADR_D4_PATH",
    "ADR_D4_SHA256",
    "D4_CRITERIA",
    "DossierWriteRefused",
    "build_dossier",
    "main",
    "render_markdown",
    "write_dossier",
]

# ADR-D4, the rule this dossier feeds, pinned by file hash (D109). Not restated here.
ADR_D4_PATH: Final = "atlas:roadmap/adr/ADR-D4.md"
ADR_D4_SHA256: Final = "sha256:f0d0d3ceb466752136e16774a3b60d13e78b8e1d03ba872c8eeb132376275b17"
ADR_D4_COMMIT: Final = "9a94f16c8e8d18a40a0955087d50c301c6b4d51f"

D4_CRITERIA: Final = (
    D4Criterion(
        key="criterion_i",
        label="ADR-D4 criterion (i)",
        reads=(DossierField.CONNECTION_TYPE, DossierField.CONNECTION_TERMS),
    ),
    D4Criterion(key="criterion_ii", label="ADR-D4 criterion (ii)", reads=(DossierField.DNO,)),
    D4Criterion(
        key="criterion_iii", label="ADR-D4 criterion (iii)", reads=(DossierField.METERING_CLASS,)
    ),
    D4Criterion(
        key="criterion_iv",
        label="ADR-D4 criterion (iv)",
        reads=(DossierField.BESS_OWNER, DossierField.BESS_FUNDER),
    ),
    D4Criterion(key="criterion_v", label="ADR-D4 criterion (v)", reads=()),
    D4Criterion(
        key="criterion_vi", label="ADR-D4 criterion (vi)", reads=(DossierField.COOLING_TOPOLOGY,)
    ),
    D4Criterion(
        key="spec_net_export",
        label="spec D-4 input: net export",
        reads=(DossierField.NET_EXPORT,),
    ),
    D4Criterion(
        key="spec_onsite_generation_r13",
        label="spec D-4 input (R13): gensets and on-site generation",
        reads=(DossierField.ONSITE_GENERATION,),
    ),
)

#: IC-4 cannot carry a funding commitment (IC4_INVENTORY gap G9).
G9: Final = ContractGap(
    gap_id="IC4-G9",
    contract="IC-4 1.2.0",
    missing="an evidence type for a BESS funding commitment",
    fields=(DossierField.BESS_FUNDER,),
    owner="WP-BO-02 (next IC-4 minor, types only)",
)

FUNDER_ASK: Final = DossierAsk.make(
    request=(
        "A funding commitment naming the BESS funder for each candidate site; IC-4 has no "
        "evidence type for it yet (gap G9, WP-BO-02)"
    ),
    holder=AskHolder.FUNDER,
    scope="dossier",
)

#: The standard document ask per customer-sourced field: (request, holder).
_DOCUMENT_ASKS: Final[dict[DossierField, tuple[str, AskHolder]]] = {
    DossierField.CONNECTION_TYPE: (
        "Signed connection agreement or connection offer for the site's grid connection, all "
        "pages including any export, non-firm or ANM schedules",
        AskHolder.CUSTOMER,
    ),
    DossierField.CONNECTION_TERMS: (
        "Signed connection agreement or connection offer for the site's grid connection, all "
        "pages including any export, non-firm or ANM schedules",
        AskHolder.CUSTOMER,
    ),
    DossierField.DNO: (
        "A document naming the connecting network operator: the connection agreement, or the "
        "import MPAN with its registration",
        AskHolder.CUSTOMER,
    ),
    DossierField.METERING_CLASS: (
        "MPAN registration or meter details showing SVA or CVA settlement and the measurement "
        "class",
        AskHolder.CUSTOMER,
    ),
    DossierField.NET_EXPORT: (
        "The agreed export capacity from the connection agreement or G99 approval, and twelve "
        "months of metered export data",
        AskHolder.CUSTOMER,
    ),
    DossierField.ONSITE_GENERATION: (
        "A register of on-site generation and standby gensets (technology, rating, how each "
        "runs), with the G99 approval for any that runs in parallel with the grid; or a "
        "statement from the site that it has none",
        AskHolder.CUSTOMER,
    ),
    DossierField.COOLING_TOPOLOGY: (
        "Cooling plant design data: the plant schedule or a mechanical design report",
        AskHolder.CUSTOMER,
    ),
    DossierField.BESS_OWNER: (
        "The ownership clause of the site access and installation agreement for the BESS",
        AskHolder.HOST,
    ),
}

_LOCATION_ASK_TEXT: Final = (
    "Register the site's location (a published operator address or a survey point) in the "
    "candidate register, so the licence area can be read from published DNO boundary data"
)
_BOUNDARY_ASK: Final = DossierAsk.make(
    request=(
        "Download a published DNO licence-area boundary dataset (GeoJSON, WGS84) and register "
        "its source: URL, retrieval date and sha256 of the bytes"
    ),
    holder=AskHolder.NEURA_OWNER,
    scope="dossier",
)

_OPERATOR_PREFIXES: Final = (
    ("NGED", NetworkOperator.NGED),
    ("WPD", NetworkOperator.NGED),
    ("SSEN", NetworkOperator.SSEN),
    ("UKPN", NetworkOperator.UKPN),
    ("SPEN", NetworkOperator.SPEN),
    ("ENWL", NetworkOperator.ENWL),
    ("NPG", NetworkOperator.NPG),
)


class DossierWriteRefused(PermissionError):
    """A dossier naming a real site would be written where git does not ignore it."""


# --------------------------------------------------------------------------------------------
# Per-site accumulation
# --------------------------------------------------------------------------------------------


@dataclass
class _FieldState:
    contributions: list[tuple[Any, tuple[EvidenceItem, ...]]] = field(default_factory=list)
    candidates: list[CandidateEvidence] = field(default_factory=list)
    asks: list[DossierAsk] = field(default_factory=list)
    recorded: list[RecordedUnevidenced] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    needs_document: bool = False
    blocked: bool = False


def _short(h: str) -> str:
    return h.removeprefix("sha256:")[:12]


def _sorted_items(items: Iterable[EvidenceItem]) -> tuple[EvidenceItem, ...]:
    unique = {json.dumps(i.model_dump(mode="json"), sort_keys=True): i for i in items}
    return tuple(unique[k] for k in sorted(unique))


def _operator_for(region: str) -> NetworkOperator | None:
    token = region.strip().upper()
    for prefix, operator in _OPERATOR_PREFIXES:
        if token.startswith(prefix):
            return operator
    return None


def _profiled(agreement: ConnectionAgreement) -> str | Unknown:
    p = agreement.profiled_import
    if isinstance(p, ImportProfile):
        return "profiled"
    if isinstance(p, NotApplicable):
        return "not_profiled"
    return p


def _merge(
    name: DossierField, contributions: Sequence[tuple[Any, tuple[EvidenceItem, ...]]]
) -> tuple[Any, tuple[EvidenceItem, ...]] | str:
    """One value from the evidenced contributions, or the reason they cannot be one."""
    items = _sorted_items(i for _, its in contributions for i in its)
    values = [v for v, _ in contributions]
    if name is DossierField.DNO:
        operators = {v.operator for v in values}
        if len(operators) > 1:
            return "the evidenced inputs name different network operators"
        areas = {v.licence_area for v in values if isinstance(v.licence_area, str)}
        if len(areas) > 1:
            return "the evidenced inputs name different licence areas"
        area: str | Unknown = (
            areas.pop()
            if areas
            else Unknown(kind="unknown", reason="no evidenced input names the licence area")
        )
        return DnoValue(kind="dno", operator=operators.pop(), licence_area=area), items
    if name is DossierField.NET_EXPORT:
        by_basis: dict[str, set[float]] = defaultdict(set)
        for v in values:
            for m in v.measures:
                by_basis[m.basis].add(m.kw)
        if any(len(kws) > 1 for kws in by_basis.values()):
            return "the evidenced inputs state different export figures on the same basis"
        measures = tuple(
            NetExportMeasure(basis=b, kw=next(iter(by_basis[b])))  # type: ignore[arg-type]
            for b in sorted(by_basis)
        )
        return NetExportValue(kind="net_export", measures=measures), items
    distinct = {canonical_json(v): v for v in values}
    if len(distinct) > 1:
        return "the evidenced inputs state different values"
    return next(iter(distinct.values())), items


@dataclass
class _SiteBuild:
    entry: CandidateEntry
    registry: tuple[EvidenceObject, ...]
    as_of: date
    root: Path
    boundaries: BoundaryDataset | None
    state: dict[DossierField, _FieldState] = field(
        default_factory=lambda: {f: _FieldState() for f in DossierField}
    )
    notes: list[RecordNote] = field(default_factory=list)

    @property
    def site_id(self) -> str:
        return self.entry.site_id

    @property
    def synthetic(self) -> bool:
        return is_synthetic_site(self.entry.site_id)

    def admit(self, ref: Any, name: DossierField, supports: str) -> EvidenceItem | None:
        adm = _admit(
            ref,
            field_name=name,
            site_id=self.site_id,
            registry=self.registry,
            as_of=self.as_of,
            supports=supports,
        )
        st = self.state[name]
        if adm.evidence is not None:
            return adm.evidence
        if adm.candidate is not None:
            st.candidates.append(adm.candidate)
            st.reasons.append(
                f"evidence {_short(adm.candidate.ref.evidence_hash)} not admitted: "
                f"{adm.candidate.why_not_admitted}"
            )
        st.asks.extend(adm.asks)
        st.needs_document = st.needs_document or adm.needs_document
        return None

    # ----- IC-1 instances (D110) -----
    def take_agreements(self, agreements: Sequence[ConnectionAgreement]) -> set[str]:
        admitted: list[tuple[ConnectionAgreement, EvidenceItem]] = []
        for a in agreements:
            ref = connection_agreement_ref(a)
            h = ref.agreement_hash
            src = a.source
            item: EvidenceItem | None
            if isinstance(src, SyntheticArchetype):
                if not self.synthetic:
                    raise DossierInputError(
                        f"IC-1 instance {_short(h)} has a synthetic archetype source on real "
                        f"site {self.site_id}"
                    )
                item = SyntheticArchetypeEvidence(
                    kind="synthetic_archetype", agreement_hash=h, statement=src.statement
                )
            elif isinstance(src, EvidenceBacked):
                item = self.admit(src.evidence, DossierField.CONNECTION_TERMS, f"ic1:{h}")
                if item is None:
                    t = self.state[DossierField.CONNECTION_TYPE]
                    terms = self.state[DossierField.CONNECTION_TERMS]
                    t.candidates.extend(terms.candidates[-1:])
                    t.reasons.extend(terms.reasons[-1:])
                    t.asks.extend(terms.asks)
                    t.needs_document = terms.needs_document
            else:
                for name in (DossierField.CONNECTION_TYPE, DossierField.CONNECTION_TERMS):
                    self.state[name].reasons.append(
                        f"IC-1 instance {_short(h)} has no source document, so it evidences nothing"
                    )
                    self.state[name].needs_document = True
                item = None
            if item is not None:
                admitted.append((a, item))
        if not admitted:
            return {connection_agreement_ref(a).agreement_hash for a in agreements}
        embedded = tuple(
            sorted(
                (
                    EmbeddedAgreement(ref=connection_agreement_ref(a), agreement=a)
                    for a, _ in admitted
                ),
                key=lambda e: e.ref.agreement_hash,
            )
        )
        items = tuple(i for _, i in admitted)
        self.state[DossierField.CONNECTION_TERMS].contributions.append(
            (ConnectionTermsValue(kind="connection_terms", agreements=embedded), items)
        )
        archetypes = {a.archetype for a, _ in admitted}
        if len(archetypes) > 1:
            st = self.state[DossierField.CONNECTION_TYPE]
            hashes = ", ".join(_short(e.ref.agreement_hash) for e in embedded)
            st.reasons.append("the evidenced IC-1 instances state different archetypes")
            st.asks.append(
                DossierAsk.make(
                    request=(
                        f"Say which connection document is current: IC-1 instances {hashes} "
                        "state different connection archetypes"
                    ),
                    holder=AskHolder.CUSTOMER,
                    holder_name=self._display,
                    scope=f"site:{self.site_id}",
                    closes_with=(
                        EvidenceType.CONNECTION_AGREEMENT,
                        EvidenceType.CONNECTION_OFFER,
                    ),
                )
            )
            st.blocked = True
        else:
            profiles = {
                p if isinstance(p, str) else canonical_json(p)
                for p in (_profiled(a) for a, _ in admitted)
            }
            profiled: Any = (
                profiles.pop()
                if len(profiles) == 1 and next(iter(profiles)) in ("profiled", "not_profiled")
                else Unknown(
                    kind="unknown",
                    reason="the IC-1 instances do not state, or do not agree on, a profiled import",
                )
            )
            self.state[DossierField.CONNECTION_TYPE].contributions.append(
                (
                    ConnectionTypeValue(
                        kind="connection_type",
                        archetype=archetypes.pop(),
                        profiled_import=profiled,
                    ),
                    items,
                )
            )
        for a, item in admitted:
            if isinstance(a.network_operator, NetworkOperator):
                self.state[DossierField.DNO].contributions.append(
                    (
                        DnoValue(
                            kind="dno",
                            operator=a.network_operator,
                            licence_area=Unknown(
                                kind="unknown", reason="the IC-1 instance names no licence area"
                            ),
                        ),
                        (item,),
                    )
                )
            else:
                self.state[DossierField.DNO].reasons.append(
                    f"IC-1 instance {_short(connection_agreement_ref(a).agreement_hash)} does "
                    "not name its network operator"
                )
        return {connection_agreement_ref(a).agreement_hash for a in agreements}

    @property
    def _display(self) -> str:
        from gridq.governance.customer_data import CUSTOMERS

        spec = CUSTOMERS.get(self.entry.customer)
        return str(spec["display"]) if spec else self.entry.customer

    # ----- transcriptions (D107) -----
    def take_transcriptions(self, records: Sequence[Any]) -> None:
        for t in records:
            items: list[EvidenceItem] = []
            for ref in t.evidence:
                item = self.admit(ref, t.field, f"transcription:{t.locator}")
                if item is not None:
                    items.append(item)
            if items:
                self.state[t.field].contributions.append((t.value, tuple(items)))
            else:
                self.state[t.field].recorded.append(
                    RecordedUnevidenced(
                        record=f"transcription:{_short(content_hash(t))}",
                        field_path=t.locator,
                        value=t.value,
                    )
                )

    # ----- released customer records -----
    def take_record(self, path: str, ic1_hashes: set[str]) -> None:
        resolved = Path(path) if Path(path).is_absolute() else _repo_root() / path
        try:
            site = load_site_model_json(resolved, run_cross_checks=False)
        except DpaEvidenceMissing as exc:
            note = f"not read: the DPA guard refused it ({exc})"
            self.notes.append(RecordNote(record=path, note=note))
            return
        except SiteModelLoadError as exc:
            self.notes.append(RecordNote(record=path, note=f"not usable: {exc}"))
            return
        if site.site_id != self.site_id:
            self.notes.append(
                RecordNote(record=path, note=f"read; it names site {site.site_id}, so not used")
            )
            return
        self.notes.append(
            RecordNote(
                record=path,
                note="read (synthetic site)" if self.synthetic else "read under the site's DPA",
            )
        )
        links = site.evidence_refs or ()
        facts: list[tuple[DossierField, str, Any]] = []
        region = site.connection.dno_region
        if region:
            operator = _operator_for(region)
            if operator is None:
                self.notes.append(
                    RecordNote(
                        record=path,
                        note="connection.dno_region does not name a known network operator group",
                    )
                )
            else:
                facts.append(
                    (
                        DossierField.DNO,
                        "connection.dno_region",
                        DnoValue(kind="dno", operator=operator, licence_area=region.strip()),
                    )
                )
        if site.connection.mec_kw is not None:
            facts.append(
                (
                    DossierField.NET_EXPORT,
                    "connection.mec_kw",
                    NetExportValue(
                        kind="net_export",
                        measures=(
                            NetExportMeasure(
                                basis="agreed_export_capacity", kw=float(site.connection.mec_kw)
                            ),
                        ),
                    ),
                )
            )
        if site.load_data.max_export_kw is not None:
            facts.append(
                (
                    DossierField.NET_EXPORT,
                    "load_data.max_export_kw",
                    NetExportValue(
                        kind="net_export",
                        measures=(
                            NetExportMeasure(
                                basis="metered_peak_net_export",
                                kw=float(site.load_data.max_export_kw),
                            ),
                        ),
                    ),
                )
            )
        for name, field_path, value in facts:
            items = [
                item
                for link in links
                if link.supports == field_path
                for item in [self.admit(link.evidence, name, f"record:{path}#{field_path}")]
                if item is not None
            ]
            if items:
                self.state[name].contributions.append((value, tuple(items)))
            else:
                self.state[name].recorded.append(
                    RecordedUnevidenced(record=path, field_path=field_path, value=value)
                )
                self.state[name].needs_document = True
        ref = site.connection.agreement
        if ref is not None and ref.agreement_hash not in ic1_hashes:
            for name in (DossierField.CONNECTION_TYPE, DossierField.CONNECTION_TERMS):
                st = self.state[name]
                st.blocked = True
                st.reasons.append(
                    f"the site record names IC-1 agreement {_short(ref.agreement_hash)}, which "
                    "is not among the dossier's connection-agreement inputs"
                )
                st.asks.append(
                    DossierAsk.make(
                        request=(
                            f"Register the IC-1 agreement {_short(ref.agreement_hash)} that the "
                            "site record names among the dossier's connection-agreement inputs"
                        ),
                        holder=AskHolder.NEURA_OWNER,
                        scope=f"site:{self.site_id}",
                    )
                )

    # ----- the licence area (public channel, D111) -----
    def licence_area(self) -> Evidenced[Any] | UnknownField:
        location = self.entry.location
        asks: list[DossierAsk] = []
        reasons: list[str] = []
        if isinstance(location, SiteLocation):
            if location.basis.basis == "synthetic_fixture" and not self.synthetic:
                raise DossierInputError(
                    f"real site {self.site_id} has a synthetic fixture location"
                )
        else:
            reasons.append("no location is registered for the site")
            asks.append(
                DossierAsk.make(
                    request=_LOCATION_ASK_TEXT,
                    holder=AskHolder.NEURA_OWNER,
                    scope=f"site:{self.site_id}",
                )
            )
        if self.boundaries is None:
            reasons.append("no published licence-area boundary dataset is registered")
            asks.append(_BOUNDARY_ASK)
        elif self.boundaries.source.record_class == "synthetic_fixture" and not self.synthetic:
            raise DossierInputError(
                f"a synthetic fixture boundary dataset cannot place real site {self.site_id}"
            )
        if asks or not isinstance(location, SiteLocation) or self.boundaries is None:
            return UnknownField(status="unknown", reason="; ".join(reasons), asks=tuple(asks))
        lookup = licence_area_at(self.boundaries, location)
        if lookup.value is None:
            return UnknownField(
                status="unknown",
                reason=lookup.why_not,
                asks=(
                    DossierAsk.make(
                        request=(f"Survey the site's connection point position: {lookup.why_not}"),
                        holder=AskHolder.NEURA_OWNER,
                        scope=f"site:{self.site_id}",
                    ),
                ),
            )
        return Evidenced[Any](
            status="evidenced",
            value=lookup.value,
            evidence=(
                PublicSourceEvidence(
                    kind="public_source",
                    source=self.boundaries.source,
                    supports=f"feature:{lookup.value.boundary_feature}",
                ),
            ),
        )

    # ----- one field -----
    def resolve(
        self, name: DossierField, dpa_asks: tuple[DossierAsk, ...]
    ) -> Evidenced[Any] | UnknownField:
        st = self.state[name]
        if name is DossierField.BESS_FUNDER:
            return UnknownField(
                status="unknown",
                reason=(
                    "IC-4 1.2.0 has no evidence type for a funding commitment (gap G9), so the "
                    "funder cannot be evidenced"
                ),
                asks=(FUNDER_ASK,),
            )
        if st.contributions and not st.blocked:
            merged = _merge(name, st.contributions)
            if not isinstance(merged, str):
                value, items = merged
                return Evidenced[Any](status="evidenced", value=value, evidence=items)
            st.reasons.append(merged)
            st.asks.append(
                DossierAsk.make(
                    request=(f"Say which document is current for {name.value}: {merged}"),
                    holder=AskHolder.CUSTOMER,
                    holder_name=self._display,
                    scope=f"site:{self.site_id}",
                    closes_with=tuple(sorted(FIELD_EVIDENCE[name])),
                )
            )
        request, holder = _DOCUMENT_ASKS[name]
        prereqs = tuple(a.ask_id for a in dpa_asks) if name in CUSTOMER_SOURCED else ()
        document_ask = DossierAsk.make(
            request=request,
            holder=holder,
            holder_name=self._display,
            scope=f"site:{self.site_id}",
            closes_with=tuple(sorted(FIELD_EVIDENCE[name])),
            prerequisites=prereqs,
        )
        asks: list[DossierAsk] = list(dpa_asks) if name in CUSTOMER_SOURCED else []
        if st.needs_document or not st.asks or dpa_asks:
            asks.append(document_ask)
        asks.extend(st.asks)
        if dpa_asks:
            reason = (
                f"the DPA guard has not released this site's customer data: no valid, verified "
                f"DPA is registered for {self.entry.customer} (WP-GRC-05)"
            )
        elif st.reasons:
            reason = "; ".join(dict.fromkeys(st.reasons))
        else:
            reason = "no admissible document has been registered for this field"
        return UnknownField(
            status="unknown",
            reason=reason,
            asks=tuple(dict((a.ask_id, a) for a in asks).values()),
            candidate_evidence=tuple(dict((canonical_json(c), c) for c in st.candidates).values()),
            recorded_unevidenced=tuple(sorted(st.recorded, key=lambda r: (r.record, r.field_path))),
        )


def _d4_inputs(fields: dict[DossierField, Evidenced[Any] | UnknownField]) -> tuple[D4Input, ...]:
    out: list[D4Input] = []
    for c in D4_CRITERIA:
        if not c.reads:
            out.append(
                D4Input(
                    key=c.key,
                    completeness="not_characterised_by_this_dossier",
                    open_asks=(),
                    open_ask_count=0,
                )
            )
            continue
        unknowns = [f for f in (fields[name] for name in c.reads) if isinstance(f, UnknownField)]
        open_ids = sorted({a.ask_id for f in unknowns for a in f.asks})
        out.append(
            D4Input(
                key=c.key,
                completeness="inputs_open" if open_ids else "all_inputs_evidenced",
                open_asks=tuple(open_ids),
                open_ask_count=len(open_ids),
            )
        )
    return tuple(out)


def _build_site(
    entry: CandidateEntry,
    *,
    registry: tuple[EvidenceObject, ...],
    as_of: date,
    root: Path,
    boundaries: BoundaryDataset | None,
) -> SiteDossier:
    build = _SiteBuild(entry, registry, as_of, root, boundaries)
    gate = gate_site(entry, registry=registry, as_of=as_of)
    for path, note in gate.record_notes:
        build.notes.append(RecordNote(record=path, note=note))
    dpa_asks = tuple(dpa_ask(c) for c in gate.blockers)
    if gate.customer_data != "not_released":
        ic1_hashes = build.take_agreements(read_site_agreements(root, entry.site_id))
        build.take_transcriptions(read_site_transcriptions(root, entry.site_id))
        for path in gate.released_records:
            build.take_record(path, ic1_hashes)
    fields: dict[DossierField, Evidenced[Any] | UnknownField] = {}
    for name in DossierField:
        if name is DossierField.DNO_LICENCE_AREA:
            fields[name] = build.licence_area()
        else:
            fields[name] = build.resolve(name, dpa_asks)
    field_values: dict[str, Any] = {name.value: value for name, value in fields.items()}
    return SiteDossier(
        site_id=entry.site_id,
        customer=entry.customer,
        origin=entry.origin,
        partner=entry.partner,
        customer_data=gate.customer_data,
        dpa_blockers=gate.blockers,
        records=tuple(sorted(build.notes, key=lambda n: (n.record, n.note))),
        d4_inputs=_d4_inputs(fields),
        **field_values,
    )


def _ledger(
    sites: Sequence[SiteDossier], dossier_asks: Sequence[DossierAsk]
) -> tuple[LedgerEntry, ...]:
    """De-duplicated asks, prerequisites first, then by how many unknowns each serves."""
    asks: dict[str, DossierAsk] = {}
    uses: dict[str, list[str]] = defaultdict(list)

    def add(ask: DossierAsk, use: str) -> None:
        known = asks.setdefault(ask.ask_id, ask)
        if known != ask:
            raise ValueError(f"two asks share id {ask.ask_id} but differ")
        if use not in uses[ask.ask_id]:
            uses[ask.ask_id].append(use)

    for s in sites:
        for name in DossierField:
            f = s.field(name)
            if isinstance(f, UnknownField):
                for a in f.asks:
                    add(a, f"{s.site_id}/{name.value}")
    for a in dossier_asks:
        add(a, "dossier")
    for a in asks.values():
        missing = [p for p in a.prerequisites if p not in asks]
        if missing:
            raise ValueError(f"ask {a.ask_id} has prerequisites not in the dossier: {missing}")
    placed: list[str] = []
    remaining = set(asks)
    while remaining:
        ready = [i for i in remaining if all(p in placed for p in asks[i].prerequisites)]
        if not ready:
            raise ValueError("the ask prerequisites form a cycle")
        nxt = min(ready, key=lambda i: (-len(uses[i]), i))
        placed.append(nxt)
        remaining.discard(nxt)
    return tuple(
        LedgerEntry(
            position=n,
            ask=asks[i],
            leverage=len(uses[i]),
            unknowns=tuple(sorted(uses[i])),
        )
        for n, i in enumerate(placed, start=1)
    )


def build_dossier(
    register: CandidateRegister | None = None,
    *,
    as_of: date,
    evidence_root: str | Path | None = None,
    boundaries: BoundaryDataset | None = None,
) -> CandidateSiteDossier:
    """The dossier for every site in ``register`` (default: `default_register`) as of ``as_of``."""
    reg = register if register is not None else default_register()
    root = Path(evidence_root) if evidence_root is not None else evidence_dir()
    registry = load_evidence_registry(root)
    sites = tuple(
        _build_site(e, registry=registry, as_of=as_of, root=root, boundaries=boundaries)
        for e in sorted(reg.entries, key=lambda e: e.site_id)
    )
    dossier_asks = tuple(sorted(reg.pipeline_asks, key=lambda a: a.ask_id))
    return CandidateSiteDossier(
        dossier_schema=DOSSIER_SCHEMA,
        dossier_schema_version=DOSSIER_SCHEMA_VERSION,
        work_package="WP-OPS-01",
        as_of=as_of,
        statement=NO_VERDICT_STATEMENT,
        d4=D4Reference(
            adr="ADR-D4",
            path=ADR_D4_PATH,
            file_sha256=ADR_D4_SHA256,
            atlas_commit=ADR_D4_COMMIT,
            statement=NO_VERDICT_STATEMENT,
            criteria=D4_CRITERIA,
        ),
        sites=sites,
        excluded=reg.exclusions,
        dossier_asks=dossier_asks,
        ask_ledger=_ledger(sites, dossier_asks),
        contract_gaps=(G9,),
    )


# --------------------------------------------------------------------------------------------
# Rendering: Markdown is rendered from the canonical JSON only, and carries its hash
# --------------------------------------------------------------------------------------------


def dossier_json(dossier: CandidateSiteDossier) -> str:
    """The written JSON: the canonical JSON, indented. Same inputs give the same bytes."""
    return (
        json.dumps(
            json.loads(canonical_json(dossier)), indent=2, sort_keys=True, ensure_ascii=False
        )
        + "\n"
    )


def _value_text(v: dict[str, Any]) -> str:
    kind = v["kind"]
    if kind == "connection_type":
        p = v["profiled_import"]
        return f"{v['archetype']}; import {p if isinstance(p, str) else 'profile unknown'}"
    if kind == "connection_terms":
        return "IC-1 " + ", ".join(
            f"{a['agreement']['agreement_ref']} ({_short(a['ref']['agreement_hash'])})"
            for a in v["agreements"]
        )
    if kind == "dno":
        area = v["licence_area"]
        return v["operator"] + (f" ({area})" if isinstance(area, str) else "")
    if kind == "licence_area_containing_site":
        return f"{v['licence_area']} (margin {v['margin_km']} km)"
    if kind == "metering_class":
        mc = v["measurement_class"]
        return v["settlement"].upper() + (f" class {mc}" if isinstance(mc, str) else "")
    if kind == "net_export":
        return "; ".join(f"{m['basis']} {m['kw']} kW" for m in v["measures"])
    if kind == "onsite_generation":
        if v["statement"] == "no_onsite_generation":
            return "no on-site generation"
        return "; ".join(
            f"{a['technology']} "
            f"{a['rated_kw'] if not isinstance(a['rated_kw'], dict) else '? '}kW "
            f"({a['operation'] if isinstance(a['operation'], str) else 'operation unknown'})"
            for a in v["assets"]
        )
    if kind == "cooling_topology":
        return ", ".join(v["plant"])
    if kind == "bess_owner":
        return f"{v['owner_type']}: {v['owner_name']}"
    return kind


def _evidence_text(e: dict[str, Any]) -> str:
    if e["kind"] == "ic4":
        return f"IC-4 {e['evidence_type']} ({e['issuer_role']}) {_short(e['ref']['evidence_hash'])}"
    if e["kind"] == "public_source":
        s = e["source"]
        return f"{s['publisher']}, {s['title']} ({s['url']}, retrieved {s['retrieved_on']})"
    return f"synthetic archetype IC-1 {_short(e['agreement_hash'])}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(dossier: CandidateSiteDossier) -> str:
    """A reading copy, rendered from the canonical JSON (never from anything else)."""
    canonical = canonical_json(dossier)
    d = json.loads(canonical)
    digest = dossier.content_hash()
    position = {e["ask"]["ask_id"]: e["position"] for e in d["ask_ledger"]}
    lines = [
        f"# Candidate site dossier (WP-OPS-01), as of {d['as_of']}",
        "",
        f"> {d['statement']}",
        "",
        f"- Content hash of the JSON this page is rendered from: `{digest}`",
        f"- Schema: {d['dossier_schema']} {d['dossier_schema_version']}",
        f"- D-4 rule: {d['d4']['adr']} at `{d['d4']['path']}`, file `{d['d4']['file_sha256']}`, "
        f"atlas commit `{d['d4']['atlas_commit'][:12]}`",
        f"- Sites characterised: {len(d['sites'])}; excluded by the owner: {len(d['excluded'])}",
        "",
        "## Data asks",
        "",
        "Each ask once, prerequisites first, then by how many unknown fields it serves.",
        "",
        "| # | Ask | Holder | Closes with | After | Unknown fields served |",
        "|---|---|---|---|---|---|",
    ]
    for e in d["ask_ledger"]:
        a = e["ask"]
        name = a["holder_name"]
        holder = a["holder"] + (f" ({name})" if isinstance(name, str) else "")
        after = ", ".join(f"#{position[p]}" for p in a["prerequisites"]) or "-"
        closes = ", ".join(a["closes_with"]) or "no IC-4 type yet"
        lines.append(
            f"| {e['position']} | {_cell(a['request'])} | {holder} | {closes} | {after} | "
            f"{e['leverage']} |"
        )
    labels = {c["key"]: c["label"] for c in d["d4"]["criteria"]}
    for s in d["sites"]:
        partner = s["partner"] if isinstance(s["partner"], str) else "none"
        lines += [
            "",
            f"## {s['site_id']}",
            "",
            f"Customer: {s['customer']}. Origin: {s['origin']}. Partner: {partner}. "
            f"Customer data: {s['customer_data']}.",
            "",
            "| Field | Status | Value | Evidence | Asks |",
            "|---|---|---|---|---|",
        ]
        recorded: list[str] = []
        for name in DossierField:
            f = s[name.value]
            if f["status"] == "evidenced":
                lines.append(
                    f"| {name.value} | evidenced | {_cell(_value_text(f['value']))} | "
                    f"{_cell('; '.join(_evidence_text(e) for e in f['evidence']))} | - |"
                )
            else:
                asks = ", ".join(f"#{position[a['ask_id']]}" for a in f["asks"])
                lines.append(f"| {name.value} | unknown: {_cell(f['reason'])} | - | - | {asks} |")
                for r in f["recorded_unevidenced"]:
                    recorded.append(
                        f"- {name.value}: `{r['record']}` {r['field_path']} states "
                        f"{_value_text(r['value'])} (no admissible document)"
                    )
                for c in f["candidate_evidence"]:
                    recorded.append(
                        f"- {name.value}: document {_short(c['ref']['evidence_hash'])} not "
                        f"admitted ({c['why_not_admitted']})"
                    )
        if recorded:
            lines += ["", "Recorded but not evidenced:", "", *recorded]
        if s["records"]:
            lines += ["", "Customer records:", ""]
            lines += [f"- `{r['record']}`: {r['note']}" for r in s["records"]]
        lines += ["", "| D-4 input | Reads | Inputs | Open asks |", "|---|---|---|---|"]
        reads = {c["key"]: ", ".join(c["reads"]) or "-" for c in d["d4"]["criteria"]}
        for i in s["d4_inputs"]:
            lines.append(
                f"| {labels[i['key']]} | {reads[i['key']]} | {i['completeness']} | "
                f"{i['open_ask_count']} |"
            )
    if d["excluded"]:
        lines += ["", "## Excluded by the owner", ""]
        lines += [f"- {x['site_id']}: {x['reason']}" for x in d["excluded"]]
    lines += ["", "## Contract gaps", ""]
    lines += [
        f"- {g['gap_id']} ({g['contract']}): no {g['missing']}; fields "
        f"{', '.join(g['fields'])}; owner {g['owner']}"
        for g in d["contract_gaps"]
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------
# Writing (fail closed: a real-site dossier only into a git-ignored path)
# --------------------------------------------------------------------------------------------


def _require_git_ignored(path: Path) -> None:
    anchor = path.parent
    while not anchor.exists():
        anchor = anchor.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(anchor), "check-ignore", "-q", str(path)],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise DossierWriteRefused(f"git is not available to confirm {path} is ignored") from exc
    if result.returncode != 0:
        raise DossierWriteRefused(
            f"{path} is not ignored by git: a dossier naming a real site is written only to a "
            "git-ignored path (dossiers/ in this repository)"
        )


def write_dossier(dossier: CandidateSiteDossier, out_dir: str | Path) -> tuple[Path, Path]:
    """Write the JSON and its Markdown rendering. Returns their paths."""
    out = Path(out_dir).resolve()
    stem = f"candidate_site_dossier_{dossier.as_of.isoformat()}_{_short(dossier.content_hash())}"
    json_path, md_path = out / f"{stem}.json", out / f"{stem}.md"
    if dossier.has_real_site:
        for p in (json_path, md_path):
            _require_git_ignored(p)
    out.mkdir(parents=True, exist_ok=True)
    json_path.write_text(dossier_json(dossier), encoding="utf-8")
    md_path.write_text(render_markdown(dossier), encoding="utf-8")
    return json_path, md_path


def _summary(dossier: CandidateSiteDossier) -> dict[str, int]:
    fields = [s.field(n) for s in dossier.sites for n in DossierField]
    return {
        "sites": len(dossier.sites),
        "sites_released_under_dpa": sum(
            s.customer_data == "released_under_dpa" for s in dossier.sites
        ),
        "fields": len(fields),
        "fields_evidenced": sum(isinstance(f, Evidenced) for f in fields),
        "asks": len(dossier.ask_ledger),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gridq.ops.dossier",
        description="Build the candidate site dossier (WP-OPS-01) into a git-ignored directory.",
    )
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    parser.add_argument("--out", type=Path, default=_repo_root() / "dossiers")
    parser.add_argument("--register", type=Path, default=None)
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--boundaries", type=Path, default=None)
    parser.add_argument("--boundaries-source", type=Path, default=None)
    parser.add_argument("--area-property", default=None)
    args = parser.parse_args(argv)
    try:
        boundaries = None
        if args.boundaries is not None:
            if args.boundaries_source is None or args.area_property is None:
                parser.error("--boundaries needs --boundaries-source and --area-property")
            from gridq.ops.dossier_model import PublicSource

            source = PublicSource.model_validate_json(
                args.boundaries_source.read_text(encoding="utf-8")
            )
            boundaries = load_boundary_dataset(
                args.boundaries, source=source, area_property=args.area_property
            )
        dossier = build_dossier(
            default_register(args.register),
            as_of=args.as_of,
            evidence_root=args.evidence_dir,
            boundaries=boundaries,
        )
        json_path, md_path = write_dossier(dossier, args.out)
    except DossierWriteRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (DossierInputError, ValueError) as exc:
        print(f"input error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    for key, value in _summary(dossier).items():
        print(f"{key}: {value}")
    print(f"content_hash: {dossier.content_hash()}")
    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
