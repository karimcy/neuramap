"""The ride-through flag: a cited obligation ledger, never a verdict (spec A3-04; D-NEW-SITE06-3).

**Who delivers it is fixed by the type system.** `RideThroughFlag` carries
``delivered_by="oem_pcs_ppc"``, ``studied_by="psal_rms_emt"`` and ``neura_role="screen_only"``;
no other values exist, so a record saying Neura delivers fault ride-through or fast fault current
cannot be built. Neura does not write protection-grade controls.

**The G99 type is a candidate set.** A BESS on inverters is a Power Park Module, and G99 includes
Electricity Storage in its Power Generating Modules. Type follows Registered Capacity and
Connection Point voltage (G99 Section 4). An unknown input widens the set, never defaults it; each
unknown is a typed ask, and the obligations attached are the union over the set, each tagged with
its type.

**Per type** (G99 Issue 2):

- A: fault ride-through only where specifically agreed with the DNO for network security
  (11.3.1): ``conditional_on_dno_agreement``.
- B: 12.3 (Table 12.2, Power Park Modules), fast fault current 12.6, study cases B.4.4.
- C, and D below 110 kV: 13.3 (Table 13.5), fast fault current 13.6, study cases C.7.5.
- D at or above 110 kV: 13.3 (Table 13.6), 13.6, C.7.5.

The parameters are transcribed as cited constants (`gridq.ramp_ridethrough.sources`), labelled
``requirement_cited``: they are what the plant must meet, never a statement of what it does.

**States.** ``indeterminate`` (more than one candidate type); ``conditional_on_dno_agreement``
(Type A); ``required_open`` (B to D, evidence missing); ``required_evidenced`` (both legs held: an
OEM, independent engineer or PSAL study or certificate, and a DNO G99 approval or commissioning
record). There is no "pass": the terminal state records that third parties attest.

**Grid Code.** ECC.6.3.15 and ECC.6.3.16 are cited when the facility reaches the Grid Code "Large"
threshold for its region (G99 6.1.3.1). Below the lowest threshold they do not apply; at or above
the highest they apply; in between the region decides, and it is never inferred from the network
operator.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from fractions import Fraction
from typing import Final, Literal

from gridq.adequacy._common import ExactRational
from gridq.contracts._base import ContractModel, NonEmptyStr, NotApplicable, Sha256, Unknown
from gridq.contracts.connection import ConnectionAgreement, PccTerms
from gridq.contracts.evidence import (
    EvidenceObject,
    EvidenceRef,
    EvidenceRefused,
    EvidenceStatus,
    EvidenceType,
    IssuerRole,
    SiteScope,
    VerificationMethod,
    Verified,
    require_third_party,
    resolve_ref,
)
from gridq.ops.dossier_model import AskHolder, DossierAsk
from gridq.ramp_ridethrough._common import (
    PSAL_NAME,
    Cite,
    bess_ask,
    cite,
    cite_constant,
    declarant_ask,
    exact,
    rational,
    site_ask,
    sorted_asks,
)
from gridq.ramp_ridethrough.inputs import BessDeclaration, StudyInputs
from gridq.ramp_ridethrough.sources import CONSTANTS, SourceId

__all__ = [
    "CitedParameter",
    "GridCodeApplicability",
    "RideThroughFlag",
    "TypeObligation",
    "candidate_types",
    "cited_parameter",
    "pcc_ask",
    "screen_ridethrough",
]

G99Type = Literal["A", "B", "C", "D"]
FlagState = Literal[
    "indeterminate",
    "conditional_on_dno_agreement",
    "required_open",
    "required_evidenced",
    "not_in_g99_scope",
]

_STUDY_TYPES = frozenset({EvidenceType.TECHNICAL_REPORT, EvidenceType.CONFORMITY_CERTIFICATE})
_STUDY_ISSUERS = frozenset(
    {IssuerRole.OEM, IssuerRole.INDEPENDENT_ENGINEER, IssuerRole.RESEARCH_PARTNER}
)
_DNO_TYPES = frozenset({EvidenceType.G99_APPROVAL, EvidenceType.COMMISSIONING_RECORD})
_DNO_ISSUERS = frozenset({IssuerRole.DNO, IssuerRole.IDNO})


class CitedParameter(ContractModel):
    """A requirement transcribed from a standard: what the plant must meet."""

    key: NonEmptyStr
    value: ExactRational
    unit: NonEmptyStr
    label: Literal["requirement_cited"]
    cite: Cite


def cited_parameter(key: str) -> CitedParameter:
    c = CONSTANTS[key]
    return CitedParameter(
        key=key,
        value=rational(c.value),
        unit=c.unit,
        label="requirement_cited",
        cite=cite_constant(key),
    )


def _table(prefix: str) -> tuple[CitedParameter, ...]:
    return tuple(cited_parameter(k) for k in sorted(CONSTANTS) if k.startswith(prefix + "."))


class TypeObligation(ContractModel):
    g99_type: G99Type
    frt: Literal["required", "conditional_on_dno_agreement"]
    fast_fault_current: Literal["required", "not_required"]
    voltage_against_time: tuple[CitedParameter, ...] | Unknown | NotApplicable
    study_retained_voltage: CitedParameter | Unknown | NotApplicable
    recovery: tuple[CitedParameter, ...] | NotApplicable
    clauses: tuple[Cite, ...]


class GridCodeApplicability(ContractModel):
    status: Literal["applies", "not_applicable", "indeterminate"]
    reason: NonEmptyStr
    clauses: tuple[Cite, ...]


class EvidenceLeg(ContractModel):
    leg: Literal["study", "network_operator"]
    held: tuple[Sha256, ...]
    satisfied: bool


class RideThroughFlag(ContractModel):
    kind: Literal["ridethrough_flag"]
    delivered_by: Literal["oem_pcs_ppc"]
    studied_by: Literal["psal_rms_emt"]
    neura_role: Literal["screen_only"]
    statement: Literal[
        "G99 fault ride-through and fast fault current must be delivered by the OEM PCS/PPC and "
        "demonstrated by RMS/EMT study; Neura does not write protection-grade controls."
    ]
    state: FlagState
    candidate_types: tuple[G99Type, ...]
    registered_capacity_w: int | Unknown
    connection_kv: ExactRational | Unknown
    obligations: tuple[TypeObligation, ...]
    grid_code: GridCodeApplicability
    evidence: tuple[EvidenceLeg, ...]
    basis: tuple[Cite, ...]
    requests: tuple[DossierAsk, ...]


STATEMENT: Final = (
    "G99 fault ride-through and fast fault current must be delivered by the OEM PCS/PPC and "
    "demonstrated by RMS/EMT study; Neura does not write protection-grade controls."
)


def _type_of_capacity(cap: int) -> G99Type | None:
    if cap >= CONSTANTS["g99.type_d.min_capacity_w"].value:
        return "D"
    if cap >= CONSTANTS["g99.type_c.min_capacity_w"].value:
        return "C"
    if cap >= CONSTANTS["g99.type_b.min_capacity_w"].value:
        return "B"
    if cap >= CONSTANTS["g99.type_a.min_capacity_w"].value:
        return "A"
    return None


def candidate_types(cap: int | Unknown, kv: Fraction | Unknown) -> tuple[G99Type, ...]:
    """Every G99 type consistent with what is known (Section 4). Empty: below Type A."""
    d_kv = CONSTANTS["g99.type_d.min_connection_kv"].value
    if isinstance(kv, Fraction) and kv >= d_kv:
        return ("D",)
    if isinstance(cap, int):
        t = _type_of_capacity(cap)
        if isinstance(kv, Fraction):
            return (t,) if t else ()
        return tuple(sorted({t, "D"} if t else {"D"}))  # type: ignore[arg-type]
    return ("A", "B", "C", "D")


def _obligation(t: G99Type, kv: Fraction | Unknown) -> TypeObligation:
    g99: SourceId = "G99_ISSUE_2"
    recovery = (
        cited_parameter("g99.frt.recovery_fraction"),
        cited_parameter("g99.frt.recovery_within_s"),
    )
    if t == "A":
        return TypeObligation(
            g99_type="A",
            frt="conditional_on_dno_agreement",
            fast_fault_current="not_required",
            voltage_against_time=NotApplicable(
                kind="not_applicable", reason="Type A: the DNO advises case by case (11.3.1)"
            ),
            study_retained_voltage=NotApplicable(
                kind="not_applicable", reason="no Type A simulation study is set by G99 Annex B.4"
            ),
            recovery=NotApplicable(kind="not_applicable", reason="Type A"),
            clauses=(cite(g99, "11.3.1"),),
        )
    if t == "B":
        return TypeObligation(
            g99_type="B",
            frt="required",
            fast_fault_current="required",
            voltage_against_time=_table("g99.t12_2"),
            study_retained_voltage=cited_parameter("g99.b4_1.ppm_retained_voltage"),
            recovery=recovery,
            clauses=(
                cite(g99, "12.3.1.1-12.3.1.7"),
                cite(g99, "12.3.4"),
                cite(g99, "12.6"),
                cite(g99, "B.4.4"),
            ),
        )
    d_kv = CONSTANTS["g99.type_d.min_connection_kv"].value
    vat: tuple[CitedParameter, ...] | Unknown
    retained: CitedParameter | Unknown
    if isinstance(kv, Unknown):
        vat = Unknown(kind="unknown", reason="Table 13.5 or 13.6 depends on the PCC voltage")
        retained = Unknown(kind="unknown", reason="Table C.7.1 row depends on the PCC voltage")
    elif kv < d_kv:
        vat = _table("g99.t13_5")
        retained = cited_parameter("g99.c7_1.ppm_below_110kv_retained_voltage")
    else:
        vat = _table("g99.t13_6")
        retained = (
            cited_parameter("g99.c7_1.ppm_d_above_110kv_retained_voltage")
            if kv > d_kv
            else Unknown(
                kind="unknown",
                reason="Table C.7.1 has rows for below and above 110 kV; exactly 110 kV is in "
                "neither, so the DNO must state the retained voltage",
            )
        )
    return TypeObligation(
        g99_type=t,
        frt="required",
        fast_fault_current="required",
        voltage_against_time=vat,
        study_retained_voltage=retained,
        recovery=recovery,
        clauses=(
            cite(g99, "13.3.1.1-13.3.1.11"),
            cite(g99, "13.3.4"),
            cite(g99, "13.6"),
            cite(g99, "C.7.5"),
        ),
    )


def _grid_code(bess: BessDeclaration) -> tuple[GridCodeApplicability, list[DossierAsk]]:
    k = {
        "north_scotland": "g99.grid_code_large.north_scotland_w",
        "south_scotland": "g99.grid_code_large.south_scotland_w",
        "england_wales": "g99.grid_code_large.england_wales_w",
    }
    lo = min(CONSTANTS[v].value for v in k.values())
    hi = max(CONSTANTS[v].value for v in k.values())
    thresholds = tuple(cite_constant(v) for v in k.values())
    ecc = (
        cite("GRID_CODE_I6R33", "ECC.6.3.15"),
        cite("GRID_CODE_I6R33", "ECC.6.3.16"),
    )
    fac = bess.facility_registered_capacity_w
    asks: list[DossierAsk] = []
    if isinstance(fac, Unknown):
        asks.append(
            declarant_ask(
                "Declare the Registered Capacity of every Power Generating Module at the facility "
                "(G99 6.1.3.1), in W",
                site_id=bess.site_id,
                declarant=bess.declared_by,
            )
        )
        return GridCodeApplicability(
            status="indeterminate", reason="facility capacity unknown", clauses=thresholds
        ), asks
    if fac < lo:
        return GridCodeApplicability(
            status="not_applicable",
            reason="below the lowest Grid Code 'Large' threshold in any GB region",
            clauses=thresholds,
        ), asks
    if fac >= hi:
        return GridCodeApplicability(
            status="applies",
            reason="at or above the Grid Code 'Large' threshold in every GB region",
            clauses=thresholds + ecc,
        ), asks
    region = bess.gb_region
    if isinstance(region, Unknown):
        asks.append(
            declarant_ask(
                "State the GB region of the site (England and Wales, south Scotland or north "
                "Scotland): the Grid Code 'Large' threshold depends on it",
                site_id=bess.site_id,
                declarant=bess.declared_by,
            )
        )
        return GridCodeApplicability(
            status="indeterminate",
            reason="between regional 'Large' thresholds and the region is unknown",
            clauses=thresholds,
        ), asks
    key = k[region]
    if fac >= CONSTANTS[key].value:
        return GridCodeApplicability(
            status="applies",
            reason=f"at or above the 'Large' threshold for {region}",
            clauses=(cite_constant(key), *ecc),
        ), asks
    return GridCodeApplicability(
        status="not_applicable",
        reason=f"below the 'Large' threshold for {region}",
        clauses=(cite_constant(key),),
    ), asks


def _admissible(obj: EvidenceObject, site_id: str, as_of: date, synthetic_run: bool) -> bool:
    require_third_party(obj)  # a Neura self-issued record raises: never downgraded
    if not isinstance(obj.scope, SiteScope) or obj.scope.site_id != site_id:
        return False
    if obj.record_class == "synthetic_fixture" and not synthetic_run:
        return False
    if obj.status_at(as_of) is not EvidenceStatus.VALID:
        return False
    v = obj.verification
    return (
        isinstance(v, Verified)
        and v.method is VerificationMethod.CONFIRMED_WITH_ISSUER
        and v.verified_on <= as_of
    )


def _leg(
    leg: Literal["study", "network_operator"],
    ref: EvidenceRef | Unknown,
    evidence: tuple[EvidenceObject, ...],
    site_id: str,
    as_of: date,
    synthetic_run: bool,
) -> EvidenceLeg:
    """One leg, from the evidence the study inputs bind to it by reference (never by search).

    A reference to the wrong kind of document (type or issuer outside the leg's closed sets) is
    refused, not ignored; a right-kind document that is out of date, unverified or for another
    site leaves the leg open.
    """
    if isinstance(ref, Unknown):
        return EvidenceLeg(leg=leg, held=(), satisfied=False)
    obj = resolve_ref(ref, evidence, include_synthetic=synthetic_run)
    types, issuers = (
        (_STUDY_TYPES, _STUDY_ISSUERS) if leg == "study" else (_DNO_TYPES, _DNO_ISSUERS)
    )
    if obj.evidence_type not in types or obj.issuer.role not in issuers:
        raise EvidenceRefused(
            f"the {leg} leg of the ride-through flag takes a "
            f"{' or '.join(sorted(t.value for t in types))} issued by "
            f"{' or '.join(sorted(str(r) for r in issuers))}, not a {obj.evidence_type.value} "
            f"issued by {obj.issuer.role}"
        )
    ok = _admissible(obj, site_id, as_of, synthetic_run)
    return EvidenceLeg(leg=leg, held=(obj.content_hash(),) if ok else (), satisfied=ok)


def pcc_ask(agreement: ConnectionAgreement) -> DossierAsk:
    """The ask for unknown IC-1 PCC terms (G99 type and every study case depend on them)."""
    return site_ask(
        f"Provide the PCC voltage (kV) of connection agreement {agreement.agreement_ref}",
        site_id=agreement.site_id,
        holder=AskHolder.CUSTOMER,
        closes_with=(EvidenceType.CONNECTION_AGREEMENT, EvidenceType.CONNECTION_OFFER),
    )


def screen_ridethrough(
    agreement: ConnectionAgreement,
    bess: BessDeclaration,
    study_inputs: StudyInputs,
    evidence: Iterable[EvidenceObject],
    *,
    as_of: date,
    synthetic_run: bool,
) -> RideThroughFlag:
    site = bess.site_id
    evidence = tuple(evidence)
    asks: list[DossierAsk] = []
    cap = bess.registered_capacity_w
    if isinstance(cap, Unknown):
        asks.append(bess_ask(bess, "registered_capacity_w"))
    kv: Fraction | Unknown
    if isinstance(agreement.pcc, PccTerms):
        kv = exact(agreement.pcc.voltage_kv)
    else:
        kv = Unknown(kind="unknown", reason=f"IC-1 PCC terms unknown: {agreement.pcc.reason}")
        asks.append(pcc_ask(agreement))
    types = candidate_types(cap, kv)
    grid_code, gc_asks = _grid_code(bess)
    asks += gc_asks
    obligations = tuple(_obligation(t, kv) for t in types)
    for o in obligations:
        if isinstance(o.study_retained_voltage, Unknown) and not isinstance(kv, Unknown):
            asks.append(
                site_ask(
                    "State the retained voltage for the fault ride-through study of a Type D "
                    "Power Park Module connected at exactly 110 kV (G99 Table C.7.1)",
                    site_id=site,
                    holder=AskHolder.NETWORK_OPERATOR,
                )
            )
    study = _leg("study", study_inputs.frt_study_report, evidence, site, as_of, synthetic_run)
    dno = _leg(
        "network_operator", study_inputs.dno_frt_acceptance, evidence, site, as_of, synthetic_run
    )
    state: FlagState
    if not types:
        state = "not_in_g99_scope"
    elif len(types) > 1:
        state = "indeterminate"
    elif types == ("A",):
        state = "conditional_on_dno_agreement"
        asks.append(
            site_ask(
                "State whether fault ride-through has been specifically agreed for this Type A "
                "BESS for network security (G99 11.3.1), with the protection clearance times",
                site_id=site,
                holder=AskHolder.NETWORK_OPERATOR,
                closes_with=(EvidenceType.CONNECTION_AGREEMENT,),
            )
        )
    elif study.satisfied and dno.satisfied:
        state = "required_evidenced"
    else:
        state = "required_open"
        if not study.satisfied:
            asks.append(
                site_ask(
                    "Deliver the fault ride-through and fast fault current simulation study of "
                    "the BESS (G99 B.4.4 or C.7.5, RMS and EMT) as a report",
                    site_id=site,
                    holder=AskHolder.PARTNER,
                    holder_name=PSAL_NAME,
                    closes_with=(EvidenceType.TECHNICAL_REPORT,),
                )
            )
        if not dno.satisfied:
            asks.append(
                site_ask(
                    "Issue the G99 approval (or the commissioning record) covering fault "
                    "ride-through of the BESS",
                    site_id=site,
                    holder=AskHolder.NETWORK_OPERATOR,
                    closes_with=(EvidenceType.G99_APPROVAL, EvidenceType.COMMISSIONING_RECORD),
                )
            )
    return RideThroughFlag(
        kind="ridethrough_flag",
        delivered_by="oem_pcs_ppc",
        studied_by="psal_rms_emt",
        neura_role="screen_only",
        statement=STATEMENT,
        state=state,
        candidate_types=types,
        registered_capacity_w=cap,
        connection_kv=rational(kv) if isinstance(kv, Fraction) else kv,
        obligations=obligations,
        grid_code=grid_code,
        evidence=(study, dno),
        basis=(
            cite("G99_ISSUE_2", "4 (Type A-D; Power Park Module; Electricity Storage)"),
            cite("G99_ISSUE_2", "Introduction: Electricity Storage is a Power Generating Module"),
        ),
        requests=sorted_asks(asks),
    )
