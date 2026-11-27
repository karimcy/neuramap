"""Shared types for ``gridq.ramp_ridethrough``: citations, not-computed outcomes, asks, exact maths.

Every verdict in this package carries the clauses it was judged against (`Cite`), each with its
source's URL, retrieval hash and currency, so a reader can never see a bare "pass" (D203).
"""

from __future__ import annotations

from fractions import Fraction
from math import isqrt
from typing import Literal

from gridq.adequacy._common import ExactRational, ask
from gridq.contracts._base import ContractModel, NonEmptyStr
from gridq.contracts.evidence import EvidenceType
from gridq.ops.dossier_model import AskHolder, DossierAsk
from gridq.ramp_ridethrough.inputs import BessDeclaration, Declarant
from gridq.ramp_ridethrough.sources import CONSTANTS, CURRENCY, SOURCES, Currency, SourceId

__all__ = [
    "OEM_NAME",
    "PSAL_NAME",
    "Cite",
    "NotComputed",
    "cite",
    "cite_constant",
    "bess_ask",
    "ceil_sqrt",
    "declarant_ask",
    "exact",
    "fault_level_ask",
    "rational",
    "site_ask",
    "sorted_asks",
    "x_over_r_ask",
]

#: Named holders for asks that a partner closes.
PSAL_NAME = "PSAL"
OEM_NAME = "BESS OEM (PCS/PPC supplier)"


class Cite(ContractModel):
    """One clause a verdict rests on, with the document's identity and currency."""

    source_id: SourceId
    clause_id: NonEmptyStr
    url: NonEmptyStr
    sha256: NonEmptyStr
    currency: Currency


def cite(source_id: SourceId, clause_id: str) -> Cite:
    s = SOURCES[source_id]
    return Cite(
        source_id=source_id,
        clause_id=clause_id,
        url=s.url,
        sha256=s.sha256,
        currency=CURRENCY[source_id],
    )


def cite_constant(key: str) -> Cite:
    c = CONSTANTS[key]
    return cite(c.source_id, c.clause_id)


class NotComputed(ContractModel):
    """A screen part that could not be computed, with the typed asks that would let it run."""

    kind: Literal["not_computed"]
    reason: NonEmptyStr
    requests: tuple[DossierAsk, ...]


def exact(value: float) -> Fraction:
    """An IC-1 float as the exact decimal it was written as (``5.0`` -> 5, ``0.1`` -> 1/10)."""
    return Fraction(repr(value))


def rational(value: Fraction | int) -> ExactRational:
    return ExactRational.of(value)


def ceil_sqrt(value: Fraction, *, places: int = 4) -> Fraction:
    """sqrt(value) rounded **up** to ``places`` decimals, exactly (integer square root)."""
    if value < 0:
        raise ValueError("no real square root of a negative number")
    scale = 10**places
    n = value * scale * scale
    s = isqrt(n.numerator // n.denominator)
    while Fraction(s * s) < n:
        s += 1
    return Fraction(s, scale)


def site_ask(
    request: str,
    *,
    site_id: str,
    holder: AskHolder,
    closes_with: tuple[EvidenceType, ...] = (),
    holder_name: str | None = None,
) -> DossierAsk:
    if holder_name is None:
        return ask(request, site_id=site_id, holder=holder, closes_with=closes_with)
    return DossierAsk.make(
        request=request,
        holder=holder,
        holder_name=holder_name,
        scope=f"site:{site_id}",
        closes_with=closes_with,
    )


def declarant_ask(request: str, *, site_id: str, declarant: Declarant) -> DossierAsk:
    """An ask to whoever declared the BESS design (an OEM declarant is a partner)."""
    if declarant.role == "oem":
        return site_ask(
            request, site_id=site_id, holder=AskHolder.PARTNER, holder_name=declarant.name
        )
    holder = AskHolder.HOST if declarant.role == "host" else AskHolder.CUSTOMER
    return site_ask(request, site_id=site_id, holder=holder)


def sorted_asks(*groups: tuple[DossierAsk, ...] | list[DossierAsk]) -> tuple[DossierAsk, ...]:
    """Every ask once, ordered by id (deterministic)."""
    seen: dict[str, DossierAsk] = {}
    for g in groups:
        for a in g:
            seen.setdefault(a.ask_id, a)
    return tuple(seen[k] for k in sorted(seen))


#: One wording per declared BESS value, so every screen raises the same ask (one ask id).
BESS_FIELD_WORDS = {
    "registered_capacity_w": (
        "Registered Capacity (G99 Section 4, based on the inverter rating), in W"
    ),
    "rated_import_power_w": "Rated Import Power (G99 Section 4), in W",
    "reactive_range": "reactive capability at Registered Capacity (absorb and inject), in var",
}


def bess_ask(bess: BessDeclaration, field: str) -> DossierAsk:
    """The ask for one undeclared BESS value, to whoever declared the design."""
    return declarant_ask(
        f"Declare the BESS {BESS_FIELD_WORDS[field]}",
        site_id=bess.site_id,
        declarant=bess.declared_by,
    )


def fault_level_ask(site_id: str) -> DossierAsk:
    """Site electrical data is the network operator's to state, never a stand-in."""
    return site_ask(
        "State the minimum short-circuit power (MVA) at the PCC under normal operating conditions "
        "(P28 6.4.1(a); G99 B.4.4.3 and C.7.5.3), not the Long Term Development Statement maximum",
        site_id=site_id,
        holder=AskHolder.NETWORK_OPERATOR,
        closes_with=(EvidenceType.OPERATIONAL_DATA_RECORD, EvidenceType.TECHNICAL_REPORT),
    )


def x_over_r_ask(site_id: str) -> DossierAsk:
    return site_ask(
        "State the X/R ratio at the PCC for the minimum short-circuit power (P28 6.1.5)",
        site_id=site_id,
        holder=AskHolder.NETWORK_OPERATOR,
        closes_with=(EvidenceType.OPERATIONAL_DATA_RECORD, EvidenceType.TECHNICAL_REPORT),
    )
