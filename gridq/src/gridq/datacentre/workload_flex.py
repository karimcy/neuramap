"""The load model's optional declared input: an IC-9 compute turn-down declaration (WP-SITE-07).

Decision D126 (Fable 5.2), as built here. A GPU operator may declare turn-down envelopes
(`gridq.contracts.compute_turndown`); nothing requires one, and nothing assumes one (A2-04). The
synthesiser takes it as ``workload_flex=``, whose default is the sentinel `NO_ENVELOPE_DECLARED`
(never ``None``: a missing declaration is a stated fact, D45).

**IT load is untouched in every path.** The declaration never reaches the engine, `evaluate`, the
manifest, the flags, the warnings or `DcLoadInputs`: `synthesise` builds the ensemble exactly as it
did before IC-9 existed and then *binds* the declaration beside it (`WorkloadFlexBinding`). The
series hash, members, bands, aggregates and one-minute windows are therefore identical with no
declaration, with the sentinel passed explicitly, and with a declaration
(``tests/test_datacentre_ic9_untouched.py``). What a declaration could deliver is computed only on
request, by `gridq.datacentre.turndown`, and always labelled conditional.

A binding records which declaration was bound (its content hash and contract version), how many of
the horizon's naive-local half-hours it is in force and offers something at (read with
`declared_at_local`, the contract's one naive-local reader), and a ``binding_hash`` over the series
hash, the declaration hash and the contract version. A declaration for another site is refused; a
declaration that does not overlap the horizon is bound and says so (it is not an error to declare
for a later period).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final, Literal

from gridq.contracts.compute_turndown import (
    ComputeTurndownDeclaration,
    NotInForce,
    Offered,
    declared_at_local,
)
from gridq.datacentre.inputs import HALF_HOURS_PER_DAY, MINUTES_PER_DAY
from gridq.provenance.hashing import hash_json

__all__ = [
    "NO_ENVELOPE_DECLARED",
    "NoEnvelopeDeclared",
    "WorkloadFlex",
    "WorkloadFlexBinding",
    "bind",
]


class NoEnvelopeDeclared(Enum):
    """The operator declared no turn-down envelope: IT load is untouched (the default)."""

    NO_ENVELOPE_DECLARED = "no_envelope_declared"

    def __repr__(self) -> str:
        return "NO_ENVELOPE_DECLARED"


#: The frozen singleton default of every ``workload_flex=`` keyword. Compared with ``is``.
NO_ENVELOPE_DECLARED: Final = NoEnvelopeDeclared.NO_ENVELOPE_DECLARED

WorkloadFlex = ComputeTurndownDeclaration | Literal[NoEnvelopeDeclared.NO_ENVELOPE_DECLARED]


@dataclass(frozen=True)
class WorkloadFlexBinding:
    """A declaration bound to an ensemble it did not change."""

    declaration: ComputeTurndownDeclaration
    declaration_hash: str
    contract_version: str
    #: Naive-local half-hours of the horizon at which the declaration is in force.
    intervals_in_force: int
    #: Of those, the half-hours at which at least one envelope is offered.
    intervals_offered: int
    notes: tuple[str, ...]
    #: ``sha256`` of the series hash, the declaration hash and the contract version.
    binding_hash: str


def bind(
    declaration: ComputeTurndownDeclaration,
    *,
    site_id: str,
    timestamps: tuple[datetime, ...],
    series_hash: str,
) -> WorkloadFlexBinding:
    """Bind ``declaration`` to an ensemble of ``site_id`` over ``timestamps`` (naive local
    half-hour starts). Reads the declaration; touches nothing of the ensemble."""
    if type(declaration) is not ComputeTurndownDeclaration:
        raise TypeError(
            "workload_flex must be an IC-9 ComputeTurndownDeclaration or NO_ENVELOPE_DECLARED, "
            f"not {type(declaration).__name__}"
        )
    if declaration.site_id != site_id:
        raise ValueError(
            f"the IC-9 declaration is for site {declaration.site_id!r}, not {site_id!r}"
        )
    in_force = offered = 0
    for ts in timestamps:
        half_hour = (ts.hour * 60 + ts.minute) // (MINUTES_PER_DAY // HALF_HOURS_PER_DAY)
        answer = declared_at_local(declaration, ts.date(), half_hour)
        if isinstance(answer, NotInForce):
            continue
        in_force += 1
        offered += isinstance(answer.state, Offered)
    notes = [
        "IC-9 turn-down declared by the GPU operator and bound beside the ensemble: the IT load, "
        "facility series, bands and hashes are those of the undeclared model.",
        "Any turn-down is CONDITIONAL (B8-03): never firm, never in the priced floor.",
    ]
    if declaration.record_class == "synthetic_fixture":
        notes.append("The declaration is a SYNTHETIC schema-test fixture, not an operator's.")
    if in_force == 0:
        notes.append("The declaration is not in force at any half-hour of the horizon.")
    declaration_hash = declaration.content_hash()
    binding_hash = hash_json(
        {
            "series_hash": series_hash,
            "declaration_hash": declaration_hash,
            "contract_version": declaration.contract_version,
        }
    )
    return WorkloadFlexBinding(
        declaration=declaration,
        declaration_hash=declaration_hash,
        contract_version=declaration.contract_version,
        intervals_in_force=in_force,
        intervals_offered=offered,
        notes=tuple(notes),
        binding_hash=binding_hash,
    )
