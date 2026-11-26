"""Typed references from the chassis to other frozen contracts (IC-12 2.0.0, WP-CORE-03).

Two references, both by content hash, both reusing the reference types the contracts package
already froze rather than minting chassis copies of them:

- `ConnectionAgreementRef` (IC-1): the connection agreement a site's connection terms come from.
  It is the typed replacement for reading terms out of the free-text
  ``ConnectionDetails.anm_or_curtailment_notes``.
- `EvidenceLink`: an IC-4 `EvidenceRef` together with what the document supports.

**The growth rule that keeps every existing identity still.** Each field added at 2.0.0 is
optional, and while it is ``None`` it is *absent from every dump* of its model
(`omit_unset`, applied by a wrap serialiser on each model that grew). So a 1.0.0 instance
serialises to its 1.0.0 bytes through every route, not only through the quote's canonical input:
``input_hash``, the finance ``case_id``, the dispatch schedule reference and a persisted record
all stay byte-identical. A set value is dumped, and so enters every identity it should.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import ConfigDict, Field

from gridq.contracts.connection import CONTRACT_ID as _IC1
from gridq.contracts.connection import ConnectionAgreement
from gridq.contracts.curtailment import ConnectionAgreementRef
from gridq.contracts.evidence import EvidenceRef
from gridq.schemas.provenance import GridQModel

__all__ = [
    "ConnectionAgreementRef",
    "EvidenceLink",
    "EvidenceRef",
    "canonical_evidence_links",
    "connection_agreement_ref",
    "omit_unset",
    "resolve_connection_agreement",
]


def connection_agreement_ref(agreement: ConnectionAgreement) -> ConnectionAgreementRef:
    """The reference a chassis record carries for ``agreement``: its own version and hash."""
    return ConnectionAgreementRef(
        kind="connection_agreement_ref",
        contract=_IC1,
        contract_version=agreement.contract_version,
        agreement_hash=agreement.content_hash(),
    )


def resolve_connection_agreement(
    ref: ConnectionAgreementRef, agreements: Iterable[ConnectionAgreement]
) -> ConnectionAgreement:
    """The agreement ``ref`` points at, from ``agreements``; the place a consumer reads terms.

    Raises ``LookupError`` when no agreement has the referenced hash, and ``ValueError`` when the
    one that has it is not the version the reference states (the rule `evidence.resolve_ref`
    keeps for IC-4). The hash is the binding; the version is a claim checked here.
    """
    found = next((a for a in agreements if a.content_hash() == ref.agreement_hash), None)
    if found is None:
        raise LookupError(f"no connection agreement has content hash {ref.agreement_hash}")
    if found.contract_version != ref.contract_version:
        raise ValueError(
            f"the reference states contract_version {ref.contract_version} but the agreement "
            f"{ref.agreement_hash} is {found.contract_version}"
        )
    return found


class EvidenceLink(GridQModel):
    """One IC-4 evidence object, by hash, and what it supports on the record that carries it.

    ``supports`` names the thing evidenced: a chassis field path (``connection.mic_kw``), a
    revenue stream id, or a registered extension id. The reference says a document exists and
    which one; whether it is valid, third-party and in scope is decided where the object is in
    hand (`gridq.contracts.evidence.resolve_ref`, `assess`), never here.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    supports: str = Field(min_length=1, pattern=r"\S")
    evidence: EvidenceRef


def canonical_evidence_links(
    links: Sequence[EvidenceLink] | None,
) -> tuple[EvidenceLink, ...] | None:
    """Order links by (supports, hash), refuse an exact duplicate, and turn empty into ``None``.

    The same links in any input order then give one dump and one hash, and an empty list cannot
    shift an identity for no information (the rule `SiteModel.derived_assumptions` already keeps).
    """
    if not links:
        return None
    ordered = sorted(links, key=lambda link: (link.supports, link.evidence.evidence_hash))
    keys = [(link.supports, link.evidence.evidence_hash) for link in ordered]
    if len(keys) != len(set(keys)):
        raise ValueError("evidence_refs must not repeat a (supports, evidence hash) pair.")
    return tuple(ordered)


def omit_unset(dump: Any, names: Iterable[str]) -> Any:
    """Drop each of ``names`` from a model dump while its value is ``None``. See the module note."""
    if isinstance(dump, dict):
        for name in names:
            if name in dump and dump[name] is None:
                del dump[name]
    return dump
