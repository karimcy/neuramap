"""The release record ``ramp_ridethrough_screen`` (WP-SITE-06; spec A3-03, A3-04).

`screen_ramp_ridethrough` reads one IC-1 `ConnectionAgreement`, the declared BESS design, the
site's evidenced study inputs and (optionally) a minute net-import series, and returns one
`RampRidethroughScreen`: the ramp screen, the RVC screen, the ride-through flag and the PSAL
handoff bundle, with every clause cited, every gap a typed ask and the person-only asks listed.

Refusals (raised, never downgraded): a synthetic IC-1 archetype, declaration, study input, series
or evidence object in a registered run; a synthetic run without a ``SYNTHETIC-`` site id; inputs
naming different sites; an `EvidenceRef` that does not resolve; a Neura self-issued record where
third-party evidence is required.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import date
from typing import Annotated, Any, Final, Literal

from pydantic import Field, model_validator

from gridq.adequacy._common import RecordClass
from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    content_hash,
)
from gridq.contracts.connection import ConnectionAgreement, EvidenceBacked, SyntheticArchetype
from gridq.contracts.evidence import (
    EvidenceObject,
    EvidenceRef,
    EvidenceRefused,
    EvidenceType,
    resolve_ref,
)
from gridq.ops.dossier_model import DossierAsk
from gridq.ramp_ridethrough._common import sorted_asks
from gridq.ramp_ridethrough.handoff import (
    CaseReadiness,
    PsalBundle,
    build_bundle,
    case_readiness,
)
from gridq.ramp_ridethrough.inputs import (
    BessDeclaration,
    EvidencedQuantity,
    NetImportSeries,
    StudyInputs,
)
from gridq.ramp_ridethrough.ramp import AgreedRamp, NoLimitAgreed, RampTermsUnknown, screen_ramp
from gridq.ramp_ridethrough.ridethrough import RideThroughFlag, screen_ridethrough
from gridq.ramp_ridethrough.rvc import RvcScreen, screen_rvc
from gridq.ramp_ridethrough.sources import CURRENCY, PERSON_ONLY_ASKS, SOURCES, Currency, SourceId

__all__ = [
    "METHOD_VERSION",
    "SCREEN_SCHEMA",
    "RampRidethroughScreen",
    "SourceRecord",
    "render_summary",
    "screen_hash_of",
    "screen_ramp_ridethrough",
]

SCREEN_SCHEMA: Final = "gridq.ramp_ridethrough_screen"
METHOD_VERSION: Final = "gridq.ramp_ridethrough/1.0.0"

CAVEAT_UNCONFIRMED: Final = (
    "SCREEN ONLY, standards currency unconfirmed: judged against G99 Issue 2 (2025), EREC P28 "
    "Issue 2 (2018) and Grid Code I6R33 as retrieved on 2026-11-26. Fault ride-through and fast "
    "fault current are delivered by the OEM PCS/PPC and studied by PSAL; Neura does not write "
    "protection-grade controls."
)
CAVEAT_CONFIRMED: Final = (
    "SCREEN ONLY: judged against the cited standards, whose currency has been confirmed. Fault "
    "ride-through and fast fault current are delivered by the OEM PCS/PPC and studied by PSAL; "
    "Neura does not write protection-grade controls."
)

_AGREEMENT_TYPES: Final = frozenset(
    {EvidenceType.CONNECTION_AGREEMENT, EvidenceType.CONNECTION_OFFER}
)


class SourceRecord(ContractModel):
    source_id: SourceId
    reference: NonEmptyStr
    url: NonEmptyStr
    retrieved_on: IsoDate
    sha256: NonEmptyStr
    currency: Currency


def _source_records() -> tuple[SourceRecord, ...]:
    return tuple(
        SourceRecord(
            source_id=s.source_id,  # type: ignore[arg-type]
            reference=s.reference,
            url=s.url,
            retrieved_on=date.fromisoformat(s.retrieved_on),
            sha256=s.sha256,
            currency=CURRENCY[s.source_id],
        )
        for s in SOURCES.values()
    )


RampOut = Annotated[AgreedRamp | NoLimitAgreed | RampTermsUnknown, Field(discriminator="kind")]


class RampRidethroughScreen(ContractModel):
    schema_name: Literal["gridq.ramp_ridethrough_screen"] = SCREEN_SCHEMA
    schema_version: Literal["1.0.0"] = "1.0.0"
    method_version: Literal["gridq.ramp_ridethrough/1.0.0"] = METHOD_VERSION
    site_id: NonEmptyStr
    record_class: RecordClass
    as_of: IsoDate
    caveat: Literal[
        "SCREEN ONLY, standards currency unconfirmed: judged against G99 Issue 2 (2025), EREC P28 "
        "Issue 2 (2018) and Grid Code I6R33 as retrieved on 2026-11-26. Fault ride-through and "
        "fast fault current are delivered by the OEM PCS/PPC and studied by PSAL; Neura does not "
        "write protection-grade controls.",
        "SCREEN ONLY: judged against the cited standards, whose currency has been confirmed. "
        "Fault ride-through and fast fault current are delivered by the OEM PCS/PPC and studied "
        "by PSAL; Neura does not write protection-grade controls.",
    ]
    agreement_hash: Sha256
    agreement_source: Literal["evidence_backed", "synthetic_archetype", "unknown"]
    declaration_hash: Sha256
    study_inputs_hash: Sha256
    series_hash: Sha256 | NotApplicable
    ramp: RampOut
    rvc: RvcScreen
    ridethrough: RideThroughFlag
    handoff: PsalBundle
    #: Derived from ``handoff`` (checked on validation), never set independently.
    case_readiness: tuple[CaseReadiness, ...]
    sources: tuple[SourceRecord, ...]
    person_only_asks: tuple[DossierAsk, ...]
    data_requests: tuple[DossierAsk, ...]
    screen_hash: Sha256

    @model_validator(mode="after")
    def _consistent(self) -> RampRidethroughScreen:
        if self.case_readiness != case_readiness(self.handoff):
            raise ValueError("case_readiness is derived from the handoff bundle")
        if self.screen_hash != screen_hash_of(self):
            raise ValueError("screen_hash does not match the record")
        if self.record_class == "synthetic_fixture" and not self.site_id.startswith("SYNTHETIC-"):
            raise ValueError("a synthetic screen carries a SYNTHETIC- site id")
        return self


def screen_hash_of(record: RampRidethroughScreen) -> str:
    """``sha256:`` of the canonical JSON of every field except ``screen_hash`` itself."""
    body = record.model_dump(mode="json", exclude={"screen_hash"})
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _refs(study: StudyInputs) -> list[EvidenceRef]:
    out = []
    for name in StudyInputs.model_fields:
        v = getattr(study, name)
        if isinstance(v, EvidencedQuantity):
            out.append(v.evidence)
        elif isinstance(v, EvidenceRef):
            out.append(v)
    return out


def screen_ramp_ridethrough(
    agreement: ConnectionAgreement,
    declaration: BessDeclaration,
    study_inputs: StudyInputs,
    *,
    evidence: Iterable[EvidenceObject] = (),
    series: NetImportSeries | None = None,
    as_of: date,
    record_class: RecordClass,
) -> RampRidethroughScreen:
    """Screen ramp, RVC and ride-through for one site and build the PSAL handoff bundle."""
    evidence = tuple(evidence)
    synthetic_run = record_class == "synthetic_fixture"
    site = declaration.site_id
    if synthetic_run and not site.startswith("SYNTHETIC-"):
        raise ValueError("a synthetic run carries a SYNTHETIC- site id")
    named = [("IC-1 agreement", agreement.site_id), ("study inputs", study_inputs.site_id)]
    if series is not None:
        named.append(("net-import series", series.site_id))
    for what, other in named:
        if other != site:
            raise ValueError(f"the {what} names site {other}; the declaration is for {site}")
    if not synthetic_run:
        if isinstance(agreement.source, SyntheticArchetype):
            raise EvidenceRefused(
                "the IC-1 agreement is a synthetic archetype; it is refused outside a synthetic run"
            )
        for what, rc in (
            ("BESS declaration", declaration.record_class),
            ("study inputs", study_inputs.record_class),
            *((("net-import series", series.record_class),) if series is not None else ()),
        ):
            if rc == "synthetic_fixture":
                raise EvidenceRefused(f"the {what} is a synthetic fixture in a registered run")
    if isinstance(agreement.source, EvidenceBacked):
        found = resolve_ref(agreement.source.evidence, evidence, include_synthetic=synthetic_run)
        if found.evidence_type not in _AGREEMENT_TYPES:
            raise EvidenceRefused(
                f"the IC-1 source is a {found.evidence_type.value}, not a connection agreement "
                "or offer"
            )
    for ref in _refs(study_inputs):
        resolve_ref(ref, evidence, include_synthetic=synthetic_run)
    series_hash: str | None = None
    if series is not None:
        if isinstance(series.source, EvidenceRef):
            resolve_ref(series.source, evidence, include_synthetic=synthetic_run)
        series_hash = content_hash(series)

    ramp = screen_ramp(agreement, declaration, series, series_hash=series_hash)
    rvc = screen_rvc(declaration, study_inputs)
    flag = screen_ridethrough(
        agreement,
        declaration,
        study_inputs,
        evidence,
        as_of=as_of,
        synthetic_run=synthetic_run,
    )
    bundle = build_bundle(
        agreement,
        declaration,
        study_inputs,
        flag,
        evidence=evidence,
        as_of=as_of,
        record_class=record_class,
    )
    confirmed = all(c == "confirmed" for c in CURRENCY.values())
    fields: dict[str, Any] = dict(
        site_id=site,
        record_class=record_class,
        as_of=as_of,
        caveat=CAVEAT_CONFIRMED if confirmed else CAVEAT_UNCONFIRMED,
        agreement_hash=agreement.content_hash(),
        agreement_source=agreement.source.kind,
        declaration_hash=content_hash(declaration),
        study_inputs_hash=content_hash(study_inputs),
        series_hash=series_hash
        if series_hash is not None
        else NotApplicable(kind="not_applicable", reason="no net-import series supplied"),
        ramp=ramp,
        rvc=rvc,
        ridethrough=flag,
        handoff=bundle,
        case_readiness=case_readiness(bundle),
        sources=_source_records(),
        person_only_asks=sorted_asks(
            PERSON_ONLY_ASKS,
            [a for a in bundle.requests if a.scope == "dossier"],
        ),
        data_requests=tuple(
            a
            for a in sorted_asks(ramp.requests, rvc.requests, flag.requests, bundle.requests)
            if a.scope != "dossier"
        ),
    )
    draft = RampRidethroughScreen.model_construct(**fields, screen_hash="sha256:" + "0" * 64)
    return RampRidethroughScreen(**fields, screen_hash=screen_hash_of(draft))


def render_summary(screen: RampRidethroughScreen) -> str:
    """A short human rendering. The first line is always the caveat."""
    lines = [screen.caveat, f"Site {screen.site_id} ({screen.record_class}), as of {screen.as_of}"]
    r = screen.ramp
    if isinstance(r, AgreedRamp):
        s = r.settings
        verdict = (
            f"PPC settings {s.towards_charge.verdict} / {s.towards_discharge.verdict}"
            if s.kind == "settings_check"
            else "PPC settings not declared"
        )
        lines.append(f"Ramp: agreed IC-1 terms; {verdict}; series {r.series.kind}")
    else:
        lines.append(f"Ramp: {r.kind}")
    for e in screen.rvc.events:
        if e.kind == "event_result":
            lines.append(
                f"RVC {e.event}: {e.outcome} (<= {e.estimate_pct_ceil.as_float:.4f} % at nominal "
                f"V0, limit {e.limit_pct.as_float:g} %)"
            )
        else:
            lines.append(f"RVC {e.event}: not computed ({e.reason})")
    f = screen.ridethrough
    lines.append(
        f"Ride-through: {f.state}; G99 type(s) {', '.join(f.candidate_types) or 'none'}; "
        f"delivered by {f.delivered_by}, studied by {f.studied_by}"
    )
    lines.append(
        f"PSAL handoff: catalogue {screen.handoff.catalogue_status} "
        f"({screen.handoff.catalogue_hash})"
    )
    for c in screen.case_readiness:
        lines.append(f"  {c.case_id}: {c.readiness.kind}")
    for a in screen.person_only_asks:
        lines.append(f"Person-only ask ({a.holder.value}): {a.request}")
    for a in screen.data_requests:
        lines.append(f"Data request ({a.holder.value}): {a.request}")
    return "\n".join(lines)
